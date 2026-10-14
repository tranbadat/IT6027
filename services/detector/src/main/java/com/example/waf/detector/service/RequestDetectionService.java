package com.example.waf.detector.service;

import com.example.waf.detector.model.DetectionResult;
import com.example.waf.detector.model.Event;

public interface RequestDetectionService {
    DetectionResult analyze(Event event);
}
