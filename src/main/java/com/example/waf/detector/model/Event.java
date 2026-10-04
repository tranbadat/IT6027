package com.example.waf.detector.model;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

public record Event(String eventId, String timestamp, String domain, String clientIp,
                    String method, String path, String query, String body,
                    String userAgent, int status) {
    public String domainKey() {
        return domain.toLowerCase(Locale.ROOT);
    }

    public String clientKey() {
        return domainKey() + "|" + clientIp;
    }

    public long epochMilli() {
        return OffsetDateTime.parse(timestamp).toInstant().toEpochMilli();
    }

    public long epochSecond() {
        return epochMilli() / 1_000;
    }

    public Map<String, Object> toMap() {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("event_id", eventId);
        data.put("timestamp", timestamp);
        data.put("domain", domain);
        data.put("client_ip", clientIp);
        data.put("method", method);
        data.put("path", path);
        data.put("query", query);
        data.put("body", body);
        data.put("user_agent", userAgent);
        data.put("status", status);
        return data;
    }
}
