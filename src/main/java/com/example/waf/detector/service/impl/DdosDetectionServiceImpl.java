package com.example.waf.detector.service.impl;

import com.example.waf.detector.config.DetectorConfig;
import com.example.waf.detector.model.DetectionResult;
import com.example.waf.detector.model.Event;
import com.example.waf.detector.service.DdosDetectionService;

import java.time.Clock;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;

public final class DdosDetectionServiceImpl implements DdosDetectionService {
    private static final class Bucket {
        int requests;
        final Map<String, Integer> ips = new HashMap<>();
    }

    private static final class Window {
        final TreeMap<Long, Bucket> seconds = new TreeMap<>();
        final Map<String, Integer> ips = new HashMap<>();
        long latestSecond = Long.MIN_VALUE;
        long lastAlertSecond = Long.MIN_VALUE;
        long lastProcessedMillis;
        int requests;
    }

    private final Map<String, Window> domains;
    private final DetectorConfig.Ddos config;
    private final Clock clock;
    private final long maxFutureSkewSeconds;
    private long processed;

    public DdosDetectionServiceImpl(DetectorConfig.Ddos config, Clock clock, int maxFutureSkewSeconds) {
        this.config = config;
        this.clock = clock;
        this.maxFutureSkewSeconds = maxFutureSkewSeconds;
        this.domains = new LinkedHashMap<>(64, 0.75f, true) {
            @Override
            protected boolean removeEldestEntry(Map.Entry<String, Window> eldest) {
                return size() > DdosDetectionServiceImpl.this.config.maxTrackedDomains();
            }
        };
    }

    @Override
    public Optional<DetectionResult> analyze(Event event) {
        long second = event.epochSecond();
        if (second > clock.instant().getEpochSecond() + maxFutureSkewSeconds)
            throw new IllegalArgumentException("timestamp exceeds allowed future clock skew");
        long processingMillis = clock.millis();
        Window window = domains.computeIfAbsent(event.domainKey(), ignored -> new Window());
        window.lastProcessedMillis = processingMillis;
        window.latestSecond = Math.max(window.latestSecond, second);
        long oldest = window.latestSecond - config.windowSeconds();
        expire(window, oldest);

        if (++processed % 1_000 == 0) {
            long inactiveBefore = processingMillis - config.windowSeconds() * 2_000L;
            domains.entrySet().removeIf(item -> item.getValue().lastProcessedMillis < inactiveBefore);
        }
        if (second <= oldest) return Optional.empty();

        Bucket bucket = window.seconds.computeIfAbsent(second, ignored -> new Bucket());
        bucket.requests++;
        bucket.ips.merge(event.clientIp(), 1, Integer::sum);
        window.requests++;
        window.ips.merge(event.clientIp(), 1, Integer::sum);
        if (window.requests < config.requestThreshold() || window.ips.size() < config.uniqueIpThreshold()
                || (window.lastAlertSecond != Long.MIN_VALUE
                && second - window.lastAlertSecond < config.cooldownSeconds())) return Optional.empty();
        window.lastAlertSecond = second;

        Map<String, Object> stats = new LinkedHashMap<>();
        stats.put("seconds", config.windowSeconds());
        stats.put("requests", window.requests);
        stats.put("unique_ips", window.ips.size());
        return Optional.of(new DetectionResult("ddos:" + event.eventId(), event.eventId(),
                event.timestamp(), clock.instant().toString(), event.domain(), event.clientIp(),
                event.method(), event.path(), event.status(), 90, "high", true, false,
                List.of("DDoS"), List.of(), List.of("distributed_request_burst"), stats));
    }

    private static void expire(Window window, long oldest) {
        while (!window.seconds.isEmpty() && window.seconds.firstKey() <= oldest) {
            Bucket expired = window.seconds.pollFirstEntry().getValue();
            window.requests -= expired.requests;
            for (Map.Entry<String, Integer> entry : expired.ips.entrySet()) {
                window.ips.compute(entry.getKey(), (ip, count) ->
                        count == null || count <= entry.getValue() ? null : count - entry.getValue());
            }
        }
    }
}
