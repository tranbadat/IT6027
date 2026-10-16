package com.example.waf.detector.model;

/**
 * Kết quả thử một rule trên request mẫu: trường khớp, đoạn khớp và vị trí [start, end) trong giá trị
 * của trường sau khi chuẩn hoá theo normalizers của rule.
 */
public record RuleSampleMatch(String field, String value, int start, int end) {
}
