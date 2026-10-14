package com.example.waf.detector.service.impl;

import com.example.waf.detector.config.DetectorConfig;
import com.example.waf.detector.model.DetectionResult;
import com.example.waf.detector.model.Event;
import com.example.waf.detector.model.RuleMatch;
import com.example.waf.detector.service.RequestDetectionService;
import com.example.waf.detector.service.RuleEngineService;

import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Pattern;

public final class RequestDetectionServiceImpl implements RequestDetectionService {
    static final int CORROBORATION_BONUS = 10;
    /** Flood từ một nguồn: cộng thêm vào request_burst (30) để tự đạt ngưỡng cảnh báo mặc định 70. */
    static final int FLOOD_POINTS = 40;
    /**
     * Tệp tĩnh lành tính: vẫn tính vào số request nhưng KHÔNG tính vào số lỗi và số path khác nhau,
     * để một lần tải trang (CSS/JS/ảnh) hay favicon.ico trả 404 không bị coi là dò quét.
     */
    static final Pattern STATIC_ASSET = Pattern.compile(
            "\\.(?:ico|css|js|mjs|png|jpe?g|gif|svg|webp|woff2?|ttf|eot|map|txt)$", Pattern.CASE_INSENSITIVE);

    private static final class Bucket {
        int requests;
        int errors;
        final Map<String, Integer> paths = new HashMap<>();
    }

    private static final class Window {
        final TreeMap<Long, Bucket> buckets = new TreeMap<>();
        final Map<String, Integer> paths = new HashMap<>();
        long latestEventMillis = Long.MIN_VALUE;
        long lastProcessedMillis;
        long lastAlertSecond = Long.MIN_VALUE;
        int requests;
        int errors;
    }

    private final Map<String, Window> windows;
    private final RuleEngineService rules;
    private final DetectorConfig.Request config;
    private final Clock clock;
    private final long maxFutureSkewMillis;
    private long processed;

    public RequestDetectionServiceImpl(RuleEngineService rules, DetectorConfig.Request config,
                                       Clock clock, int maxFutureSkewSeconds) {
        this.rules = rules;
        this.config = config;
        this.clock = clock;
        this.maxFutureSkewMillis = maxFutureSkewSeconds * 1_000L;
        this.windows = new LinkedHashMap<>(256, 0.75f, true) {
            @Override
            protected boolean removeEldestEntry(Map.Entry<String, Window> eldest) {
                return size() > RequestDetectionServiceImpl.this.config.maxTrackedClients();
            }
        };
    }

    @Override
    public DetectionResult analyze(Event event) {
        long eventMillis = event.epochMilli();
        if (eventMillis > clock.millis() + maxFutureSkewMillis)
            throw new IllegalArgumentException("timestamp exceeds allowed future clock skew");

        List<RuleMatch> matches = rules.match(event);
        long processingMillis = clock.millis();
        Window window = windows.computeIfAbsent(event.clientKey(), ignored -> new Window());
        window.lastProcessedMillis = processingMillis;
        window.latestEventMillis = Math.max(window.latestEventMillis, eventMillis);
        long cutoff = window.latestEventMillis - config.windowSeconds() * 1_000L;
        expire(window, cutoff);

        List<String> reasons = new ArrayList<>();
        if (eventMillis > cutoff) add(window, eventMillis, event);
        else reasons.add("late_event_outside_window");

        if (++processed % 1_000 == 0) {
            long inactiveBefore = processingMillis - config.windowSeconds() * 2_000L;
            windows.entrySet().removeIf(item -> item.getValue().lastProcessedMillis < inactiveBefore);
        }

        // Điểm rule = trọng số lớn nhất, cộng thêm cho mỗi rule khớp thêm (bằng chứng củng cố nhau).
        int score = 0;
        Set<String> types = new HashSet<>();
        for (RuleMatch match : matches) {
            score = Math.max(score, match.weight());
            types.add(match.type());
        }
        if (matches.size() > 1) score += CORROBORATION_BONUS * (matches.size() - 1);
        if (window.requests >= config.requestThreshold()) {
            score += 30;
            reasons.add("request_burst");
        }
        if (window.requests >= config.floodThreshold()) {
            score += FLOOD_POINTS;
            reasons.add("request_flood");
        }
        if (window.errors >= config.errorThreshold()) {
            score += 25;
            reasons.add("http_error_burst");
        }
        if (window.paths.size() >= config.distinctPathThreshold()) {
            score += 25;
            reasons.add("path_scan");
        }
        score = Math.min(100, score);

        boolean qualifies = score >= config.alertThreshold();
        boolean suppressed = qualifies && window.lastAlertSecond != Long.MIN_VALUE
                && event.epochSecond() - window.lastAlertSecond < config.cooldownSeconds();
        boolean alert = qualifies && !suppressed;
        if (alert) window.lastAlertSecond = event.epochSecond();

        Map<String, Object> stats = new LinkedHashMap<>();
        stats.put("seconds", config.windowSeconds());
        stats.put("requests", window.requests);
        stats.put("errors", window.errors);
        stats.put("distinct_paths", window.paths.size());
        return new DetectionResult(event.eventId(), null, event.timestamp(), clock.instant().toString(),
                event.domain(), event.clientIp(), event.method(), event.path(), event.status(), score,
                DetectionResult.severity(score), alert, suppressed, types.stream().sorted().toList(),
                matches, reasons, stats);
    }

    private static void add(Window window, long eventMillis, Event event) {
        Bucket bucket = window.buckets.computeIfAbsent(eventMillis, ignored -> new Bucket());
        bucket.requests++;
        window.requests++;
        if (STATIC_ASSET.matcher(event.path()).find()) return;
        if (event.status() >= 400) {
            bucket.errors++;
            window.errors++;
        }
        bucket.paths.merge(event.path(), 1, Integer::sum);
        window.paths.merge(event.path(), 1, Integer::sum);
    }

    private static void expire(Window window, long cutoff) {
        while (!window.buckets.isEmpty() && window.buckets.firstKey() <= cutoff) {
            Bucket expired = window.buckets.pollFirstEntry().getValue();
            window.requests -= expired.requests;
            window.errors -= expired.errors;
            for (Map.Entry<String, Integer> entry : expired.paths.entrySet()) {
                window.paths.compute(entry.getKey(), (path, count) ->
                        count == null || count <= entry.getValue() ? null : count - entry.getValue());
            }
        }
    }
}
