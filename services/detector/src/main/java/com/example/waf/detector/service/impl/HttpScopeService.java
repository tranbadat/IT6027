package com.example.waf.detector.service.impl;

import com.example.waf.detector.service.ScopeService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Gọi GET {SCOPE_SERVICE_URL}/scope/check?domain=... và cache theo cache_ttl_seconds.
 * Fail closed: không gọi được Scope Service thì coi là KHÔNG được phép (cache ngắn để thử lại).
 */
public final class HttpScopeService implements ScopeService {
    public record Answer(boolean allowed, int cacheTtlSeconds) {}

    @FunctionalInterface
    public interface Fetcher {
        Answer fetch(String domain) throws IOException, InterruptedException;
    }

    private record Cached(boolean allowed, Instant expiresAt) {}

    static final int FAILURE_TTL_SECONDS = 5;
    private static final Duration TIMEOUT = Duration.ofSeconds(3);

    private final Fetcher fetcher;
    private final Clock clock;
    private final Map<String, Cached> cache = new ConcurrentHashMap<>();

    public HttpScopeService(Fetcher fetcher, Clock clock) {
        this.fetcher = fetcher;
        this.clock = clock;
    }

    public static HttpScopeService forBaseUrl(String baseUrl, Clock clock) {
        String base = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        HttpClient client = HttpClient.newBuilder().connectTimeout(TIMEOUT).build();
        ObjectMapper mapper = new ObjectMapper();
        return new HttpScopeService(domain -> {
            HttpRequest request = HttpRequest.newBuilder(URI.create(base + "/scope/check?domain="
                            + URLEncoder.encode(domain, StandardCharsets.UTF_8)))
                    .timeout(TIMEOUT).GET().build();
            HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200)
                throw new IOException("scope service returned HTTP " + response.statusCode());
            JsonNode body = mapper.readTree(response.body());
            return new Answer(body.path("allowed").asBoolean(false), body.path("cache_ttl_seconds").asInt(60));
        }, clock);
    }

    @Override
    public boolean allowed(String domain) {
        String key = domain.toLowerCase(Locale.ROOT);
        Instant now = clock.instant();
        Cached cached = cache.get(key);
        if (cached != null && cached.expiresAt().isAfter(now)) return cached.allowed();
        try {
            Answer answer = fetcher.fetch(key);
            cache.put(key, new Cached(answer.allowed(), now.plusSeconds(Math.max(1, answer.cacheTtlSeconds()))));
            return answer.allowed();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return deny(key, now, interrupted);
        } catch (IOException | RuntimeException failure) {
            return deny(key, now, failure);
        }
    }

    private boolean deny(String key, Instant now, Exception failure) {
        System.err.printf("scope_check_failed domain=%s error=%s (fail closed)%n", key, failure.getMessage());
        cache.put(key, new Cached(false, now.plusSeconds(FAILURE_TTL_SECONDS)));
        return false;
    }
}
