package com.example.waf.detector.model;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Một HTTP request đã chuẩn hoá. Đọc từ envelope log.enriched (C3) hoặc từ JSON phẳng
 * (simulator). requestId/sessionId/host giữ nguyên từ envelope để truy vết xuyên pipeline.
 */
public record Event(String eventId, String timestamp, String domain, String clientIp,
                    String method, String path, String query, String body,
                    String userAgent, int status, Map<String, String> headers,
                    String requestId, String sessionId, String host) {
    public Event {
        headers = headers == null ? Map.of() : Map.copyOf(headers);
        requestId = requestId == null ? "" : requestId;
        sessionId = sessionId == null ? "" : sessionId;
        host = host == null ? "" : host;
    }

    public Event(String eventId, String timestamp, String domain, String clientIp,
                 String method, String path, String query, String body,
                 String userAgent, int status, Map<String, String> headers) {
        this(eventId, timestamp, domain, clientIp, method, path, query, body, userAgent, status,
                headers, "", "", "");
    }

    public Event(String eventId, String timestamp, String domain, String clientIp,
                 String method, String path, String query, String body,
                 String userAgent, int status) {
        this(eventId, timestamp, domain, clientIp, method, path, query, body, userAgent, status, Map.of());
    }

    public String domainKey() {
        return domain.toLowerCase(Locale.ROOT);
    }

    public String clientKey() {
        return domainKey() + "|" + clientIp;
    }

    /** URL gốc của request (path + query) để hiển thị trong cảnh báo. */
    public String url() {
        return query == null || query.isEmpty() ? path : path + "?" + query;
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
        data.put("headers", headers);
        return data;
    }
}
