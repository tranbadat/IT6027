package com.example.waf.detector.eval;

import com.example.waf.detector.config.DetectorConfig;
import com.example.waf.detector.enums.Role;
import com.example.waf.detector.kafka.OutputRecord;
import com.example.waf.detector.kafka.RecordProcessor;
import com.example.waf.detector.kafka.Topics;
import com.example.waf.detector.model.Event;
import com.example.waf.detector.model.RuleDefinition;
import com.example.waf.detector.model.RuleMatch;
import com.example.waf.detector.service.RuleEngineService;
import com.example.waf.detector.service.impl.DdosDetectionServiceImpl;
import com.example.waf.detector.service.impl.RequestDetectionServiceImpl;
import com.example.waf.detector.service.impl.RuleEngineServiceImpl;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Công cụ thực nghiệm (experiments/): phát lại các envelope log.enriched đã xuất (JSONL, mỗi dòng
 * một envelope) qua đúng {@link RecordProcessor} của detector ở ba cấu hình:
 * <ul>
 *   <li>A (RULES_ONLY): chỉ rule, ngưỡng tín hiệu hành vi đặt vô hạn;</li>
 *   <li>B (BEHAVIOR_ONLY): chỉ tín hiệu hành vi, bộ rule rỗng;</li>
 *   <li>C (COMBINED): cấu hình mặc định như khi chạy thật.</li>
 * </ul>
 * Cửa sổ trượt tính theo thời gian của request (request_ts) nên kết quả tái lập được.
 * Ghi ra {@code <outDir>/<A|B|C>.jsonl}: {"config", "topic", "envelope"} cho mỗi kết quả.
 *
 * <pre>java -cp app.jar com.example.waf.detector.eval.Evaluate enriched.jsonl out-dir</pre>
 */
public final class Evaluate {
    public enum Mode {
        RULES_ONLY("A"), BEHAVIOR_ONLY("B"), COMBINED("C");

        final String label;

        Mode(String label) { this.label = label; }
    }

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final int DISABLED = Integer.MAX_VALUE;
    private static final int MAX_FUTURE_SKEW_SECONDS = 300;

    private static final RuleEngineService NO_RULES = new RuleEngineService() {
        @Override public List<RuleMatch> match(Event event) { return List.of(); }
        @Override public void replaceRules(List<RuleDefinition> rules) { }
    };

    private Evaluate() {}

    public static void main(String[] args) throws IOException {
        if (args.length != 2) {
            System.err.println("usage: Evaluate <enriched.jsonl> <out-dir>");
            System.exit(2);
        }
        List<String> lines = Files.readAllLines(Path.of(args[0]), StandardCharsets.UTF_8).stream()
                .filter(line -> !line.isBlank()).toList();
        Path outDir = Path.of(args[1]);
        Files.createDirectories(outDir);
        for (Mode mode : Mode.values()) {
            List<String> outputs = replay(mode, lines);
            Files.write(outDir.resolve(mode.label + ".jsonl"), outputs, StandardCharsets.UTF_8);
            System.out.printf("config=%s input=%d output=%d%n", mode.label, lines.size(), outputs.size());
        }
    }

    /** Phát lại toàn bộ envelope theo thứ tự cho một cấu hình; trả các dòng JSON kết quả. */
    public static List<String> replay(Mode mode, List<String> envelopes) throws IOException {
        DetectorConfig defaults = DetectorConfig.fromEnvironment();
        DetectorConfig.Request request = defaults.request();
        if (mode == Mode.RULES_ONLY)
            request = new DetectorConfig.Request(request.windowSeconds(), DISABLED, DISABLED, DISABLED,
                    request.alertThreshold(), request.cooldownSeconds(), request.maxTrackedClients(), DISABLED);
        RuleEngineService rules = mode == Mode.BEHAVIOR_ONLY ? NO_RULES : new RuleEngineServiceImpl();
        Clock clock = Clock.systemUTC();
        RecordProcessor processor = new RecordProcessor(Role.DETECTION, Topics.fromEnvironment(),
                new RequestDetectionServiceImpl(rules, request, clock, MAX_FUTURE_SKEW_SECONDS),
                new DdosDetectionServiceImpl(defaults.ddos(), clock, MAX_FUTURE_SKEW_SECONDS),
                domain -> true, clock, MAX_FUTURE_SKEW_SECONDS, request.alertThreshold());

        List<String> results = new ArrayList<>();
        for (String envelope : envelopes) {
            JsonNode node = MAPPER.readTree(envelope);
            String key = node.path("domain").asText().toLowerCase(Locale.ROOT) + "|"
                    + node.path("data").path("src_ip").asText();
            for (OutputRecord output : processor.process(key, envelope)) {
                ObjectNode row = MAPPER.createObjectNode();
                row.put("config", mode.label);
                row.put("topic", output.topic());
                row.set("envelope", output.topic().equals(Topics.fromEnvironment().deadLetter())
                        ? MAPPER.getNodeFactory().textNode(output.headers().getOrDefault("error", ""))
                        : MAPPER.readTree(output.value()));
                results.add(MAPPER.writeValueAsString(row));
            }
        }
        return results;
    }
}
