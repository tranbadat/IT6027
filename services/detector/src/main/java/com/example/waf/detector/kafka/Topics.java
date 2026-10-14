package com.example.waf.detector.kafka;

import com.example.waf.detector.util.EnvironmentUtils;

/** Tên topic Kafka. Mặc định trùng tên event trong contracts/events (topic = tên event). */
public record Topics(String input, String domainInput, String attack, String scored,
                     String alert, String deadLetter) {
    public static Topics fromEnvironment() {
        return new Topics(
                EnvironmentUtils.value("KAFKA_INPUT_TOPIC", "log.enriched"),
                EnvironmentUtils.value("KAFKA_DOMAIN_TOPIC", "log.enriched.by-domain"),
                EnvironmentUtils.value("ATTACK_DETECTED_TOPIC", "attack.detected"),
                EnvironmentUtils.value("ANOMALY_SCORED_TOPIC", "anomaly.scored"),
                EnvironmentUtils.value("ALERT_TRIGGERED_TOPIC", "alert.triggered"),
                EnvironmentUtils.value("KAFKA_DLQ_TOPIC", "waf.deadletter"));
    }
}
