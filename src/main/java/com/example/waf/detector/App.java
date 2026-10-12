package com.example.waf.detector;

import com.example.waf.detector.api.RuleApiServer;
import com.example.waf.detector.enums.Role;
import com.example.waf.detector.kafka.KafkaPipeline;

public final class App {
    public static void main(String[] args) {
        String selected = args.length == 0 ? System.getenv().getOrDefault("DETECTOR_ROLE", "detection") : args[0];
        Role role = Role.from(selected);
        try {
            if (role == Role.RULE_MANAGEMENT) new RuleApiServer().run();
            else new KafkaPipeline(role).run();
        } catch (Exception failure) {
            throw new IllegalStateException("Cannot start role " + role, failure);
        }
    }
}
