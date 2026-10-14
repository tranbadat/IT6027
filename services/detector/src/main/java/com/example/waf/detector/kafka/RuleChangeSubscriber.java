package com.example.waf.detector.kafka;

import com.example.waf.detector.model.RuleDocument;
import com.example.waf.detector.service.RuleEngineService;
import com.example.waf.detector.util.EnvironmentUtils;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.errors.WakeupException;
import org.apache.kafka.common.serialization.StringDeserializer;

import java.time.Duration;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Đọc snapshot rule từ topic compacted rules.changed và thay rule của engine.
 * Không dùng consumer group: mỗi instance tự assign partition 0 và đọc lại từ đầu khi khởi
 * động, nên không để lại group rác trên broker và luôn nhận snapshot mới nhất.
 */
public final class RuleChangeSubscriber implements AutoCloseable {
    private final KafkaConsumer<String, String> consumer;
    private final RuleEngineService engine;
    private final ObjectMapper mapper = new ObjectMapper();
    private final Thread thread;
    private final CountDownLatch caughtUp = new CountDownLatch(1);

    public RuleChangeSubscriber(RuleEngineService engine) {
        this.engine = engine;
        Properties properties = new Properties();
        properties.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, EnvironmentUtils.required("KAFKA_BOOTSTRAP_SERVERS"));
        properties.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        properties.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        properties.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        properties.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, false);
        consumer = new KafkaConsumer<>(properties);
        thread = new Thread(this::consume, "rule-change-subscriber");
        thread.setDaemon(true);
    }

    public void start() { thread.start(); }

    /**
     * Chờ tới khi đã đọc hết snapshot hiện có trên topic. Gọi trước khi xử lý log để không chạy
     * với rule mặc định trong JAR khi operator đã thêm/tắt rule. Trả false nếu hết thời gian chờ.
     */
    public boolean awaitCaughtUp(Duration timeout) throws InterruptedException {
        return caughtUp.await(timeout.toMillis(), TimeUnit.MILLISECONDS);
    }

    private void consume() {
        TopicPartition partition = new TopicPartition(
                EnvironmentUtils.value("RULE_CHANGES_TOPIC", "rules.changed"), 0);
        consumer.assign(List.of(partition));
        consumer.seekToBeginning(List.of(partition));
        long endOffset = consumer.endOffsets(List.of(partition)).getOrDefault(partition, 0L);
        try {
            while (!Thread.currentThread().isInterrupted()) {
                consumer.poll(Duration.ofSeconds(1)).forEach(record -> {
                    try {
                        RuleDocument document = mapper.readValue(record.value(), RuleDocument.class);
                        engine.replaceRules(document.rules());
                    } catch (Exception invalid) {
                        System.err.println("rule_snapshot_rejected=" + invalid.getMessage());
                    }
                });
                if (caughtUp.getCount() > 0 && consumer.position(partition) >= endOffset) caughtUp.countDown();
            }
        } catch (WakeupException ignored) { }
        finally { consumer.close(); }
    }

    @Override public void close() { consumer.wakeup(); }
}
