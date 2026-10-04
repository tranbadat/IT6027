package com.example.waf.detector.util;

public final class EnvironmentUtils {
    private EnvironmentUtils() {}

    public static String value(String name, String fallback) {
        return System.getenv().getOrDefault(name, fallback);
    }

    public static String required(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) throw new IllegalArgumentException("Missing " + name);
        return value;
    }

    public static int positiveInt(String name, int fallback) {
        return boundedInt(name, fallback, 1, Integer.MAX_VALUE);
    }

    public static int boundedInt(String name, int fallback, int minimum, int maximum) {
        int value;
        try {
            value = Integer.parseInt(value(name, String.valueOf(fallback)));
        } catch (NumberFormatException ex) {
            throw new IllegalArgumentException(name + " must be an integer", ex);
        }
        if (value < minimum || value > maximum)
            throw new IllegalArgumentException(name + " must be from " + minimum + " to " + maximum);
        return value;
    }
}
