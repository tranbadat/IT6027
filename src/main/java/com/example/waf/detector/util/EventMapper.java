package com.example.waf.detector.util;

import com.example.waf.detector.model.Event;

import java.time.Clock;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Locale;
import java.util.Map;

public final class EventMapper {
    private EventMapper() {}

    public static Event from(Map<String, Object> data, Clock clock, int maxFutureSkewSeconds) {
        if (data == null) throw new IllegalArgumentException("Event must be a JSON object");
        String id = field(data, "event_id", 128, true);
        String stamp = field(data, "timestamp", 64, true);
        OffsetDateTime parsed;
        try {
            parsed = OffsetDateTime.parse(stamp).withOffsetSameInstant(ZoneOffset.UTC);
        } catch (RuntimeException ex) {
            throw new IllegalArgumentException("timestamp must be ISO 8601 with timezone", ex);
        }
        if (parsed.toInstant().isAfter(clock.instant().plusSeconds(maxFutureSkewSeconds)))
            throw new IllegalArgumentException("timestamp exceeds allowed future clock skew");
        String domain = field(data, "domain", 255, true);
        String ip = field(data, "client_ip", 64, true);
        String method = field(data, "method", 16, true).toUpperCase(Locale.ROOT);
        String path = field(data, "path", 4096, true);
        if (!method.matches("[A-Z]{1,16}") || !path.startsWith("/"))
            throw new IllegalArgumentException("Invalid method or path");
        Object code = data.getOrDefault("status", 200);
        if (!(code instanceof Integer) || (int) code < 100 || (int) code > 599)
            throw new IllegalArgumentException("status must be an integer from 100 to 599");
        return new Event(id, parsed.toString(), domain, ip, method, path,
                field(data, "query", 8192, false), field(data, "body", 8192, false),
                field(data, "user_agent", 2048, false), (int) code);
    }

    private static String field(Map<String, Object> data, String key, int maximum, boolean required) {
        Object value = data.getOrDefault(key, "");
        if (!(value instanceof String text) || text.length() > maximum || (required && text.isBlank()))
            throw new IllegalArgumentException("Invalid field: " + key);
        return text;
    }
}
