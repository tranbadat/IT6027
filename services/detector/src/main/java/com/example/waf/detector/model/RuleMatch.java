package com.example.waf.detector.model;

import java.util.LinkedHashMap;
import java.util.Map;

/** Một rule khớp: loại tấn công, trọng số, trường khớp và đoạn giá trị làm bằng chứng. */
public record RuleMatch(String ruleId, String type, int weight, String field,
                        String matchedValue, String description) {
    public static final int MAX_EVIDENCE_LENGTH = 200;

    public RuleMatch {
        matchedValue = matchedValue == null ? ""
                : matchedValue.substring(0, Math.min(MAX_EVIDENCE_LENGTH, matchedValue.length()));
        description = description == null ? "" : description;
    }

    public RuleMatch(String ruleId, String type, int weight, String field) {
        this(ruleId, type, weight, field, "", "");
    }

    public Map<String, Object> toMap() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("rule_id", ruleId);
        result.put("type", type);
        result.put("weight", weight);
        result.put("field", field);
        result.put("matched_value", matchedValue);
        return result;
    }
}
