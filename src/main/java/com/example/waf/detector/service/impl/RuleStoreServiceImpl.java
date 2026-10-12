package com.example.waf.detector.service.impl;

import com.example.waf.detector.model.RuleDefinition;
import com.example.waf.detector.model.RuleDocument;
import com.example.waf.detector.service.RuleLoaderService;
import com.example.waf.detector.service.RuleStoreService;
import com.example.waf.detector.service.RuleValidationService;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Optional;

public final class RuleStoreServiceImpl implements RuleStoreService {
    private final Path path;
    private final ObjectMapper mapper = new ObjectMapper();
    private final RuleValidationService validator = new RuleValidationServiceImpl();
    private RuleDocument current;

    public RuleStoreServiceImpl(Path path) throws IOException {
        this.path = path;
        RuleLoaderService loader = new RuleLoaderServiceImpl();
        if (Files.exists(path)) try (InputStream input = Files.newInputStream(path)) {
            current = loader.load(input, "json");
        } else try (InputStream input = getClass().getResourceAsStream("/rules/default-rules.yaml")) {
            current = loader.load(input, "yaml");
            persist(current);
        }
        validator.validateAll(current.rules());
    }

    @Override public synchronized RuleDocument document() { return current; }

    @Override public synchronized Optional<RuleDefinition> find(String id) {
        return current.rules().stream().filter(rule -> rule.id().equals(id)).findFirst();
    }

    @Override public synchronized RuleDocument save(RuleDefinition rule) throws IOException {
        validator.validate(rule);
        ArrayList<RuleDefinition> next = new ArrayList<>(current.rules());
        next.removeIf(existing -> existing.id().equals(rule.id()));
        next.add(rule);
        next.sort(java.util.Comparator.comparing(RuleDefinition::id));
        return replace(RuleDocument.of(next));
    }

    @Override public synchronized RuleDocument delete(String id) throws IOException {
        ArrayList<RuleDefinition> next = new ArrayList<>(current.rules());
        if (!next.removeIf(rule -> rule.id().equals(id))) throw new IllegalArgumentException("Rule not found: " + id);
        return replace(RuleDocument.of(next));
    }

    private RuleDocument replace(RuleDocument document) throws IOException {
        validator.validateAll(document.rules());
        persist(document);
        current = document;
        return current;
    }

    private void persist(RuleDocument document) throws IOException {
        Path parent = path.toAbsolutePath().getParent();
        if (parent != null) Files.createDirectories(parent);
        Path temporary = path.resolveSibling(path.getFileName() + ".tmp");
        mapper.writerWithDefaultPrettyPrinter().writeValue(temporary.toFile(), document);
        try { Files.move(temporary, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE); }
        catch (java.nio.file.AtomicMoveNotSupportedException ignored) {
            Files.move(temporary, path, StandardCopyOption.REPLACE_EXISTING);
        }
    }
}
