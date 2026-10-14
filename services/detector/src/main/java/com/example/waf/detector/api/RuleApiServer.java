package com.example.waf.detector.api;

import com.example.waf.detector.kafka.KafkaRuleChangePublisher;
import com.example.waf.detector.model.RuleDefinition;
import com.example.waf.detector.model.RuleDocument;
import com.example.waf.detector.service.RuleChangePublisher;
import com.example.waf.detector.service.RuleStoreService;
import com.example.waf.detector.service.RuleValidationService;
import com.example.waf.detector.service.impl.RuleStoreServiceImpl;
import com.example.waf.detector.service.impl.RuleValidationServiceImpl;
import com.example.waf.detector.util.EnvironmentUtils;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Map;
import java.util.concurrent.Executors;

public final class RuleApiServer {
    private final ObjectMapper mapper = new ObjectMapper()
            .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, true);
    private final RuleStoreService store;
    private final RuleValidationService validator = new RuleValidationServiceImpl();
    private final RuleChangePublisher publisher;
    private final HttpServer server;
    private final Object mutationLock = new Object();

    public RuleApiServer() throws IOException {
        store = new RuleStoreServiceImpl(Path.of(EnvironmentUtils.value("RULE_STORE_PATH", "data/rules.json")));
        publisher = new KafkaRuleChangePublisher();
        int port = EnvironmentUtils.positiveInt("RULE_API_PORT", 8090);
        server = HttpServer.create(new InetSocketAddress(port), 0);
        server.createContext("/health", exchange -> json(exchange, 200, Map.of("status", "UP")));
        // Qua gateway (JWT) tiền tố /api bị bỏ nên nhận /rules; gọi trực tiếp trong mạng nội bộ dùng /api/rules.
        server.createContext("/rules", this::rules);
        server.createContext("/api/rules", this::rules);
        server.setExecutor(Executors.newCachedThreadPool());
    }

    public void run() {
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            server.stop(1);
            publisher.close();
        }));
        publisher.publish(store.document());
        server.start();
        System.out.printf("role=RULE_MANAGEMENT port=%d rules=%d%n",
                server.getAddress().getPort(), store.document().rules().size());
    }

    private void rules(HttpExchange exchange) throws IOException {
        try {
            String method = exchange.getRequestMethod();
            String base = exchange.getHttpContext().getPath();
            String path = exchange.getRequestURI().getPath();
            String id = path.length() > base.length() + 1 ? path.substring(base.length() + 1) : null;
            if ("GET".equals(method) && id == null) { json(exchange, 200, store.document()); return; }
            if ("GET".equals(method)) {
                json(exchange, 200, store.find(id).orElseThrow(() -> new NotFound("Rule not found: " + id))); return;
            }
            if ("POST".equals(method) && "validate".equals(id)) {
                RuleDefinition rule = readRule(exchange); validator.validate(rule);
                json(exchange, 200, Map.of("valid", true)); return;
            }
            if ("POST".equals(method) && id == null) {
                RuleDefinition rule = readRule(exchange);
                changed(exchange, mutate(() -> {
                    if (store.find(rule.id()).isPresent())
                        throw new IllegalArgumentException("Rule already exists: " + rule.id());
                    return store.save(rule);
                }), 201); return;
            }
            if ("PUT".equals(method) && id != null) {
                RuleDefinition rule = readRule(exchange);
                if (!id.equals(rule.id())) throw new IllegalArgumentException("Path id and body id must match");
                changed(exchange, mutate(() -> store.save(rule)), 200); return;
            }
            if ("DELETE".equals(method) && id != null) {
                changed(exchange, mutate(() -> {
                    if (store.find(id).isEmpty()) throw new NotFound("Rule not found: " + id);
                    return store.delete(id);
                }), 200); return;
            }
            json(exchange, 405, Map.of("error", "Method not allowed"));
        } catch (NotFound failure) { json(exchange, 404, Map.of("error", failure.getMessage())); }
        catch (SnapshotNotPublished failure) { json(exchange, 502, Map.of("error", failure.getMessage())); }
        catch (com.fasterxml.jackson.core.JsonProcessingException failure) {
            json(exchange, 400, Map.of("error", "Invalid rule JSON: " + failure.getOriginalMessage()));
        }
        catch (IllegalArgumentException failure) { json(exchange, 400, Map.of("error", failure.getMessage())); }
        catch (Exception failure) { json(exchange, 500, Map.of("error", "Rule operation failed")); }
    }

    @FunctionalInterface
    private interface Mutation { RuleDocument apply() throws IOException; }

    /** Ghi rule rồi phát snapshot trong cùng một khoá: detector luôn nhận snapshot mới nhất sau cùng. */
    private RuleDocument mutate(Mutation mutation) throws IOException {
        synchronized (mutationLock) {
            RuleDocument document = mutation.apply();
            try {
                publisher.publish(document);
            } catch (RuntimeException failure) {
                // Rule đã lưu vào rules.json; rule-api phát lại snapshot đầy đủ mỗi lần khởi động.
                throw new SnapshotNotPublished("Rule saved but snapshot was not published to Kafka; "
                        + "restart rule-api or retry: " + failure.getMessage());
            }
            return document;
        }
    }

    private RuleDefinition readRule(HttpExchange exchange) throws IOException {
        byte[] body = exchange.getRequestBody().readNBytes(1_000_001);
        if (body.length > 1_000_000) throw new IllegalArgumentException("Body exceeds 1 MB");
        return mapper.readValue(body, RuleDefinition.class);
    }

    private void changed(HttpExchange exchange, RuleDocument document, int status) throws IOException {
        json(exchange, status, document);
    }

    private void json(HttpExchange exchange, int status, Object value) throws IOException {
        byte[] body = mapper.writeValueAsBytes(value);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }

    private static final class NotFound extends RuntimeException { private NotFound(String message) { super(message); } }

    private static final class SnapshotNotPublished extends RuntimeException {
        private SnapshotNotPublished(String message) { super(message); }
    }
}
