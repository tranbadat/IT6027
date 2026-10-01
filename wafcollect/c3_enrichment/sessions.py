"""Gom phiên và tính tần suất request, bộ nhớ có giới hạn.

Phiên = (domain, IP, cookie phiên nếu có). Một phiên kết thúc khi im lặng quá `session_timeout`.
Tần suất tính theo IP bằng các bucket 1 giây (tối đa `window_s` bucket/IP) thay vì lưu từng timestamp.
Mọi cấu trúc đều bị chặn kích thước (LRU + dọn theo thời gian) để lượng log lớn không làm tràn RAM.
Dùng thời gian của sự kiện (request_time trong log) chứ không phải giờ hệ thống, để chạy lại log cũ vẫn đúng.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class _Session:
    session_id: str
    first_seen: float
    last_seen: float
    requests: int = 0
    errors: int = 0


class SessionTracker:
    def __init__(
        self,
        session_timeout: float = 1800.0,
        max_sessions: int = 50_000,
        max_ips: int = 100_000,
        window_s: int = 60,
        short_window_s: int = 10,
    ) -> None:
        self.session_timeout = session_timeout
        self.max_sessions = max_sessions
        self.max_ips = max_ips
        self.window_s = window_s
        self.short_window_s = short_window_s
        self._sessions: "OrderedDict[tuple[str, str, str], _Session]" = OrderedDict()
        # (domain, ip) -> deque([sec, total, err4xx, err5xx])
        self._ips: "OrderedDict[tuple[str, str], deque[list[int]]]" = OrderedDict()

    # ---- phiên
    @staticmethod
    def _sid(domain: str, ip: str, cookie_hash: str, first_seen: float) -> str:
        h = hashlib.sha256(f"{domain}|{ip}|{cookie_hash}|{int(first_seen)}".encode()).hexdigest()
        return "s-" + h[:16]

    def _touch_session(self, domain: str, ip: str, cookie_hash: str, ts: float, status: int) -> tuple[_Session, bool]:
        key = (domain, ip, cookie_hash)
        s = self._sessions.get(key)
        is_new = False
        if s is not None and ts - s.last_seen > self.session_timeout:
            s, is_new = None, True
        if s is None:
            s = _Session(self._sid(domain, ip, cookie_hash, ts), ts, ts)
            is_new = True
            self._sessions[key] = s
        s.last_seen = max(s.last_seen, ts)
        s.requests += 1
        if status >= 400:
            s.errors += 1
        self._sessions.move_to_end(key)
        self._evict_sessions(ts)
        return s, is_new

    def _evict_sessions(self, now: float) -> None:
        while self._sessions:
            k, s = next(iter(self._sessions.items()))
            if len(self._sessions) > self.max_sessions or now - s.last_seen > self.session_timeout:
                self._sessions.popitem(last=False)
            else:
                break

    # ---- tần suất theo IP
    def _touch_ip(self, domain: str, ip: str, ts: float, status: int) -> dict[str, Any]:
        key = (domain, ip)
        buckets = self._ips.get(key)
        if buckets is None:
            buckets = self._ips[key] = deque()
        sec = int(ts)
        newest = buckets[-1][0] if buckets else sec
        if sec >= newest - self.window_s:  # bỏ qua sự kiện quá muộn so với cửa sổ
            placed = False
            for b in reversed(buckets):
                if b[0] == sec:
                    b[1] += 1
                    b[2] += 400 <= status < 500
                    b[3] += status >= 500
                    placed = True
                    break
                if b[0] < sec:
                    break
            if not placed:
                nb = [sec, 1, int(400 <= status < 500), int(status >= 500)]
                if buckets and sec < buckets[-1][0]:
                    items = sorted(list(buckets) + [nb])
                    buckets.clear()
                    buckets.extend(items)
                else:
                    buckets.append(nb)
        newest = buckets[-1][0]
        while buckets and buckets[0][0] <= newest - self.window_s:
            buckets.popleft()
        self._ips.move_to_end(key)
        while len(self._ips) > self.max_ips:
            self._ips.popitem(last=False)

        total = sum(b[1] for b in buckets)
        e4 = sum(b[2] for b in buckets)
        e5 = sum(b[3] for b in buckets)
        short = sum(b[1] for b in buckets if b[0] > newest - self.short_window_s)
        return {
            "window_seconds": self.window_s,
            "requests_10s": short,
            "requests_60s": total,
            "errors_4xx_60s": e4,
            "errors_5xx_60s": e5,
            "error_ratio_60s": round((e4 + e5) / total, 4) if total else 0.0,
        }

    def observe(self, domain: str, ip: str, cookie_key: Optional[str], ts: float, status: int) -> dict[str, Any]:
        cookie_hash = hashlib.sha256(cookie_key.encode()).hexdigest()[:16] if cookie_key else ""
        sess, is_new = self._touch_session(domain, ip, cookie_hash, ts, status)
        freq = self._touch_ip(domain, ip, ts, status)
        return {
            "session": {
                "id": sess.session_id,
                "is_new": is_new,
                "key_basis": "ip+cookie" if cookie_key else "ip",
                "request_count": sess.requests,
                "error_count": sess.errors,
                "first_seen": _iso(sess.first_seen),
                "last_seen": _iso(sess.last_seen),
                "duration_s": round(sess.last_seen - sess.first_seen, 3),
            },
            "frequency": freq,
        }

    def sizes(self) -> dict[str, int]:
        return {"sessions": len(self._sessions), "ips": len(self._ips)}


def _iso(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


SESSION_COOKIE_NAMES = (
    "phpsessid", "jsessionid", "sessionid", "session", "sid", "connect.sid",
    "laravel_session", "asp.net_sessionid", "_session_id", "session_id",
)


def extract_session_cookie(cookie: Optional[str], names: tuple[str, ...] = SESSION_COOKIE_NAMES) -> Optional[str]:
    """Lấy giá trị cookie phiên.
    - Nếu chuỗi dạng header `a=1; PHPSESSID=xyz` -> tìm theo tên cookie phiên phổ biến.
    - Nếu chuỗi không có '=' (server chỉ log `$cookie_sessionid`) -> coi cả chuỗi là token phiên.
    Cookie theo dõi/quảng cáo bị bỏ qua vì đổi liên tục sẽ làm vỡ phiên."""
    if not cookie:
        return None
    if "=" not in cookie:
        return cookie
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name.lower() in names and value:
            return value
    return None
