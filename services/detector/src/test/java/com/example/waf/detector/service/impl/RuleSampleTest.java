package com.example.waf.detector.service.impl;

import com.example.waf.detector.model.RuleDefinition;
import com.example.waf.detector.model.RuleSampleMatch;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Thử một rule trên request mẫu (rule-api POST /rules/test, nút "Chạy kiểm thử" của dashboard). */
class RuleSampleTest {
    private static RuleDefinition rule(String pattern, List<String> targets, List<String> normalizers) {
        return new RuleDefinition("SQLI_TEST", "sql_injection", "test", true, targets, pattern, 85, normalizers);
    }

    @Test
    void reportsFieldValueAndPositionOfMatchInQuery() {
        Optional<RuleSampleMatch> match = RuleEngineServiceImpl.test(
                rule("(?i)or\\s+1\\s*=\\s*1", List.of("query"), List.of("url_decode")),
                "/api/users?id=1%20OR%201=1%20--");

        assertTrue(match.isPresent());
        assertEquals("query", match.get().field());
        assertEquals("OR 1=1", match.get().value());
        assertEquals(5, match.get().start());
        assertEquals(11, match.get().end());
    }

    @Test
    void appliesOnlyToTargetedFields() {
        assertTrue(RuleEngineServiceImpl.test(
                rule("(?i)union\\s+select", List.of("url"), List.of("url_decode")),
                "/p?id=1%20UNION%20SELECT%20pw").isEmpty());
        assertTrue(RuleEngineServiceImpl.test(
                rule("(?i)union\\s+select", List.of("url"), List.of("url_decode")),
                "/p/1%20UNION%20SELECT").isPresent());
    }

    @Test
    void noMatchOnBenignSample() {
        assertTrue(RuleEngineServiceImpl.test(
                rule("(?i)or\\s+1\\s*=\\s*1", List.of("url", "query"), List.of("url_decode")),
                "/search?q=laptop").isEmpty());
    }
}
