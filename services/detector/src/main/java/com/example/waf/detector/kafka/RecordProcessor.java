package com.example.waf.detector.kafka;

import com.example.waf.detector.enums.Role;
import com.example.waf.detector.model.DetectionResult;
import com.example.waf.detector.model.Event;
import com.example.waf.detector.model.RuleMatch;
import com.example.waf.detector.service.DdosDetectionService;
import com.example.waf.detector.service.RequestDetectionService;
import com.example.waf.detector.service.ScopeService;
import com.example.waf.detector.util.EventMapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.time.Clock;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Xử lý một record đầu vào thành các record đầu ra, không phụ thuộc Kafka client (dễ test).
 * <ul>
 *   <li>DETECTION: log.enriched → attack.detected (mỗi rule khớp một message), anomaly.scored
 *       (khi có rule khớp hoặc điểm ≥ {@value #MIN_SCORE_TO_EMIT}), alert.triggered (khi vượt ngưỡng và
 *       qua cooldown).</li>
 *   <li>REPARTITION: giữ nguyên envelope, đổi key sang domain cho nhánh DDoS.</li>
 *   <li>DDOS: alert.triggered khi số request và số IP của một domain vượt ngưỡng.</li>
 * </ul>
 * Message sai contract hoặc lỗi xử lý bất ngờ đều vào dead-letter, không làm chết consumer.
 */
public final class RecordProcessor {
    /** Điểm tối thiểu để phát anomaly.scored khi không có rule khớp (mức medium trở lên). */
    static final int MIN_SCORE_TO_EMIT = 40;

    private final Role role;
    private final Topics topics;
    private final RequestDetectionService requestDetector;
    private final DdosDetectionService ddosDetector;
    private final ScopeService scope;
    private final Clock clock;
    private final int maxFutureSkewSeconds;
    private final int alertThreshold;
    private final EnvelopeFactory envelopes;
    private final ObjectMapper mapper = new ObjectMapper();

    public RecordProcessor(Role role, Topics topics, RequestDetectionService requestDetector,
                           DdosDetectionService ddosDetector, ScopeService scope, Clock clock,
                           int maxFutureSkewSeconds, int alertThreshold) {
        if (role == Role.RULE_MANAGEMENT)
            throw new IllegalArgumentException("RULE_MANAGEMENT does not consume events");
        this.role = role;
        this.topics = topics;
        this.requestDetector = requestDetector;
        this.ddosDetector = ddosDetector;
        this.scope = scope;
        this.clock = clock;
        this.maxFutureSkewSeconds = maxFutureSkewSeconds;
        this.alertThreshold = alertThreshold;
        this.envelopes = new EnvelopeFactory(clock);
    }

    public List<OutputRecord> process(String key, String value) {
        try {
            Map<String, Object> data = mapper.readValue(value, new TypeReference<>() {});
            Event event = EventMapper.from(data, clock, maxFutureSkewSeconds);
            String expectedKey = role == Role.DDOS ? event.domainKey() : event.clientKey();
            if (!expectedKey.equals(key))
                throw new IllegalArgumentException("Kafka key does not match input contract");
            if (!scope.allowed(event.domain())) {
                System.out.printf("skip_out_of_scope domain=%s role=%s%n", event.domain(), role);
                return List.of();
            }
            return switch (role) {
                case DETECTION -> detect(event);
                case REPARTITION -> List.of(new OutputRecord(topics.domainInput(), event.domainKey(), value));
                case DDOS -> ddos(event);
                case RULE_MANAGEMENT -> throw new IllegalStateException("unreachable");
            };
        } catch (JsonProcessingException | RuntimeException failure) {
            return List.of(deadLetter(key, value, failure));
        }
    }

    private List<OutputRecord> detect(Event event) throws JsonProcessingException {
        DetectionResult result = requestDetector.analyze(event);
        String key = event.clientKey();
        List<OutputRecord> out = new ArrayList<>();
        for (RuleMatch match : result.matchedRules())
            out.add(record(topics.attack(), key, envelopes.attackDetected(event, match)));
        if (!result.matchedRules().isEmpty() || result.score() >= MIN_SCORE_TO_EMIT)
            out.add(record(topics.scored(), key, envelopes.anomalyScored(event, result, alertThreshold)));
        if (result.alert())
            out.add(record(topics.alert(), key, envelopes.alertTriggered(event, result, alertThreshold)));
        return List.copyOf(out);
    }

    private List<OutputRecord> ddos(Event event) throws JsonProcessingException {
        Optional<DetectionResult> result = ddosDetector.analyze(event);
        if (result.isEmpty()) return List.of();
        return List.of(record(topics.alert(), event.domainKey(),
                envelopes.alertTriggered(event, result.get(), alertThreshold)));
    }

    private OutputRecord record(String topic, String key, Map<String, Object> envelope)
            throws JsonProcessingException {
        return new OutputRecord(topic, key, mapper.writeValueAsString(envelope));
    }

    private OutputRecord deadLetter(String key, String value, Exception failure) {
        String message = failure.getMessage() == null ? failure.getClass().getSimpleName() : failure.getMessage();
        return new OutputRecord(topics.deadLetter(), key, value == null ? "" : value,
                Map.of("error", message.substring(0, Math.min(500, message.length())),
                        "role", role.name().toLowerCase()));
    }
}
