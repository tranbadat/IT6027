package com.example.waf.detector.service;

import com.example.waf.detector.model.DetectionResult;
import com.example.waf.detector.model.Event;

import java.util.Optional;

public interface DdosDetectionService {
    Optional<DetectionResult> analyze(Event event);
}
