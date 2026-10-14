package com.example.waf.detector.kafka;

import com.example.waf.detector.model.DetectionResult;
import com.example.waf.detector.model.Event;
import com.example.waf.detector.model.RuleMatch;

import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Dựng envelope đầu ra theo contracts/events: attack.detected, anomaly.scored, alert.triggered.
 * Trường data giữ đúng tên mà P3/P4/P5 và metrics-exporter đang đọc.
 */
public final class EnvelopeFactory {
    public static final String SOURCE = "detector";
    /** src_ip của cảnh báo DDoS: nguồn phân tán nên P4 gom (dedup) theo domain, không theo từng IP. */
    public static final String DISTRIBUTED_SOURCE = "distributed";

    private final Clock clock;

    public EnvelopeFactory(Clock clock) {
        this.clock = clock;
    }

    public Map<String, Object> attackDetected(Event event, RuleMatch match) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("attack_type", match.type());
        data.put("severity", DetectionResult.severity(match.weight()));
        data.put("rule_id", match.ruleId());
        data.put("rule_name", match.description());
        data.put("src_ip", event.clientIp());
        data.put("method", event.method());
        data.put("url", event.url());
        data.put("host", event.host());
        data.put("field", match.field());
        data.put("matched_value", match.matchedValue());
        data.put("evidence", match.description());
        return envelope("attack.detected", event, data, event.eventId() + "|" + match.ruleId());
    }

    public Map<String, Object> anomalyScored(Event event, DetectionResult result, int threshold) {
        Map<String, Object> data = scoreData(event, result, threshold);
        data.put("signals", signals(result));
        data.put("reasons", result.anomalyReasons());
        return envelope("anomaly.scored", event, data, result.eventId());
    }

    public Map<String, Object> alertTriggered(Event event, DetectionResult result, int threshold) {
        Map<String, Object> data = scoreData(event, result, threshold);
        List<String> ruleIds = result.matchedRules().stream().map(RuleMatch::ruleId).distinct().toList();
        data.put("rule_ids", ruleIds);
        data.put("signals", signals(result));
        data.put("reason", "score " + result.score() + " >= threshold " + threshold
                + "; rules=" + ruleIds + "; signals=" + result.anomalyReasons());
        return envelope("alert.triggered", event, data, result.eventId());
    }

    private Map<String, Object> scoreData(Event event, DetectionResult result, int threshold) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("score", result.score());
        data.put("threshold", threshold);
        data.put("severity", result.severity());
        List<String> types = attackTypes(result);
        data.put("src_ip", types.contains("ddos") ? DISTRIBUTED_SOURCE : event.clientIp());
        data.put("url", event.url());
        data.put("host", event.host());
        data.put("attack_types", types);
        return data;
    }

    /** Cảnh báo chỉ do hành vi (không rule nào khớp) vẫn cần loại tấn công để P4 gom và P5 hiển thị. */
    static List<String> attackTypes(DetectionResult result) {
        if (!result.attackTypes().isEmpty()) return result.attackTypes();
        return result.anomalyReasons().stream()
                .map(reason -> switch (reason) {
                    case "request_burst" -> "traffic_flood";
                    case "http_error_burst" -> "error_scan";
                    case "path_scan" -> "path_scan";
                    case "distributed_request_burst" -> "ddos";
                    default -> reason;
                })
                .filter(type -> !type.equals("late_event_outside_window"))
                .distinct().toList();
    }

    private static Map<String, Object> signals(DetectionResult result) {
        Map<String, Object> window = result.window();
        Map<String, Object> signals = new LinkedHashMap<>();
        signals.put("rule_hits", result.matchedRules().size());
        signals.put("req_count", window.getOrDefault("requests", 0));
        signals.put("error_count", window.getOrDefault("errors", 0));
        signals.put("distinct_paths", window.getOrDefault("distinct_paths", 0));
        if (window.containsKey("unique_ips")) signals.put("unique_ips", window.get("unique_ips"));
        signals.put("window_seconds", window.getOrDefault("seconds", 0));
        return signals;
    }

    /**
     * event_id xác định từ id của input (và rule/loại kết quả): xử lý lại cùng record sau sự cố cho ra
     * cùng event_id, nên P4/P5 loại được bản trùng bằng ON CONFLICT (event_id).
     */
    private Map<String, Object> envelope(String name, Event event, Map<String, Object> data, String discriminator) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("event", name);
        envelope.put("event_id", UUID.nameUUIDFromBytes((name + "|" + discriminator)
                .getBytes(StandardCharsets.UTF_8)).toString());
        envelope.put("schema_version", 1);
        envelope.put("timestamp", clock.instant().toString());
        envelope.put("source", SOURCE);
        envelope.put("domain", event.domain());
        envelope.put("request_id", event.requestId().isEmpty() ? event.eventId() : event.requestId());
        if (!event.sessionId().isEmpty()) envelope.put("session_id", event.sessionId());
        envelope.put("data", data);
        return envelope;
    }
}
