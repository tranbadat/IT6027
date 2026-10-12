package com.example.waf.detector.kafka;

import com.example.waf.detector.model.RuleDocument;
import com.example.waf.detector.service.RuleChangePublisher;
import com.example.waf.detector.util.EnvironmentUtils;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.serialization.StringSerializer;

import java.time.Duration;
import java.util.Properties;
import java.util.concurrent.TimeUnit;

public final class KafkaRuleChangePublisher implements RuleChangePublisher {
    private final KafkaProducer<String, String> producer;
    private final String topic = EnvironmentUtils.value("RULE_CHANGES_TOPIC", "rules.changed.v1");
    private final ObjectMapper mapper = new ObjectMapper();

    public KafkaRuleChangePublisher() {
        Properties properties = new Properties();
        properties.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, EnvironmentUtils.required("KAFKA_BOOTSTRAP_SERVERS"));
        properties.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        properties.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        properties.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
        properties.put(ProducerConfig.ACKS_CONFIG, "all");
        producer = new KafkaProducer<>(properties);
    }

    @Override public void publish(RuleDocument document) {
        try {
            String value = mapper.writeValueAsString(document);
            producer.send(new ProducerRecord<>(topic, "active", value)).get(30, TimeUnit.SECONDS);
        } catch (Exception failure) {
            throw new IllegalStateException("Cannot publish rule snapshot", failure);
        }
    }

    @Override public void close() { producer.close(Duration.ofSeconds(5)); }
}
