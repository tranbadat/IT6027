package com.example.waf.detector.service;

import com.example.waf.detector.model.RuleDocument;
import java.io.IOException;
import java.io.InputStream;

public interface RuleLoaderService {
    RuleDocument load(InputStream input, String format) throws IOException;
}
