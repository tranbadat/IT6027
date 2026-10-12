package com.example.waf.detector.model;

import java.util.List;

public record RuleDocument(int schemaVersion, List<RuleDefinition> rules) {
    public RuleDocument {
        rules = rules == null ? List.of() : List.copyOf(rules);
    }
    public static RuleDocument of(List<RuleDefinition> rules) { return new RuleDocument(1, rules); }
}
