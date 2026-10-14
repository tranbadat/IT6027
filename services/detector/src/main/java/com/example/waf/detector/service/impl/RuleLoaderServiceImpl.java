package com.example.waf.detector.service.impl;

public final class RuleLoaderServiceImpl implements com.example.waf.detector.service.RuleLoaderService {
    @Override
    public com.example.waf.detector.model.RuleDocument load(java.io.InputStream input, String format) throws java.io.IOException {
        var mapper = format.equalsIgnoreCase("json")
                ? new com.fasterxml.jackson.databind.ObjectMapper()
                : new com.fasterxml.jackson.databind.ObjectMapper(new com.fasterxml.jackson.dataformat.yaml.YAMLFactory());
        mapper.configure(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, true);
        return mapper.readValue(input, com.example.waf.detector.model.RuleDocument.class);
    }
}
