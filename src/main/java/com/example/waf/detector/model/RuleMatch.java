package com.example.waf.detector.model;

import java.util.LinkedHashMap;
import java.util.Map;

public record RuleMatch(String ruleId, String type, int weight, String field) {
    public Map<String, Object> toMap() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("rule_id", ruleId);
        result.put("type", type);
        result.put("weight", weight);
        result.put("field", field);
        return result;
    }
}
