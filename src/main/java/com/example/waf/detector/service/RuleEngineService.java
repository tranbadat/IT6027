package com.example.waf.detector.service;

import com.example.waf.detector.model.Event;
import com.example.waf.detector.model.RuleMatch;

import java.util.List;

public interface RuleEngineService {
    List<RuleMatch> match(Event event);
}
