"""D3 — Chấm điểm bất thường theo cửa sổ trượt.

Giữ thống kê theo (domain, src_ip) trong cửa sổ WINDOW_SECONDS:
rule_hits (trọng số theo severity), request_count, error_count, tập attack_types.
GIỚI HẠN bộ nhớ bằng cách cap số IP theo dõi (LRU). KHÔNG chặn IP, chỉ chấm điểm.
"""
from collections import OrderedDict, deque

# Trọng số theo mức độ rule.
SEVERITY_WEIGHTS = {"low": 5, "medium": 15, "high": 30, "critical": 50}

WINDOW_SECONDS = 300
COOLDOWN_SECONDS = 60
MAX_TRACKED_KEYS = 10000
MAX_ALERT_KEYS = 10000
RATE_WINDOW_SECONDS = 60

# Thành phần tần suất và tỉ lệ lỗi (đóng góp phụ vào score).
FREQ_PER_REQUEST = 0.5
FREQ_CAP = 20.0
ERROR_PER_ERROR = 2.0
ERROR_CAP = 20.0
SCORE_MAX = 100

_ERROR_STATUS_MIN = 400


def severity_from_score(score):
    """Suy mức độ từ điểm tổng hợp."""
    if score >= 90:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


class _Window:
    """Thống kê trượt cho một (domain, src_ip)."""

    __slots__ = ("requests", "errors", "hits")

    def __init__(self):
        self.requests = deque()  # ts request
        self.errors = deque()    # ts request lỗi (status >= 400)
        self.hits = deque()      # (ts, weight, attack_type, rule_id)

    def prune(self, cutoff):
        while self.requests and self.requests[0] < cutoff:
            self.requests.popleft()
        while self.errors and self.errors[0] < cutoff:
            self.errors.popleft()
        while self.hits and self.hits[0][0] < cutoff:
            self.hits.popleft()


class Score:
    """Kết quả chấm điểm bất biến."""

    __slots__ = ("score", "severity", "signals", "attack_types", "rule_ids")

    def __init__(self, score, severity, signals, attack_types, rule_ids):
        self.score = score
        self.severity = severity
        self.signals = signals
        self.attack_types = attack_types
        self.rule_ids = rule_ids


class Scorer:
    def __init__(self, window=WINDOW_SECONDS, max_keys=MAX_TRACKED_KEYS,
                 cooldown=COOLDOWN_SECONDS):
        self._window = window
        self._max_keys = max_keys
        self._cooldown = cooldown
        self._stats = OrderedDict()   # (domain, src_ip) -> _Window
        self._last_alert = OrderedDict()  # src_ip -> ts

    def _touch(self, key):
        """Lấy _Window cho key, tạo mới nếu chưa có, cập nhật LRU và cap bộ nhớ."""
        window = self._stats.get(key)
        if window is None:
            window = _Window()
            self._stats[key] = window
            while len(self._stats) > self._max_keys:
                self._stats.popitem(last=False)  # bỏ key ít dùng nhất
        else:
            self._stats.move_to_end(key)
        return window

    def record_request(self, domain, src_ip, status, now):
        """Ghi nhận một request từ log.enriched."""
        window = self._touch((domain, src_ip))
        window.prune(now - self._window)
        window.requests.append(now)
        if isinstance(status, int) and status >= _ERROR_STATUS_MIN:
            window.errors.append(now)

    def record_attack(self, domain, src_ip, severity, attack_type, rule_id, now):
        """Ghi nhận một attack.detected (cộng trọng số theo severity)."""
        window = self._touch((domain, src_ip))
        window.prune(now - self._window)
        weight = SEVERITY_WEIGHTS.get(severity, SEVERITY_WEIGHTS["low"])
        window.hits.append((now, weight, attack_type, rule_id))

    def evaluate(self, domain, src_ip, now):
        """Tính score và các tín hiệu hiện tại cho (domain, src_ip)."""
        window = self._stats.get((domain, src_ip))
        if window is None:
            return Score(0, "low", _empty_signals(), (), ())
        window.prune(now - self._window)

        rule_weight = sum(h[1] for h in window.hits)
        req_count = len(window.requests)
        error_count = len(window.errors)
        error_rate = round(error_count / req_count, 4) if req_count else 0.0
        rate_cutoff = now - RATE_WINDOW_SECONDS
        req_rate_1m = sum(1 for ts in window.requests if ts >= rate_cutoff)

        freq_component = min(FREQ_CAP, req_rate_1m * FREQ_PER_REQUEST)
        error_component = min(ERROR_CAP, error_count * ERROR_PER_ERROR)
        score = int(min(SCORE_MAX, rule_weight + freq_component + error_component))

        attack_types = _ordered_unique(h[2] for h in window.hits)
        rule_ids = _ordered_unique(h[3] for h in window.hits)
        signals = {
            "rule_hits": len(window.hits),
            "req_count": req_count,
            "error_count": error_count,
            "error_rate": error_rate,
            "req_rate_1m": req_rate_1m,
        }
        return Score(score, severity_from_score(score), signals,
                     attack_types, rule_ids)

    def should_alert(self, src_ip, now):
        """True nếu qua cooldown theo src_ip (chưa đánh dấu là đã cảnh báo)."""
        last = self._last_alert.get(src_ip)
        return last is None or (now - last) >= self._cooldown

    def mark_alert(self, src_ip, now):
        self._last_alert[src_ip] = now
        self._last_alert.move_to_end(src_ip)
        while len(self._last_alert) > MAX_ALERT_KEYS:
            self._last_alert.popitem(last=False)


def _empty_signals():
    return {"rule_hits": 0, "req_count": 0, "error_count": 0,
            "error_rate": 0.0, "req_rate_1m": 0}


def _ordered_unique(items):
    """Loại trùng nhưng giữ thứ tự xuất hiện. Trả tuple bất biến."""
    seen = {}
    for item in items:
        if item is not None and item not in seen:
            seen[item] = None
    return tuple(seen.keys())
