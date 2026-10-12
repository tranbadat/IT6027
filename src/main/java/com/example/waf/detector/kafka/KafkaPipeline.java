package com.example.waf.detector.kafka;

import com.example.waf.detector.config.DetectorConfig;
import com.example.waf.detector.enums.Role;
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

    private final Role role;
    private final String inputTopic;
    private final String outputTopic;
    private final String attackTopic;
    private final String alertTopic;
    private final String dlqTopic;
    private final boolean emitOnlyAlerts;
    private final KafkaConsumer<String, String> consumer;
    private final KafkaProducer<String, String> producer;
    private final ObjectMapper mapper = new ObjectMapper();
    private final RequestDetectionService requestDetector;
    private final DdosDetectionService ddosDetector;
    private final Clock clock;
    private final int maxFutureSkewSeconds;
    private final RuleChangeSubscriber ruleChangeSubscriber;

    // Khởi tạo
    public KafkaPipeline(Role role) {
        this.role = role;
        this.clock = Clock.systemUTC();

        // Load config Detector từ env
        DetectorConfig config = DetectorConfig.fromEnvironment();

        // Độ lệch tối đa cho phép của timestamp "tương lai" (giây), dùng để loại bỏ event có thời gian bất thường
        this.maxFutureSkewSeconds = config.maxFutureSkewSeconds();

        // Khởi tạo các service phát hiện tấn công
        RuleEngineService ruleEngine = new RuleEngineServiceImpl();
        this.ruleChangeSubscriber = role == Role.DETECTION ? new RuleChangeSubscriber(ruleEngine) : null;
        this.requestDetector = new RequestDetectionServiceImpl(
                ruleEngine, config.request(), clock, maxFutureSkewSeconds);
        // Detector phát hiện DDoS (dựa trên thống kê theo domain)
        this.ddosDetector = new DdosDetectionServiceImpl(config.ddos(), clock, maxFutureSkewSeconds);
        // Nếu true: chỉ đẩy ra topic output các event có cảnh báo (bỏ qua event bình thường)
        this.emitOnlyAlerts = Boolean.parseBoolean(EnvironmentUtils.value("EMIT_ONLY_ALERTS", "false"));

        // KAFKA CONFIG
        // Địa chỉ Kafka broker
        String bootstrap = EnvironmentUtils.required("KAFKA_BOOTSTRAP_SERVERS");
        // Topic chứa request thô ban đầu
        String rawTopic = EnvironmentUtils.value("KAFKA_INPUT_TOPIC", "web.requests.v1");
        // Topic chứa request đã được phân vùng lại theo domain
        String domainTopic = EnvironmentUtils.value("KAFKA_DOMAIN_TOPIC", "web.requests.by-domain.v1");
        // DDOS đọc từ topic đã phân vùng theo domain; DETECTION và REPARTITION đọc từ topic thô
        this.inputTopic = role == Role.DDOS ? domainTopic : rawTopic;
        // REPARTITION ghi ra topic theo domain; các role còn lại ghi ra topic kết quả phát hiện
        this.outputTopic = role == Role.REPARTITION ? domainTopic
                : EnvironmentUtils.value("ANOMALY_SCORED_TOPIC", "anomaly.scored.v1");
        this.attackTopic = EnvironmentUtils.value("ATTACK_DETECTED_TOPIC", "attack.detected.v1");
        this.alertTopic = EnvironmentUtils.value("ALERT_TRIGGERED_TOPIC", "alert.triggered.v1");
        // Dead-letter queue: nơi chứa các message không hợp lệ / xử lý lỗi
        this.dlqTopic = EnvironmentUtils.value("KAFKA_DLQ_TOPIC", "security.invalid.v1");

        // ===== Cấu hình Kafka Consumer =====
        Properties consumerProperties = new Properties();
        consumerProperties.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap);
        // Mỗi role dùng một consumer group riêng để offset và việc chia partition độc lập nhau
        consumerProperties.put(ConsumerConfig.GROUP_ID_CONFIG, switch (role) {
            case DETECTION -> EnvironmentUtils.value("KAFKA_CONSUMER_GROUP", "waf-detector-v2");
            case REPARTITION -> EnvironmentUtils.value("KAFKA_REPARTITION_GROUP", "waf-repartition-v2");
            case DDOS -> EnvironmentUtils.value("KAFKA_DDOS_GROUP", "waf-ddos-v2");
            case RULE_MANAGEMENT -> throw new IllegalArgumentException("RULE_MANAGEMENT does not use KafkaPipeline");
        });
        // Key/value đều được đọc dưới dạng String (payload là JSON text)
        consumerProperties.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        consumerProperties.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        // Tắt auto-commit: tự commit offset thủ công sau khi xử lý xong để tránh mất dữ liệu (at-least-once)
        consumerProperties.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, false);
        // Nếu group chưa có offset thì đọc từ đầu topic
        consumerProperties.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        // DETECTION xử lý nặng hơn nên dùng batch nhỏ hơn (200), các role khác dùng 500
        consumerProperties.put(ConsumerConfig.MAX_POLL_RECORDS_CONFIG,
                EnvironmentUtils.positiveInt("BATCH_SIZE", role == Role.DETECTION ? 200 : 500));
        // Thời gian tối đa giữa 2 lần poll (5 phút); vượt quá sẽ bị coi là consumer chết và rebalance
        consumerProperties.put(ConsumerConfig.MAX_POLL_INTERVAL_MS_CONFIG, 300_000);
        consumer = new KafkaConsumer<>(consumerProperties);

        // ===== Cấu hình Kafka Producer =====
        Properties producerProperties = new Properties();
        producerProperties.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap);
        producerProperties.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        producerProperties.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        // Bật idempotence: tránh ghi trùng message khi producer retry
        producerProperties.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
        // acks=all: đợi tất cả replica in-sync xác nhận, đảm bảo độ bền dữ liệu cao nhất
        producerProperties.put(ProducerConfig.ACKS_CONFIG, "all");
        // Chờ tối đa 10ms để gom thêm message vào cùng một batch, tăng throughput
        producerProperties.put(ProducerConfig.LINGER_MS_CONFIG, 10);
        // Kích thước batch tối đa 128KB (131_072 bytes)
        producerProperties.put(ProducerConfig.BATCH_SIZE_CONFIG, 131_072);
        // Nén lz4: cân bằng tốt giữa tốc độ và tỷ lệ nén
        producerProperties.put(ProducerConfig.COMPRESSION_TYPE_CONFIG, "lz4");
        producer = new KafkaProducer<>(producerProperties);
    }

    public void run() {
        if (ruleChangeSubscriber != null) ruleChangeSubscriber.start();
        // Đăng ký shutdown hook: khi nhận SIGTERM/Ctrl+C thì gọi wakeup() để poll() ném WakeupException và thoát vòng lặp
        Runtime.getRuntime().addShutdownHook(new Thread(consumer::wakeup));
        // DETECTION/REPARTITION đọc topic thô; DDOS đọc topic theo domain
        consumer.subscribe(List.of(inputTopic));
        long processed = 0;
        System.out.printf("role=%s input=%s output=%s%n", role, inputTopic, outputTopic);
        try {
            while (true) {
                // Poll tối đa 100ms; nếu không có dữ liệu thì quay lại poll tiếp
                ConsumerRecords<String, String> batch = consumer.poll(Duration.ofMillis(100));
                if (batch.isEmpty()) continue;

                // Danh sách future của các message đã gửi, dùng để chờ xác nhận trước khi commit offset
                List<Future<RecordMetadata>> deliveries = new ArrayList<>();
                // Offset cần commit cho từng partition (offset cuối cùng đã xử lý + 1)
                Map<TopicPartition, OffsetAndMetadata> offsets = new HashMap<>();

                for (ConsumerRecord<String, String> record : batch) {
                    Map<String, Object> output = null;     // Kết quả cần ghi ra (null = không ghi gì)
                    String destination = outputTopic;      // Mặc định ghi ra topic output
                    String outputKey = null;               // Key của message đầu ra
                    try {
                        // Parse JSON đầu vào thành Map, rồi chuyển thành Event (có kiểm tra timestamp lệch tương lai)
                        Map<String, Object> data = mapper.readValue(record.value(), new TypeReference<>() {});
                        Event event = EventMapper.from(data, clock, maxFutureSkewSeconds);

                        // Kiểm tra key của Kafka khớp với hợp đồng đầu vào:
                        // DDOS nhận message đã partition theo domain, các role còn lại partition theo client
                        String expectedKey = role == Role.DDOS ? event.domainKey() : event.clientKey();
                        if (!expectedKey.equals(record.key()))
                            throw new IllegalArgumentException("Kafka key does not match input contract");

                        // DETECTION giữ key theo client, các role còn lại dùng key theo domain
                        outputKey = role == Role.DETECTION ? event.clientKey() : event.domainKey();

                        switch (role) {
                            case DETECTION -> {
                                // Phân tích theo luật; nếu bật emitOnlyAlerts thì chỉ ghi ra khi có cảnh báo
                                DetectionResult result = requestDetector.analyze(event);
                                String json = mapper.writeValueAsString(result.toMap());
                                if (!result.matchedRules().isEmpty())
                                    deliveries.add(producer.send(new ProducerRecord<>(attackTopic, outputKey, json)));
                                if (result.alert())
                                    deliveries.add(producer.send(new ProducerRecord<>(alertTopic, outputKey, json)));
                                if (!emitOnlyAlerts || result.alert()) output = result.toMap();
                            }
                            // REPARTITION: chuyển nguyên event sang topic mới với key là domain
                            case REPARTITION -> output = event.toMap();
                            // DDOS: chỉ ghi ra khi detector phát hiện bất thường (Optional rỗng -> không ghi)
                            case DDOS -> output = ddosDetector.analyze(event)
                                    .map(DetectionResult::toMap).orElse(null);
                            case RULE_MANAGEMENT -> throw new IllegalStateException("RULE_MANAGEMENT does not consume events");
                        }
                    } catch (JsonProcessingException | IllegalArgumentException ex) {
                        // Message lỗi (JSON sai, sai contract, timestamp bất thường...) -> đẩy sang DLQ
                        // kèm vị trí gốc (topic/partition/offset) để truy vết, cắt message lỗi tối đa 300 ký tự
                        destination = dlqTopic;
                        String message = ex.getMessage() == null ? "invalid event" : ex.getMessage();
                        output = Map.of("schema_version", 1, "source_topic", record.topic(),
                                "source_partition", record.partition(), "source_offset", record.offset(),
                                "error", message.substring(0, Math.min(300, message.length())));
                    }

                    // Gửi bất đồng bộ ra topic đích và giữ lại Future để chờ xác nhận sau
                    if (output != null) {
                        if (role == Role.DDOS) destination = alertTopic;
                        deliveries.add(producer.send(new ProducerRecord<>(destination, outputKey,
                                mapper.writeValueAsString(output))));
                    }
                    // Ghi nhận offset của record này (kể cả record không có output) để vẫn được commit
                    offsets.put(new TopicPartition(record.topic(), record.partition()),
                            new OffsetAndMetadata(record.offset() + 1));
                }

                // Chờ broker xác nhận toàn bộ message của batch (tối đa 30s mỗi message); lỗi sẽ ném exception
                for (Future<RecordMetadata> delivery : deliveries)
                    delivery.get(30, TimeUnit.SECONDS);
                // Chỉ commit offset SAU KHI đã gửi xong tất cả -> đảm bảo at-least-once
                consumer.commitSync(offsets);

                // Cập nhật bộ đếm và log mỗi khi vượt mốc 10.000 message
                processed += batch.count();
                if (processed % 10_000 < batch.count())
                    System.out.printf("role=%s processed=%d%n", role, processed);
            }
        } catch (WakeupException stopped) {
            // Dừng bình thường do shutdown hook gọi wakeup()
            System.out.println("Kafka worker stopping");
        } catch (Exception failure) {
            // Lỗi gửi/commit: ném lỗi để process chết, khi restart sẽ đọc lại batch chưa commit
            throw new RuntimeException("Kafka output/commit failed; restart and replay uncommitted batch", failure);
        } finally {
            // Đóng consumer (rời group sạch sẽ) và producer (đợi tối đa 5s để flush phần còn lại)
            consumer.close();
            producer.close(Duration.ofSeconds(5));
            if (ruleChangeSubscriber != null) ruleChangeSubscriber.close();
        }
    }
}
