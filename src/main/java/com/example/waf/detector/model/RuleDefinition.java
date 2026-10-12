package com.example.waf.detector.model;

import java.util.List;

public record RuleDefinition(String id, String group, String description, boolean enabled,
                             List<String> targets, String pattern, int score,
                             List<String> normalizers) {
    public RuleDefinition {
        targets = targets == null ? List.of() : List.copyOf(targets);
        normalizers = normalizers == null ? List.of() : List.copyOf(normalizers);
    }
}
