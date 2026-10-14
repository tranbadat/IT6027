package com.example.waf.detector.util;

import com.example.waf.detector.model.Event;

import java.time.Clock;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Kiểm tra và chuyển JSON đầu vào thành {@link Event}. Nhận hai dạng:
 * <ul>
 *   <li>envelope log.enriched theo contracts/events (nguồn chính, do C3 phát ra);</li>
 *   <li>JSON phẳng web.requests (simulator khi phát triển độc lập).</li>
 * </ul>
 * Chỉ lỗi cấu trúc (thiếu domain, IP, event_id, timestamp sai) mới ném IllegalArgumentException để
 * pipeline chuyển sang dead-letter. Trường quá dài bị cắt bớt thay vì loại cả message, vì kẻ tấn công
 * có thể cố ý đệm User-Agent/query để né phát hiện.
 */
public final class EventMapper {
    public static final String ENRICHED_EVENT = "log.enriched";
    /** Giá trị thay thế khi C2 không parse được dòng request (method rỗng, status "-"). */
    public static final String UNKNOWN = "-";
    static final int MAX_METHOD = 16;
    static final int MAX_PATH = 4096;
    static final int MAX_QUERY = 8192;
    static final int MAX_USER_AGENT = 2048;
    static final int MAX_REFERER = 4096;
    static final int MAX_BODY = 8192;

    private EventMapper() {}

    public static Event from(Map<String, Object> data, Clock clock, int maxFutureSkewSeconds) {
        if (data == null) throw new IllegalArgumentException("Event must be a JSON object");
        return data.containsKey("event") && data.containsKey("data")
                ? fromEnvelope(data, clock, maxFutureSkewSeconds)
                : fromFlat(data, clock, maxFutureSkewSeconds);
    }

    private static Event fromEnvelope(Map<String, Object> envelope, Clock clock, int maxFutureSkewSeconds) {
        if (!ENRICHED_EVENT.equals(envelope.get("event")))
            throw new IllegalArgumentException("Unsupported event: " + envelope.get("event"));
        if (!(envelope.get("data") instanceof Map<?, ?> rawData))
            throw new IllegalArgumentException("Envelope data must be an object");
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) rawData;

        String id = field(envelope, "event_id", 128, true);
        String requestTs = optional(data, "request_ts", 64);
        String stamp = requestTs.isBlank() ? field(envelope, "timestamp", 64, true) : requestTs;
        String domain = field(envelope, "domain", 255, true);
        String ip = field(data, "src_ip", 64, true);
        // Dòng log mà C2 không parse được request line (rác, CONNECT, absolute-form, M-SEARCH...)
        // vẫn được phân tích: đây thường chính là lưu lượng dò quét.
        String method = orUnknown(optional(data, "method", MAX_METHOD)).toUpperCase(Locale.ROOT);
        String path = orUnknown(optional(data, "path", MAX_PATH));

        Map<String, String> headers = new LinkedHashMap<>();
        String userAgent = optional(data, "user_agent", MAX_USER_AGENT);
        String referer = optional(data, "referer", MAX_REFERER);
        if (!userAgent.isEmpty()) headers.put("user-agent", userAgent);
        if (!referer.isEmpty()) headers.put("referer", referer);

        return new Event(id, timestamp(stamp, clock, maxFutureSkewSeconds), domain, ip, method, path,
                optional(data, "query_string", MAX_QUERY), "", userAgent, lenientStatus(data), headers,
                optional(envelope, "request_id", 128), optional(envelope, "session_id", 128),
                optional(data, "host", 255));
    }

    private static Event fromFlat(Map<String, Object> data, Clock clock, int maxFutureSkewSeconds) {
        String id = field(data, "event_id", 128, true);
        String stamp = field(data, "timestamp", 64, true);
        String domain = field(data, "domain", 255, true);
        String ip = field(data, "client_ip", 64, true);
        String method = field(data, "method", 16, true).toUpperCase(Locale.ROOT);
        String path = truncate(field(data, "path", Integer.MAX_VALUE, true), MAX_PATH);
        validateRequestLine(method, path);
        return new Event(id, timestamp(stamp, clock, maxFutureSkewSeconds), domain, ip, method, path,
                optional(data, "query", MAX_QUERY), optional(data, "body", MAX_BODY),
                optional(data, "user_agent", MAX_USER_AGENT), status(data), headers(data));
    }

    private static String timestamp(String stamp, Clock clock, int maxFutureSkewSeconds) {
        OffsetDateTime parsed;
        try {
            parsed = OffsetDateTime.parse(stamp).withOffsetSameInstant(ZoneOffset.UTC);
        } catch (RuntimeException ex) {
            throw new IllegalArgumentException("timestamp must be ISO 8601 with timezone", ex);
        }
        if (parsed.toInstant().isAfter(clock.instant().plusSeconds(maxFutureSkewSeconds)))
            throw new IllegalArgumentException("timestamp exceeds allowed future clock skew");
        return parsed.toString();
    }

    private static void validateRequestLine(String method, String path) {
        if (!method.matches("[A-Z]{1,16}") || !path.startsWith("/"))
            throw new IllegalArgumentException("Invalid method or path");
    }

    /** status từ C2: null khi log ghi "-" -> 0 (không tính là lỗi). */
    private static int lenientStatus(Map<String, Object> data) {
        Object code = data.get("status");
        return code instanceof Integer value && value >= 100 && value <= 599 ? value : 0;
    }

    private static int status(Map<String, Object> data) {
        Object code = data.getOrDefault("status", 200);
        if (!(code instanceof Integer value) || value < 100 || value > 599)
            throw new IllegalArgumentException("status must be an integer from 100 to 599");
        return value;
    }

    private static Map<String, String> headers(Map<String, Object> data) {
        Object raw = data.get("headers");
        if (raw == null) return Map.of();
        if (!(raw instanceof Map<?, ?> values) || values.size() > 100)
            throw new IllegalArgumentException("headers must be an object with at most 100 entries");
        Map<String, String> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : values.entrySet()) {
            if (!(entry.getKey() instanceof String key) || !(entry.getValue() instanceof String value)
                    || key.length() > 128)
                throw new IllegalArgumentException("Invalid header");
            result.put(key.toLowerCase(Locale.ROOT), truncate(value, MAX_REFERER));
        }
        return result;
    }

    /** Trường tuỳ chọn kiểu chuỗi: thiếu hoặc null -> "", quá dài -> cắt bớt, sai kiểu -> lỗi. */
    private static String optional(Map<String, Object> data, String key, int maximum) {
        Object value = data.get(key);
        if (value == null) return "";
        if (!(value instanceof String text)) throw new IllegalArgumentException("Invalid field: " + key);
        return truncate(text, maximum);
    }

    private static String orUnknown(String value) {
        return value.isBlank() ? UNKNOWN : value;
    }

    private static String truncate(String value, int maximum) {
        return value.length() <= maximum ? value : value.substring(0, maximum);
    }

    private static String field(Map<String, Object> data, String key, int maximum, boolean required) {
        Object value = data.get(key);
        if (value == null && !required) return "";
        if (!(value instanceof String text) || text.length() > maximum || (required && text.isBlank()))
            throw new IllegalArgumentException("Invalid field: " + key);
        return text;
    }
}
