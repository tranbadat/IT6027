package com.example.waf.detector.eval;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Ba cấu hình thực nghiệm: A chỉ rule, B chỉ hành vi, C kết hợp. */
class EvaluateTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private static String enriched(String ip, String path, String query, int status, int second, String sid)
            throws Exception {
        Map<String, Object> data = Map.of("src_ip", ip, "method", "GET", "path", path, "query_string", query,
                "status", status, "user_agent", "Mozilla/5.0", "host", "lab.local",
                "request_ts", String.format("2026-10-01T08:00:%02d+00:00", second));
        return MAPPER.writeValueAsString(Map.of("event", "log.enriched", "event_id", "e-" + sid,
                "timestamp", "2026-10-01T08:00:59+00:00", "source", "c3-enrichment", "domain", "lab.local",
                "session_id", sid, "data", data));
    }

    /** Một kẻ tấn công SQLi và một nguồn dò thư mục (30 lỗi 404 ở 30 path khác nhau, không payload). */
    private static List<String> dataset() throws Exception {
        List<String> lines = new ArrayList<>();
        lines.add(enriched("203.0.113.1", "/api", "id=1%20UNION%20SELECT%20pw", 200, 0, "sqli-1"));
        for (int i = 0; i < 30; i++)
            lines.add(enriched("192.0.2.1", "/scan-" + i, "", 404, i, "scan-" + i));
        return lines;
    }

    private static List<JsonNode> outputs(Evaluate.Mode mode) throws Exception {
        List<JsonNode> result = new ArrayList<>();
        for (String line : Evaluate.replay(mode, dataset())) result.add(MAPPER.readTree(line));
        return result;
    }

    private static boolean alerted(List<JsonNode> out, String ip) {
        return out.stream().anyMatch(o -> o.path("topic").asText().equals("alert.triggered")
                && o.path("envelope").path("data").path("src_ip").asText().equals(ip));
    }

    @Test
    void rulesOnlyDetectsPayloadButNotScanning() throws Exception {
        List<JsonNode> out = outputs(Evaluate.Mode.RULES_ONLY);
        assertTrue(alerted(out, "203.0.113.1"));
        assertTrue(!alerted(out, "192.0.2.1"));
    }

    @Test
    void behaviorOnlyDetectsScanningButNoRuleMatches() throws Exception {
        List<JsonNode> out = outputs(Evaluate.Mode.BEHAVIOR_ONLY);
        assertTrue(alerted(out, "192.0.2.1"));
        assertTrue(out.stream().noneMatch(o -> o.path("topic").asText().equals("attack.detected")));
    }

    @Test
    void combinedDetectsBothAndTagsOutputsWithMode() throws Exception {
        List<JsonNode> out = outputs(Evaluate.Mode.COMBINED);
        assertTrue(alerted(out, "203.0.113.1") && alerted(out, "192.0.2.1"));
        assertEquals("C", out.get(0).path("config").asText());
        assertEquals("sqli-1", out.get(0).path("envelope").path("session_id").asText());
    }
}
