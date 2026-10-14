package com.example.waf.detector.service.impl;

import com.example.waf.detector.config.DetectorConfig;
import com.example.waf.detector.enums.Role;
import com.example.waf.detector.model.DetectionResult;
import com.example.waf.detector.model.Event;
import com.example.waf.detector.model.RuleMatch;
import com.example.waf.detector.model.RuleDefinition;
import com.example.waf.detector.service.RuleEngineService;
import com.example.waf.detector.util.EventMapper;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DetectorCoreTest {
    private static final Clock CLOCK = Clock.fixed(
            Instant.parse("2026-10-05T00:00:00Z"), ZoneOffset.UTC);
    private static final DetectorConfig.Request REQUEST_CONFIG =
            new DetectorConfig.Request(60, 20, 8, 12, 70, 15, 10_000);
    private static final DetectorConfig.Ddos DDOS_CONFIG =
            new DetectorConfig.Ddos(10, 300, 20, 10, 1_000);

    @Test
    void mapsNewAndLegacyNamesToD2Roles() {
        assertEquals(Role.DETECTION, Role.from("detection"));
        assertEquals(Role.DETECTION, Role.from("rules"));
        assertEquals(Role.RULE_MANAGEMENT, Role.from("rule-management"));
        assertEquals(Role.RULE_MANAGEMENT, Role.from("rule-api"));
    }

    @Test
    void detectsEncodedAndCommentObfuscatedAttacks() {
        RuleEngineService rules = new RuleEngineServiceImpl();
        assertType(rules.match(event("s1", "2026-10-04T08:00:00Z", "1", "/products",
                "id=1%2520UNION%2520SELECT%2520x", 403)), "sql_injection");
        assertType(rules.match(event("s2", "2026-10-04T08:00:00Z", "1", "/products",
                "id=1%20UNION/**/SELECT%20x", 403)), "sql_injection");
        assertType(rules.match(event("x", "2026-10-04T08:00:00Z", "2", "/search",
                "q=%3Csvg%20onload%3Dalert(1)%3E", 200)), "xss");
        assertType(rules.match(event("p", "2026-10-04T08:00:00Z", "3", "/download",
                "file=..%2F..%2Fetc%2Fpasswd", 404)), "path_traversal");
    }

    @Test
    void doesNotMatchAcrossRequestFields() {
        RuleEngineService rules = new RuleEngineServiceImpl();
        List<RuleMatch> matches = rules.match(event("1", "2026-10-04T08:00:00Z", "1",
                "/union", "select", 200));
        assertTrue(matches.isEmpty());
    }

    @Test
    void detectsAttackInHeader() {
        Event event = new Event("header", "2026-10-04T08:00:00Z", "shop.example.test", "7",
                "GET", "/", "", "", "test", 200,
                Map.of("x-test", "<script>alert(1)</script>"));
        assertType(new RuleEngineServiceImpl().match(event), "xss");
    }

    @Test
    void rejectsRegexFeaturesOutsideLinearTimeRe2() {
        RuleDefinition rule = new RuleDefinition("BAD_BACKREF", "XSS", "unsafe", true,
                List.of("body"), "(a)\\\\1", 50, List.of());
        RuleDefinition lookahead = new RuleDefinition("BAD_LOOKAHEAD", "XSS", "unsafe", true,
                List.of("body"), "a(?=b)", 50, List.of());
        assertThrows(IllegalArgumentException.class, () -> new RuleValidationServiceImpl().validate(lookahead));
    }

    @Test
    void eventTimePreventsHistoricalReplayFromBecomingBurst() {
        RequestDetectionServiceImpl detector = requestDetector();
        DetectionResult result = null;
        for (int hour = 0; hour < 20; hour++) {
            result = detector.analyze(event("h" + hour,
                    String.format("2026-10-04T%02d:00:00Z", hour), "4",
                    "/missing/" + hour, "", 404));
        }
        assertFalse(result.alert());
        assertEquals(1, result.window().get("requests"));
    }

    @Test
    void rejectsTimestampThatCouldPoisonWindows() {
        RequestDetectionServiceImpl detector = requestDetector();
        Event future = event("future", "2030-01-01T00:00:00Z", "5", "/", "", 200);
        assertThrows(IllegalArgumentException.class, () -> detector.analyze(future));
        assertThrows(IllegalArgumentException.class,
                () -> new DdosDetectionServiceImpl(DDOS_CONFIG, CLOCK, 300).analyze(future));
    }

    @Test
    void suppressesRepeatedRequestAlertsDuringCooldown() {
        RequestDetectionServiceImpl detector = requestDetector();
        DetectionResult firstAlert = null;
        for (int index = 0; index < 20; index++) {
            firstAlert = detector.analyze(event("a" + index, "2026-10-04T08:00:00Z", "6",
                    "/missing/" + index, "", 404));
        }
        assertTrue(firstAlert.alert());
        DetectionResult repeated = detector.analyze(event("a21", "2026-10-04T08:00:01Z", "6",
                "/missing/21", "", 404));
        assertFalse(repeated.alert());
        assertTrue(repeated.alertSuppressed());
    }

    @Test
    void detectsDistributedDenialOfServiceAtThreshold() {
        DdosDetectionServiceImpl detector = new DdosDetectionServiceImpl(DDOS_CONFIG, CLOCK, 300);
        for (int index = 0; index < 299; index++) {
            assertTrue(detector.analyze(event("d" + index, "2026-10-04T08:00:00Z",
                    "203.0.113." + (index % 100 + 1), "/", "", 200)).isEmpty());
        }
        assertTrue(detector.analyze(event("d299", "2026-10-04T08:00:00Z",
                "203.0.113.100", "/", "", 200)).isPresent());
    }

    @Test
    void validatesFutureTimestampAtInputBoundary() {
        Map<String, Object> data = new HashMap<>();
        data.put("event_id", "future");
        data.put("timestamp", "2030-01-01T00:00:00Z");
        data.put("domain", "shop.example.test");
        data.put("client_ip", "192.0.2.1");
        data.put("method", "GET");
        data.put("path", "/");
        assertThrows(IllegalArgumentException.class, () -> EventMapper.from(data, CLOCK, 300));
    }

    private static RequestDetectionServiceImpl requestDetector() {
        return new RequestDetectionServiceImpl(new RuleEngineServiceImpl(), REQUEST_CONFIG, CLOCK, 300);
    }

    private static Event event(String id, String timestamp, String ip,
                               String path, String query, int status) {
        return new Event(id, timestamp, "shop.example.test", ip, "GET", path,
                query, "", "test", status);
    }

    private static void assertType(List<RuleMatch> matches, String type) {
        assertTrue(matches.stream().anyMatch(match -> match.type().equals(type)),
                () -> "Missing attack type " + type + " in " + matches);
    }

    @Test
    void staticAssetErrorsAndPathsDoNotLookLikeScanning() {
        RequestDetectionServiceImpl detector = requestDetector();
        DetectionResult result = null;
        String[] assets = {"/favicon.ico", "/robots.txt", "/app.js", "/app.css", "/logo.png", "/a.svg",
                "/b.woff2", "/c.jpg", "/d.gif", "/e.webp", "/f.map", "/g.mjs", "/h.ttf"};
        for (int index = 0; index < 26; index++)
            result = detector.analyze(event("s" + index, "2026-10-04T08:00:00Z", "9",
                    assets[index % assets.length], "", 404));
        assertEquals(0, result.window().get("errors"));
        assertEquals(0, result.window().get("distinct_paths"));
        assertFalse(result.alert());
    }

    @Test
    void singleSourceFloodWithoutPayloadOrErrorsRaisesAlert() {
        RequestDetectionServiceImpl detector = requestDetector();
        DetectionResult result = null;
        for (int index = 0; index < 99; index++)
            result = detector.analyze(event("f" + index, "2026-10-04T08:00:00Z", "11", "/", "", 200));
        assertFalse(result.alert(), "99 request/phút chưa phải flood");
        result = detector.analyze(event("f99", "2026-10-04T08:00:01Z", "11", "/", "", 200));
        assertTrue(result.alert(), result.toString());
        assertTrue(result.anomalyReasons().contains("request_flood"), result.toString());
    }
}
