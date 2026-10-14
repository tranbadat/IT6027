package com.example.waf.detector.model;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public record DetectionResult(
        String eventId,
        String triggerEventId,
        String timestamp,
        String analyzedAt,
        String domain,
        String clientIp,
        String method,
        String path,
        int status,
        int score,
        String severity,
        boolean alert,
        boolean alertSuppressed,
        List<String> attackTypes,
        List<RuleMatch> matchedRules,
        List<String> anomalyReasons,
        Map<String, Object> window) {

    public Map<String, Object> toMap() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", 1);
        result.put("event_id", eventId);
        if (triggerEventId != null) result.put("trigger_event_id", triggerEventId);
        result.put("timestamp", timestamp);
        result.put("analyzed_at", analyzedAt);
        result.put("domain", domain);
        result.put("client_ip", clientIp);
        result.put("method", method);
        result.put("path", path);
        result.put("status", status);
        result.put("score", score);
        result.put("severity", severity);
        result.put("alert", alert);
        result.put("alert_suppressed", alertSuppressed);
        result.put("attack_types", attackTypes);
        result.put("matched_rules", matchedRules.stream().map(RuleMatch::toMap).toList());
        result.put("anomaly_reasons", anomalyReasons);
        result.put("window", window);
        return result;
    }

    /** Mức độ theo điểm, cùng thang với contract: critical ≥ 90, high ≥ 70, medium ≥ 40. */
    public static String severity(int score) {
        if (score >= 90) return "critical";
        if (score >= 70) return "high";
        if (score >= 40) return "medium";
        return "low";
    }
}
