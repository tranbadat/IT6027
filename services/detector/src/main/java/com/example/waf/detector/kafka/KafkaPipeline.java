package com.example.waf.detector.kafka;

import com.example.waf.detector.config.DetectorConfig;
import com.example.waf.detector.enums.Role;
import com.example.waf.detector.service.RuleEngineService;
import com.example.waf.detector.service.impl.DdosDetectionServiceImpl;
import com.example.waf.detector.service.impl.HttpScopeService;
import com.example.waf.detector.service.impl.RequestDetectionServiceImpl;
import com.example.waf.detector.service.impl.RuleEngineServiceImpl;
import com.example.waf.detector.util.EnvironmentUtils;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.errors.WakeupException;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;

import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * Vòng lặp Kafka cho các role DETECTION, REPARTITION, DDOS. Logic từng record nằm ở
 * {@link RecordProcessor}; lớp này chỉ poll, gửi output và commit offset SAU KHI broker xác
 * nhận toàn bộ output của batch (at-least-once).
 */
public final class KafkaPipeline {
    private final Role role;
    private final String group;
    private final String inputTopic;
    private final KafkaConsumer<String, String> consumer;
    private final KafkaProducer<String, String> producer;
    private final RecordProcessor processor;
    private final RuleChangeSubscriber ruleChangeSubscriber;

    public KafkaPipeline(Role role) {
        this.role = role;
        Clock clock = Clock.systemUTC();
        DetectorConfig config = DetectorConfig.fromEnvironment();
        Topics topics = Topics.fromEnvironment();

        RuleEngineService ruleEngine = new RuleEngineServiceImpl();
        this.ruleChangeSubscriber = role == Role.DETECTION ? new RuleChangeSubscriber(ruleEngine) : null;
        this.processor = new RecordProcessor(role, topics,
                new RequestDetectionServiceImpl(ruleEngine, config.request(), clock, config.maxFutureSkewSeconds()),
                new DdosDetectionServiceImpl(config.ddos(), clock, config.maxFutureSkewSeconds()),
                HttpScopeService.forBaseUrl(EnvironmentUtils.required("SCOPE_SERVICE_URL"), clock),
                clock, config.maxFutureSkewSeconds(), config.request().alertThreshold());

        String bootstrap = EnvironmentUtils.required("KAFKA_BOOTSTRAP_SERVERS");
        // DDOS đọc topic đã phân vùng theo domain; DETECTION và REPARTITION đọc log.enriched.
        this.inputTopic = role == Role.DDOS ? topics.domainInput() : topics.input();
        this.group = switch (role) {
            case DETECTION -> EnvironmentUtils.value("KAFKA_CONSUMER_GROUP", "detector");
            case REPARTITION -> EnvironmentUtils.value("KAFKA_REPARTITION_GROUP", "detector-repartition");
            case DDOS -> EnvironmentUtils.value("KAFKA_DDOS_GROUP", "detector-ddos");
            case RULE_MANAGEMENT -> throw new IllegalArgumentException("RULE_MANAGEMENT does not use KafkaPipeline");
        };

        Properties consumerProperties = new Properties();
        consumerProperties.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap);
        consumerProperties.put(ConsumerConfig.GROUP_ID_CONFIG, group);
        consumerProperties.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        consumerProperties.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        // Commit thủ công sau khi output đã được broker xác nhận (at-least-once).
        consumerProperties.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, false);
        consumerProperties.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        consumerProperties.put(ConsumerConfig.MAX_POLL_RECORDS_CONFIG,
                EnvironmentUtils.positiveInt("BATCH_SIZE", role == Role.DETECTION ? 200 : 500));
        consumerProperties.put(ConsumerConfig.MAX_POLL_INTERVAL_MS_CONFIG, 300_000);
        consumer = new KafkaConsumer<>(consumerProperties);

        Properties producerProperties = new Properties();
        producerProperties.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap);
        producerProperties.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        producerProperties.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        producerProperties.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
        producerProperties.put(ProducerConfig.ACKS_CONFIG, "all");
        producerProperties.put(ProducerConfig.LINGER_MS_CONFIG, 10);
        producerProperties.put(ProducerConfig.COMPRESSION_TYPE_CONFIG, "lz4");
        producer = new KafkaProducer<>(producerProperties);
    }

    private static final Duration RULE_SNAPSHOT_WAIT = Duration.ofSeconds(30);

    public void run() {
        if (ruleChangeSubscriber != null) {
            ruleChangeSubscriber.start();
            try {
                if (!ruleChangeSubscriber.awaitCaughtUp(RULE_SNAPSHOT_WAIT))
                    System.err.println("rule_snapshot_wait_timeout: dùng rule mặc định cho tới khi nhận snapshot");
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return;
            }
        }
        Runtime.getRuntime().addShutdownHook(new Thread(consumer::wakeup));
        consumer.subscribe(List.of(inputTopic));
        long processed = 0;
        System.out.printf("role=%s group=%s input=%s%n", role, group, inputTopic);
        try {
            while (true) {
                ConsumerRecords<String, String> batch = consumer.poll(Duration.ofMillis(100));
                if (batch.isEmpty()) continue;

                List<Future<RecordMetadata>> deliveries = new ArrayList<>();
                Map<TopicPartition, OffsetAndMetadata> offsets = new HashMap<>();
                for (ConsumerRecord<String, String> record : batch) {
                    for (OutputRecord output : processor.process(record.key(), record.value()))
                        deliveries.add(producer.send(toProducerRecord(output, record)));
                    offsets.put(new TopicPartition(record.topic(), record.partition()),
                            new OffsetAndMetadata(record.offset() + 1));
                }
                for (Future<RecordMetadata> delivery : deliveries)
                    delivery.get(30, TimeUnit.SECONDS);
                consumer.commitSync(offsets);

                processed += batch.count();
                if (processed % 10_000 < batch.count())
                    System.out.printf("role=%s processed=%d%n", role, processed);
            }
        } catch (WakeupException stopped) {
            System.out.println("Kafka worker stopping");
        } catch (Exception failure) {
            // Lỗi gửi/commit: để process chết, khởi động lại sẽ đọc lại batch chưa commit.
            throw new RuntimeException("Kafka output/commit failed; restart and replay uncommitted batch", failure);
        } finally {
            consumer.close();
            producer.close(Duration.ofSeconds(5));
            if (ruleChangeSubscriber != null) ruleChangeSubscriber.close();
        }
    }

    private ProducerRecord<String, String> toProducerRecord(OutputRecord output,
                                                            ConsumerRecord<String, String> source) {
        ProducerRecord<String, String> record = new ProducerRecord<>(output.topic(), output.key(), output.value());
        if (output.headers().isEmpty()) return record;
        // Dead-letter: giữ lý do lỗi và vị trí gốc để truy vết, cùng quy ước header với bus Python.
        output.headers().forEach((name, value) -> record.headers().add(name, bytes(value)));
        record.headers().add("source_topic", bytes(source.topic()));
        record.headers().add("source_partition", bytes(String.valueOf(source.partition())));
        record.headers().add("source_offset", bytes(String.valueOf(source.offset())));
        record.headers().add("consumer_group", bytes(group));
        return record;
    }

    private static byte[] bytes(String value) {
        return value.getBytes(StandardCharsets.UTF_8);
    }
}
