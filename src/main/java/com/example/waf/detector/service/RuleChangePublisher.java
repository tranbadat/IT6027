package com.example.waf.detector.service;

import com.example.waf.detector.model.RuleDocument;

public interface RuleChangePublisher extends AutoCloseable {
    void publish(RuleDocument document);
    @Override void close();
}
