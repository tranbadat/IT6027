package com.example.waf.detector.kafka;

import java.util.Map;

/** Một message cần ghi ra Kafka. headers chỉ dùng cho dead-letter (lý do lỗi). */
public record OutputRecord(String topic, String key, String value, Map<String, String> headers) {
    public OutputRecord {
        headers = headers == null ? Map.of() : Map.copyOf(headers);
    }

    public OutputRecord(String topic, String key, String value) {
        this(topic, key, value, Map.of());
    }
}
