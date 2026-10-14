package com.example.waf.detector.service.impl;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** P1 Scope: kết quả được cache theo TTL, lỗi gọi Scope Service thì từ chối (fail closed). */
class HttpScopeServiceTest {
    private static final class MutableClock extends Clock {
        private Instant now = Instant.parse("2026-10-05T00:00:00Z");
        @Override public ZoneOffset getZone() { return ZoneOffset.UTC; }
        @Override public Clock withZone(java.time.ZoneId zone) { return this; }
        @Override public Instant instant() { return now; }
        void advance(Duration duration) { now = now.plus(duration); }
    }

    @Test
    void cachesAllowedDomainUntilTtlExpires() {
        AtomicInteger calls = new AtomicInteger();
        MutableClock clock = new MutableClock();
        HttpScopeService scope = new HttpScopeService(domain -> {
            calls.incrementAndGet();
            return new HttpScopeService.Answer(domain.equals("shop.local"), 60);
        }, clock);

        assertTrue(scope.allowed("shop.local"));
        assertTrue(scope.allowed("shop.local"));
        assertEquals(1, calls.get());
        clock.advance(Duration.ofSeconds(61));
        assertTrue(scope.allowed("shop.local"));
        assertEquals(2, calls.get());
        assertFalse(scope.allowed("internal.local"));
    }

    @Test
    void failsClosedWhenScopeServiceUnavailable() {
        HttpScopeService scope = new HttpScopeService(domain -> {
            throw new IOException("connection refused");
        }, new MutableClock());
        assertFalse(scope.allowed("shop.local"));
    }
}
