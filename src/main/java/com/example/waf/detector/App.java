package com.example.waf.detector;

import com.example.waf.detector.kafka.KafkaPipeline;

import java.util.Locale;

public final class App {
    public static void main(String[] args) {
        String selected = args.length == 0 ? System.getenv().getOrDefault("DETECTOR_ROLE", "rules") : args[0];
        KafkaPipeline.Role role = KafkaPipeline.Role.valueOf(selected.toUpperCase(Locale.ROOT));
        new KafkaPipeline(role).run();
    }
}
