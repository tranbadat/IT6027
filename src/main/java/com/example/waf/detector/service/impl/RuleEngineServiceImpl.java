package com.example.waf.detector.service.impl;

import com.example.waf.detector.model.Event;
import com.example.waf.detector.model.RuleDefinition;
import com.example.waf.detector.model.RuleDocument;
import com.example.waf.detector.model.RuleMatch;
import com.example.waf.detector.service.RuleEngineService;
import com.example.waf.detector.service.RuleLoaderService;
import com.example.waf.detector.service.RuleValidationService;
import com.example.waf.detector.util.RequestNormalizer;
import com.google.re2j.Pattern;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public final class RuleEngineServiceImpl implements RuleEngineService {
    private record CompiledRule(RuleDefinition definition, Pattern pattern) {}
    private record Field(String name, String value, boolean plusAsSpace) {}

    private final RuleValidationService validator;
    private volatile List<CompiledRule> rules;

    public RuleEngineServiceImpl() {
        this.validator = new RuleValidationServiceImpl();
        RuleLoaderService loader = new RuleLoaderServiceImpl();
        try (InputStream input = RuleEngineServiceImpl.class.getResourceAsStream("/rules/default-rules.yaml")) {
            if (input == null) throw new IllegalStateException("Missing /rules/default-rules.yaml");
            RuleDocument document = loader.load(input, "yaml");
            replaceRules(document.rules());
        } catch (IOException failure) {
            throw new IllegalStateException("Cannot load default rules", failure);
        }
    }

    @Override
    public void replaceRules(List<RuleDefinition> definitions) {
        validator.validateAll(definitions);
        List<CompiledRule> compiled = definitions.stream()
                .filter(RuleDefinition::enabled)
                .map(rule -> new CompiledRule(rule, Pattern.compile(rule.pattern(), Pattern.CASE_INSENSITIVE)))
                .toList();
        rules = List.copyOf(compiled);
        System.out.printf("rules_loaded=%d%n", rules.size());
    }

    @Override
    public List<RuleMatch> match(Event event) {
        List<Field> fields = new ArrayList<>();
        fields.add(new Field("url", event.path(), false));
        fields.add(new Field("query", event.query(), true));
        fields.add(new Field("body", event.body(), true));
        event.headers().forEach((name, value) -> fields.add(new Field("headers." + name, value, false)));

        List<RuleMatch> matches = new ArrayList<>();
        for (CompiledRule compiled : rules) {
            RuleDefinition rule = compiled.definition();
            for (Field field : fields) {
                String target = field.name().startsWith("headers.") ? "headers" : field.name();
                if (!containsIgnoreCase(rule.targets(), target)) continue;
                String value = normalize(field.value(), field.plusAsSpace(), rule.normalizers());
                if (compiled.pattern().matcher(value).find()) {
                    matches.add(new RuleMatch(rule.id(), rule.group(), rule.score(), field.name()));
                    break;
                }
            }
        }
        return List.copyOf(matches);
    }

    private static String normalize(String value, boolean plusAsSpace, List<String> normalizers) {
        String result = value == null ? "" : value;
        if (containsIgnoreCase(normalizers, "url_decode") || containsIgnoreCase(normalizers, "html_decode"))
            result = RequestNormalizer.normalize(result, plusAsSpace);
        if (containsIgnoreCase(normalizers, "sql_canonical")) result = RequestNormalizer.normalizeSql(result);
        return result;
    }

    private static boolean containsIgnoreCase(List<String> values, String expected) {
        return values.stream().anyMatch(value -> value.toLowerCase(Locale.ROOT).equals(expected));
    }
}
