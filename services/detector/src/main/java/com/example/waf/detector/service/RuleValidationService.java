package com.example.waf.detector.service;

import com.example.waf.detector.model.RuleDefinition;
import java.util.List;

public interface RuleValidationService {
    void validate(RuleDefinition rule);
    void validateAll(List<RuleDefinition> rules);
}
