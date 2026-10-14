package com.example.waf.detector.enums;

import java.util.Locale;

/** Runtime processes that together form the D2 Detection Engine. */
public enum Role {
    DETECTION,
    RULE_MANAGEMENT,
    REPARTITION,
    DDOS;

    public static Role from(String value) {
        String normalized = value.trim().replace('-', '_').toUpperCase(Locale.ROOT);
        return switch (normalized) {
            case "DETECTION", "DETECTOR", "RULES", "D2", "D2_DETECTION" -> DETECTION;
            case "RULE_MANAGEMENT", "RULE_API", "D2_RULE_MANAGEMENT" -> RULE_MANAGEMENT;
            case "REPARTITION", "D2_REPARTITION" -> REPARTITION;
            case "DDOS", "D2_DDOS" -> DDOS;
            default -> throw new IllegalArgumentException("Unknown D2 role: " + value);
        };
    }
}
