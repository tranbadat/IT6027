package com.example.waf.detector.service.impl;

import com.example.waf.detector.model.Event;
import com.example.waf.detector.model.RuleMatch;
import com.example.waf.detector.service.RuleEngineService;
import com.example.waf.detector.util.RequestNormalizer;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

public final class RuleEngineServiceImpl implements RuleEngineService {
    private record Rule(String id, String type, int weight, Pattern pattern, boolean sqlCanonical) {
        Rule(String id, String type, int weight, String regex, boolean sqlCanonical) {
            this(id, type, weight, Pattern.compile(regex, Pattern.CASE_INSENSITIVE), sqlCanonical);
        }
    }

    private record Field(String name, String value) {}

    private final List<Rule> rules = List.of(
            new Rule("SQLI_UNION_SELECT", "SQLi", 85, "\\bunion\\s+(?:all\\s+)?select\\b", true),
            new Rule("SQLI_BOOLEAN", "SQLi", 80,
                    "(?:['\"`]\\s*)?\\b(?:or|and)\\s+(?:[0-9]+|['\"][^'\"]{0,32}['\"])\\s*=\\s*(?:[0-9]+|['\"][^'\"]{0,32}['\"])", true),
            new Rule("SQLI_TIME_FUNCTION", "SQLi", 85,
                    "\\b(?:sleep|benchmark|pg_sleep)\\s*\\(", true),
            new Rule("SQLI_WAITFOR", "SQLi", 85, "\\bwaitfor\\s+delay\\b", true),
            new Rule("SQLI_SCHEMA", "SQLi", 75,
                    "\\b(?:information_schema|pg_catalog|sysobjects)\\b", true),
            new Rule("SQLI_STACKED", "SQLi", 80,
                    ";\\s*(?:select|insert|update|delete|drop|alter|create)\\b", true),
            new Rule("XSS_SCRIPT", "XSS", 90, "<\\s*script\\b", false),
            new Rule("XSS_HANDLER", "XSS", 85, "\\bon[a-z]{3,32}\\s*=", false),
            new Rule("XSS_JAVASCRIPT", "XSS", 85, "\\bjavascript\\s*:", false),
            new Rule("XSS_DATA_HTML", "XSS", 85, "\\bdata\\s*:\\s*text/html", false),
            new Rule("XSS_SRCDOC", "XSS", 85, "\\bsrcdoc\\s*=", false),
            new Rule("TRAVERSAL_PARENT", "path_traversal", 85, "\\.\\.[/\\\\]", false),
            new Rule("TRAVERSAL_SENSITIVE", "path_traversal", 90,
                    "(?:/etc/(?:passwd|shadow|hosts)\\b|[a-z]:[/\\\\]windows[/\\\\](?:win\\.ini|system32)\\b)", false),
            new Rule("TRAVERSAL_NULL_BYTE", "path_traversal", 80, "\\x00", false)
    );

    @Override
    public List<RuleMatch> match(Event event) {
        List<Field> fields = List.of(
                new Field("path", RequestNormalizer.normalize(event.path(), false)),
                new Field("query", RequestNormalizer.normalize(event.query(), true)),
                new Field("body", RequestNormalizer.normalize(event.body(), true)));
        List<RuleMatch> matches = new ArrayList<>();
        for (Field field : fields) {
            String sqlValue = null;
            for (Rule rule : rules) {
                String value = field.value;
                if (rule.sqlCanonical) {
                    if (sqlValue == null) sqlValue = RequestNormalizer.normalizeSql(field.value);
                    value = sqlValue;
                }
                if (rule.pattern.matcher(value).find())
                    matches.add(new RuleMatch(rule.id, rule.type, rule.weight, field.name));
            }
        }
        return matches;
    }
}
