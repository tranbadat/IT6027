package com.example.waf.detector.kafka;

import com.example.waf.detector.config.DetectorConfig;
import com.example.waf.detector.enums.Role;
import com.example.waf.detector.service.ScopeService;
import com.example.waf.detector.service.impl.DdosDetectionServiceImpl;
import com.example.waf.detector.service.impl.RequestDetectionServiceImpl;
import com.example.waf.detector.service.impl.RuleEngineServiceImpl;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Kiểm tra xử lý một record Kafka: contract envelope vào/ra, scope, định tuyến DLQ. */
class RecordProcessorTest {
    private static final Clock CLOCK = Clock.fixed(Instant.parse("2026-10-05T00:00:00Z"), ZoneOffset.UTC);
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final Topics TOPICS = new Topics("log.enriched", "log.enriched.by-domain",
            "attack.detected", "anomaly.scored", "alert.triggered", "waf.deadletter");

    private static RecordProcessor processor(Role role, Set<String> allowedDomains) {
        DetectorConfig.Request request = new DetectorConfig.Request(60, 20, 8, 12, 70, 15, 10_000);
        DetectorConfig.Ddos ddos = new DetectorConfig.Ddos(10, 3, 2, 10, 1_000);
        ScopeService scope = domain -> allowedDomains.contains(domain);
        return new RecordProcessor(role, TOPICS,
                new RequestDetectionServiceImpl(new RuleEngineServiceImpl(), request, CLOCK, 300),
                new DdosDetectionServiceImpl(ddos, CLOCK, 300), scope, CLOCK, 300, 70);
    }

    private static String enriched(String ip, String path, String query, int status) throws Exception {
        return enriched(ip, "GET", path, query, status, "Mozilla/5.0");
    }

    private static String enriched(String ip, String method, String path, String query, Integer status,
                                   String userAgent) throws Exception {
        Map<String, Object> data = new java.util.HashMap<>(Map.of(
                "src_ip", ip, "method", method, "path", path, "query_string", query,
                "user_agent", userAgent, "referer", "", "host", "shop.local",
                "request_ts", "2026-10-04T08:00:00+00:00"));
        data.put("status", status); // C2 phát status = null khi log ghi "-"
        Map<String, Object> envelope = Map.of(
                "event", "log.enriched", "event_id", "e-" + ip + path + query, "schema_version", 1,
                "timestamp", "2026-10-04T08:00:01+00:00", "source", "c3-enrichment",
                "domain", "shop.local", "request_id", "req-1", "session_id", "s-1", "data", data);
        return MAPPER.writeValueAsString(envelope);
    }

    private static Map<String, Object> json(String value) throws Exception {
        return MAPPER.readValue(value, new TypeReference<>() {});
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> data(Map<String, Object> envelope) {
        return (Map<String, Object>) envelope.get("data");
    }

    @Test
    void emitsContractEnvelopesForAttackFromEnrichedLog() throws Exception {
        List<OutputRecord> out = processor(Role.DETECTION, Set.of("shop.local"))
                .process("shop.local|10.0.0.1",
                        enriched("10.0.0.1", "/api/products", "id=1%20UNION%20SELECT%20pw", 200));

        OutputRecord attack = out.stream().filter(o -> o.topic().equals("attack.detected")).findFirst().orElseThrow();
        Map<String, Object> env = json(attack.value());
        assertEquals("attack.detected", env.get("event"));
        assertEquals("shop.local", env.get("domain"));
        assertEquals("req-1", env.get("request_id"));
        assertEquals("s-1", env.get("session_id"));
        assertEquals("detector", env.get("source"));
        assertEquals("shop.local|10.0.0.1", attack.key());
        Map<String, Object> d = data(env);
        assertEquals("sql_injection", d.get("attack_type"));
        assertEquals("SQLI_UNION_SELECT", d.get("rule_id"));
        assertEquals("10.0.0.1", d.get("src_ip"));
        assertEquals("/api/products?id=1%20UNION%20SELECT%20pw", d.get("url"));
        assertTrue(String.valueOf(d.get("matched_value")).toUpperCase().contains("UNION SELECT"), d.toString());
        // Dashboard hiển thị mã HTTP, User-Agent và điểm của rule cho từng sự kiện.
        assertEquals(200, d.get("status"));
        assertEquals("Mozilla/5.0", d.get("user_agent"));
        assertTrue((Integer) d.get("score") >= 1, d.toString());

        Map<String, Object> scored = data(json(out.stream()
                .filter(o -> o.topic().equals("anomaly.scored")).findFirst().orElseThrow().value()));
        assertEquals(70, scored.get("threshold"));
        assertTrue((Integer) scored.get("score") >= 70, scored.toString());

        Map<String, Object> alert = data(json(out.stream()
                .filter(o -> o.topic().equals("alert.triggered")).findFirst().orElseThrow().value()));
        assertEquals(List.of("sql_injection"), alert.get("attack_types"));
        assertTrue(String.valueOf(alert.get("reason")).contains("SQLI_UNION_SELECT"), alert.toString());
    }

    @Test
    void normalRequestProducesNoOutput() throws Exception {
        List<OutputRecord> out = processor(Role.DETECTION, Set.of("shop.local"))
                .process("shop.local|10.0.0.2", enriched("10.0.0.2", "/blog", "content=hello", 200));
        assertTrue(out.isEmpty(), out.toString());
    }

    @Test
    void skipsDomainOutsideScope() throws Exception {
        List<OutputRecord> out = processor(Role.DETECTION, Set.of("blog.local"))
                .process("shop.local|10.0.0.3",
                        enriched("10.0.0.3", "/api", "id=1%20UNION%20SELECT%20x", 200));
        assertTrue(out.isEmpty(), out.toString());
    }

    @Test
    void invalidRecordGoesToDeadLetter() throws Exception {
        List<OutputRecord> out = processor(Role.DETECTION, Set.of("shop.local")).process("k", "{not json");
        assertEquals(1, out.size());
        assertEquals("waf.deadletter", out.get(0).topic());
    }

    @Test
    void ddosRoleSendsInvalidRecordToDeadLetterNotAlerts() throws Exception {
        List<OutputRecord> out = processor(Role.DDOS, Set.of("shop.local")).process("k", "{not json");
        assertEquals(1, out.size());
        assertEquals("waf.deadletter", out.get(0).topic());
    }

    @Test
    void ddosRoleEmitsContractAlertWhenThresholdReached() throws Exception {
        RecordProcessor ddos = processor(Role.DDOS, Set.of("shop.local"));
        List<OutputRecord> last = List.of();
        for (String ip : List.of("10.0.1.1", "10.0.1.2", "10.0.1.3"))
            last = ddos.process("shop.local", enriched(ip, "/", "", 200));
        assertEquals(1, last.size(), last.toString());
        assertEquals("alert.triggered", last.get(0).topic());
        Map<String, Object> d = data(json(last.get(0).value()));
        assertEquals(List.of("ddos"), d.get("attack_types"));
        assertFalse(String.valueOf(d.get("reason")).isBlank());
    }

    @Test
    void repartitionKeepsEnvelopeAndKeysByDomain() throws Exception {
        String input = enriched("10.0.2.1", "/", "", 200);
        List<OutputRecord> out = processor(Role.REPARTITION, Set.of("shop.local"))
                .process("shop.local|10.0.2.1", input);
        assertEquals(1, out.size());
        assertEquals("log.enriched.by-domain", out.get(0).topic());
        assertEquals("shop.local", out.get(0).key());
        assertEquals(json(input), json(out.get(0).value()));
    }

    @Test
    void oversizedFieldsAreTruncatedNotDeadLettered() throws Exception {
        String padding = "A".repeat(2_100); // vượt giới hạn 2048 của user agent
        List<OutputRecord> out = processor(Role.DETECTION, Set.of("shop.local"))
                .process("shop.local|10.0.3.1", enriched("10.0.3.1", "GET", "/api",
                        "id=1%20UNION%20SELECT%20x", 200, padding));
        assertTrue(out.stream().anyMatch(o -> o.topic().equals("attack.detected")), out.toString());
        assertTrue(out.stream().noneMatch(o -> o.topic().equals("waf.deadletter")), out.toString());
    }

    @Test
    void unparsedRequestLinesAreStillAnalysed() throws Exception {
        RecordProcessor detector = processor(Role.DETECTION, Set.of("shop.local"));
        // C2 không parse được dòng request: method rỗng, target là chuỗi gốc, status "-".
        List<OutputRecord> garbage = detector.process("shop.local|10.0.4.1",
                enriched("10.0.4.1", "", "../../../etc/passwd", "", null, "-"));
        assertTrue(garbage.stream().anyMatch(o -> o.topic().equals("attack.detected")), garbage.toString());
        for (String[] line : List.of(new String[]{"CONNECT", "evil.example:443"},
                new String[]{"M-SEARCH", "*"}, new String[]{"GET", "http://evil.example/"})) {
            List<OutputRecord> out = detector.process("shop.local|10.0.4.2",
                    enriched("10.0.4.2", line[0], line[1], "", 400, "-"));
            assertTrue(out.stream().noneMatch(o -> o.topic().equals("waf.deadletter")), line[0] + " " + out);
        }
    }

    @Test
    void outputEventIdsAreStableAcrossReplay() throws Exception {
        String input = enriched("10.0.5.1", "/api", "id=1%20UNION%20SELECT%20x", 200);
        String first = processor(Role.DETECTION, Set.of("shop.local")).process("shop.local|10.0.5.1", input)
                .stream().filter(o -> o.topic().equals("attack.detected")).findFirst().orElseThrow().value();
        String replay = processor(Role.DETECTION, Set.of("shop.local")).process("shop.local|10.0.5.1", input)
                .stream().filter(o -> o.topic().equals("attack.detected")).findFirst().orElseThrow().value();
        assertEquals(json(first).get("event_id"), json(replay).get("event_id"));
    }

    @Test
    void ddosAlertIsKeyedByDomainNotByTriggeringIp() throws Exception {
        RecordProcessor ddos = processor(Role.DDOS, Set.of("shop.local"));
        List<OutputRecord> last = List.of();
        for (String ip : List.of("10.0.6.1", "10.0.6.2", "10.0.6.3"))
            last = ddos.process("shop.local", enriched(ip, "/", "", 200));
        assertEquals("distributed", data(json(last.get(0).value())).get("src_ip"));
    }
}
