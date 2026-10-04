package com.example.waf.detector.kafka;

import com.example.waf.detector.config.DetectorConfig;
import com.example.waf.detector.model.DetectionResult;
import com.example.waf.detector.model.Event;
import com.example.waf.detector.service.DdosDetectionService;
import com.example.waf.detector.service.RequestDetectionService;
import com.example.waf.detector.service.RuleEngineService;
import com.example.waf.detector.service.impl.DdosDetectionServiceImpl;
import com.example.waf.detector.service.impl.RequestDetectionServiceImpl;
import com.example.waf.detector.service.impl.RuleEngineServiceImpl;
import com.example.waf.detector.util.EnvironmentUtils;
import com.example.waf.detector.util.EventMapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
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

import java.time.Clock;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

public final class KafkaPipeline {
    public enum Role { RULES, REPARTITION, DDOS }

    private final Role role;
    private final String inputTopic;
    private final String outputTopic;
    private final String dlqTopic;
    private final boolean emitOnlyAlerts;
    private final KafkaConsumer<String, String> consumer;
    private final KafkaProducer<String, String> producer;
    private final ObjectMapper mapper = new ObjectMapper();
    private final RequestDetectionService requestDetector;
    private final DdosDetectionService ddosDetector;
    private final Clock clock;
    private final int maxFutureSkewSeconds;

    public KafkaPipeline(Role role) {
        this.role = role;
        this.clock = Clock.systemUTC();
        DetectorConfig config = DetectorConfig.fromEnvironment();
        this.maxFutureSkewSeconds = config.maxFutureSkewSeconds();
        RuleEngineService ruleEngine = new RuleEngineServiceImpl();
        this.requestDetector = new RequestDetectionServiceImpl(
                ruleEngine, config.request(), clock, maxFutureSkewSeconds);
        this.ddosDetector = new DdosDetectionServiceImpl(config.ddos(), clock, maxFutureSkewSeconds);
        this.emitOnlyAlerts = Boolean.parseBoolean(EnvironmentUtils.value("EMIT_ONLY_ALERTS", "false"));

        String bootstrap = EnvironmentUtils.required("KAFKA_BOOTSTRAP_SERVERS");
        String rawTopic = EnvironmentUtils.value("KAFKA_INPUT_TOPIC", "web.requests.v1");
        String domainTopic = EnvironmentUtils.value("KAFKA_DOMAIN_TOPIC", "web.requests.by-domain.v1");
        this.inputTopic = role == Role.DDOS ? domainTopic : rawTopic;
        this.outputTopic = role == Role.REPARTITION ? domainTopic
                : EnvironmentUtils.value("KAFKA_OUTPUT_TOPIC", "security.detections.v1");
        this.dlqTopic = EnvironmentUtils.value("KAFKA_DLQ_TOPIC", "security.invalid.v1");

        Properties consumerProperties = new Properties();
        consumerProperties.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap);
        consumerProperties.put(ConsumerConfig.GROUP_ID_CONFIG, switch (role) {
            case RULES -> EnvironmentUtils.value("KAFKA_CONSUMER_GROUP", "waf-detector-v2");
            case REPARTITION -> EnvironmentUtils.value("KAFKA_REPARTITION_GROUP", "waf-repartition-v2");
            case DDOS -> EnvironmentUtils.value("KAFKA_DDOS_GROUP", "waf-ddos-v2");
        });
        consumerProperties.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        consumerProperties.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        consumerProperties.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, false);
        consumerProperties.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        consumerProperties.put(ConsumerConfig.MAX_POLL_RECORDS_CONFIG,
                EnvironmentUtils.positiveInt("BATCH_SIZE", role == Role.RULES ? 200 : 500));
        consumerProperties.put(ConsumerConfig.MAX_POLL_INTERVAL_MS_CONFIG, 300_000);
        consumer = new KafkaConsumer<>(consumerProperties);

        Properties producerProperties = new Properties();
        producerProperties.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap);
        producerProperties.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        producerProperties.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        producerProperties.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
        producerProperties.put(ProducerConfig.ACKS_CONFIG, "all");
        producerProperties.put(ProducerConfig.LINGER_MS_CONFIG, 10);
        producerProperties.put(ProducerConfig.BATCH_SIZE_CONFIG, 131_072);
        producerProperties.put(ProducerConfig.COMPRESSION_TYPE_CONFIG, "lz4");
        producer = new KafkaProducer<>(producerProperties);
    }

    public void run() {
        Runtime.getRuntime().addShutdownHook(new Thread(consumer::wakeup));
        consumer.subscribe(List.of(inputTopic));
        long processed = 0;
        System.out.printf("role=%s input=%s output=%s%n", role, inputTopic, outputTopic);
        try {
            while (true) {
                ConsumerRecords<String, String> batch = consumer.poll(Duration.ofMillis(100));
                if (batch.isEmpty()) continue;
                List<Future<RecordMetadata>> deliveries = new ArrayList<>();
                Map<TopicPartition, OffsetAndMetadata> offsets = new HashMap<>();
                for (ConsumerRecord<String, String> record : batch) {
                    Map<String, Object> output = null;
                    String destination = outputTopic;
                    String outputKey = null;
                    try {
                        Map<String, Object> data = mapper.readValue(record.value(), new TypeReference<>() {});
                        Event event = EventMapper.from(data, clock, maxFutureSkewSeconds);
                        String expectedKey = role == Role.DDOS ? event.domainKey() : event.clientKey();
                        if (!expectedKey.equals(record.key()))
                            throw new IllegalArgumentException("Kafka key does not match input contract");
                        outputKey = role == Role.RULES ? event.clientKey() : event.domainKey();
                        switch (role) {
                            case RULES -> {
                                DetectionResult result = requestDetector.analyze(event);
                                if (!emitOnlyAlerts || result.alert()) output = result.toMap();
                            }
                            case REPARTITION -> output = event.toMap();
                            case DDOS -> output = ddosDetector.analyze(event)
                                    .map(DetectionResult::toMap).orElse(null);
                        }
                    } catch (JsonProcessingException | IllegalArgumentException ex) {
                        destination = dlqTopic;
                        String message = ex.getMessage() == null ? "invalid event" : ex.getMessage();
                        output = Map.of("schema_version", 1, "source_topic", record.topic(),
                                "source_partition", record.partition(), "source_offset", record.offset(),
                                "error", message.substring(0, Math.min(300, message.length())));
                    }
                    if (output != null) {
                        deliveries.add(producer.send(new ProducerRecord<>(destination, outputKey,
                                mapper.writeValueAsString(output))));
                    }
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
            throw new RuntimeException("Kafka output/commit failed; restart and replay uncommitted batch", failure);
        } finally {
            consumer.close();
            producer.close(Duration.ofSeconds(5));
        }
    }
}
