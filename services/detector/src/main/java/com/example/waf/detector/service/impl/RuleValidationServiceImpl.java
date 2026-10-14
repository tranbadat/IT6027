package com.example.waf.detector.service.impl;

import com.example.waf.detector.model.RuleDefinition;
import com.example.waf.detector.service.RuleValidationService;
import com.google.re2j.Pattern;

import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

public final class RuleValidationServiceImpl implements RuleValidationService {
    private static final Set<String> TARGETS = Set.of("url", "query", "body", "headers", "user_agent", "referer");
    private static final Set<String> NORMALIZERS = Set.of("url_decode", "html_decode", "sql_canonical");

    @Override
    public void validate(RuleDefinition rule) {
        if (rule == null) throw new IllegalArgumentException("Rule is required");
        if (blank(rule.id()) || !rule.id().matches("[A-Z0-9_]{3,80}"))
            throw new IllegalArgumentException("Rule id must match [A-Z0-9_]{3,80}");
        if (blank(rule.group())) throw new IllegalArgumentException("Rule group is required");
        if (rule.score() < 1 || rule.score() > 100) throw new IllegalArgumentException("Rule score must be 1..100");
        if (rule.targets().isEmpty()) throw new IllegalArgumentException("At least one target is required");
        for (String target : rule.targets())
            if (!TARGETS.contains(target.toLowerCase(Locale.ROOT)))
                throw new IllegalArgumentException("Unsupported target: " + target);
        for (String normalizer : rule.normalizers())
            if (!NORMALIZERS.contains(normalizer.toLowerCase(Locale.ROOT)))
                throw new IllegalArgumentException("Unsupported normalizer: " + normalizer);
        if (blank(rule.pattern()) || rule.pattern().length() > 2_000)
            throw new IllegalArgumentException("Pattern is required and must not exceed 2000 characters");
        try { Pattern.compile(rule.pattern(), Pattern.CASE_INSENSITIVE); }
        catch (RuntimeException invalid) { throw new IllegalArgumentException("Invalid RE2 regex: " + invalid.getMessage(), invalid); }
    }

    @Override
    public void validateAll(List<RuleDefinition> rules) {
        if (rules == null || rules.isEmpty()) throw new IllegalArgumentException("At least one rule is required");
        Set<String> ids = new HashSet<>();
        for (RuleDefinition rule : rules) {
            validate(rule);
            if (!ids.add(rule.id())) throw new IllegalArgumentException("Duplicate rule id: " + rule.id());
        }
    }

    private static boolean blank(String value) { return value == null || value.isBlank(); }
}
