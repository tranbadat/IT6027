package com.example.waf.detector.kafka;

import com.example.waf.detector.model.RuleDocument;
import com.example.waf.detector.service.RuleEngineService;
import com.example.waf.detector.util.EnvironmentUtils;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.errors.WakeupException;
import org.apache.kafka.common.serialization.StringDeserializer;

import java.time.Duration;
import java.util.List;
import java.util.Properties;
import java.util.UUID;

public final class RuleChangeSubscriber implements AutoCloseable {
    private final KafkaConsumer<String, String> consumer;
    private final RuleEngineService engine;
    private final ObjectMapper mapper = new ObjectMapper();
    private final Thread thread;

    public RuleChangeSubscriber(RuleEngineService engine) {
        this.engine = engine;
        Properties properties = new Properties();
        properties.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, EnvironmentUtils.required("KAFKA_BOOTSTRAP_SERVERS"));
        properties.put(ConsumerConfig.GROUP_ID_CONFIG, "rule-snapshot-" + UUID.randomUUID());
        properties.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        properties.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        properties.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        properties.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, true);
        consumer = new KafkaConsumer<>(properties);
        thread = new Thread(this::consume, "rule-change-subscriber");
        thread.setDaemon(true);
    }

    public void start() { thread.start(); }

    private void consume() {
        consumer.subscribe(List.of(EnvironmentUtils.value("RULE_CHANGES_TOPIC", "rules.changed.v1")));
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
            }
        } catch (WakeupException ignored) { }
        finally { consumer.close(); }
    }

    @Override public void close() { consumer.wakeup(); }
}
