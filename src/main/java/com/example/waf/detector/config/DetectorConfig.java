package com.example.waf.detector.config;

import com.example.waf.detector.util.EnvironmentUtils;

public record DetectorConfig(Request request, Ddos ddos, int maxFutureSkewSeconds) {

    // Khai báo record Request
    public record Request(int windowSeconds, int requestThreshold, int errorThreshold,
                          int distinctPathThreshold, int alertThreshold,
                          int cooldownSeconds, int maxTrackedClients) {}

    // Khai báo record DDOS
    public record Ddos(int windowSeconds, int requestThreshold, int uniqueIpThreshold,
                       int cooldownSeconds, int maxTrackedDomains) {}

    // Load env
    public static DetectorConfig fromEnvironment() {
        Request request = new Request(
                EnvironmentUtils.positiveInt("REQUEST_WINDOW_SECONDS", 60),
                EnvironmentUtils.positiveInt("REQUEST_THRESHOLD", 20),
                EnvironmentUtils.positiveInt("ERROR_THRESHOLD", 8),
                EnvironmentUtils.positiveInt("DISTINCT_PATH_THRESHOLD", 12),
                EnvironmentUtils.boundedInt("ALERT_SCORE_THRESHOLD", 70, 1, 100),
                EnvironmentUtils.positiveInt("RULE_ALERT_COOLDOWN_SECONDS", 15),
                EnvironmentUtils.positiveInt("MAX_TRACKED_CLIENTS", 100_000));
        Ddos ddos = new Ddos(
                EnvironmentUtils.positiveInt("DDOS_WINDOW_SECONDS", 10),
                EnvironmentUtils.positiveInt("DDOS_REQUESTS_THRESHOLD", 300),
                EnvironmentUtils.positiveInt("DDOS_UNIQUE_IPS_THRESHOLD", 20),
                EnvironmentUtils.positiveInt("DDOS_COOLDOWN_SECONDS", 10),
                EnvironmentUtils.positiveInt("MAX_TRACKED_DOMAINS", 10_000));
        return new DetectorConfig(request, ddos,
                EnvironmentUtils.positiveInt("MAX_FUTURE_SKEW_SECONDS", 300));
    }
}
