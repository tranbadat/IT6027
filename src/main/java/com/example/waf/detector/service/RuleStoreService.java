package com.example.waf.detector.service;

import com.example.waf.detector.model.RuleDefinition;
import com.example.waf.detector.model.RuleDocument;

import java.io.IOException;
import java.util.Optional;

public interface RuleStoreService {
    RuleDocument document();
    Optional<RuleDefinition> find(String id);
    RuleDocument save(RuleDefinition rule) throws IOException;
    RuleDocument delete(String id) throws IOException;
}
