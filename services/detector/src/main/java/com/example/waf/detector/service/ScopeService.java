package com.example.waf.detector.service;

/** Ràng buộc phạm vi (P1): chỉ xử lý log của domain đã được xác nhận trong scope. */
@FunctionalInterface
public interface ScopeService {
    boolean allowed(String domain);
}
