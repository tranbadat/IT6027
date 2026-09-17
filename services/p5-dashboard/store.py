"""Tổng hợp dữ liệu dashboard trong bộ nhớ (có cap), an toàn đa luồng.

Consumer (thread nền) gọi record(); HTTP handler (thread chính) gọi summary().
Mọi getter trả bản sao để không lộ cấu trúc nội bộ ra ngoài.
"""
import collections
import threading

from common import envelope as envelope_mod

# Giới hạn bộ nhớ cho từng loại tổng hợp.
TRAFFIC_MINUTES = 60      # số phút traffic giữ lại cho mỗi domain
ALERTS_CAP = 200          # lịch sử cảnh báo mới nhất
STAGES_CAP = 50           # stage.completed gần nhất
RECENT_CAP = 100          # feed sự kiện gần nhất
COUNTER_CAP = 5000        # số key tối đa cho top attack/top ip
TOP_N = 10                # số mục trả về cho top attack/top ip

_REALTIME_EVENTS = ("attack.detected", "anomaly.scored", "alert.triggered", "log.enriched")


def _minute_bucket(env: dict) -> str:
    """Lấy mốc phút 'YYYY-MM-DDTHH:MM' từ request_ts hoặc timestamp envelope."""
    data = env.get("data") or {}
    ts = data.get("request_ts") or env.get("timestamp") or envelope_mod.now_rfc3339()
    return str(ts)[:16]


def _trim_counter(counter: collections.Counter) -> None:
    """Giữ counter dưới COUNTER_CAP key bằng cách bỏ các key nhỏ nhất (chống phình bộ nhớ)."""
    if len(counter) <= COUNTER_CAP:
        return
    for key, _ in counter.most_common()[COUNTER_CAP:]:
        del counter[key]


class AggregateStore:
    def __init__(self):
        self._traffic: dict[str, dict[str, int]] = {}
        self._attacks: collections.Counter = collections.Counter()
        self._ips: collections.Counter = collections.Counter()
        self._alerts: collections.deque = collections.deque(maxlen=ALERTS_CAP)
        self._stages: collections.deque = collections.deque(maxlen=STAGES_CAP)
        self._recent: collections.deque = collections.deque(maxlen=RECENT_CAP)
        self._lock = threading.Lock()

    def record(self, env: dict) -> None:
        """Cập nhật tổng hợp theo loại event. Bỏ qua event không quan tâm."""
        event = env.get("event")
        with self._lock:
            if event == "log.enriched":
                self._add_traffic(env)
            elif event == "attack.detected":
                self._add_attack(env)
            elif event == "alert.triggered":
                self._add_alert(env)
            elif event == "stage.completed":
                self._add_stage(env)
            if event in _REALTIME_EVENTS:
                self._recent.append(_compact_event(env))

    def _add_traffic(self, env: dict) -> None:
        domain = env.get("domain") or "unknown"
        minute = _minute_bucket(env)
        buckets = self._traffic.setdefault(domain, {})
        buckets[minute] = buckets.get(minute, 0) + 1
        if len(buckets) > TRAFFIC_MINUTES:
            for old in sorted(buckets)[:-TRAFFIC_MINUTES]:
                del buckets[old]

    def _add_attack(self, env: dict) -> None:
        data = env.get("data") or {}
        attack_type = data.get("attack_type") or "unknown"
        self._attacks[attack_type] += 1
        _trim_counter(self._attacks)
        src_ip = data.get("src_ip")
        if src_ip:
            self._ips[src_ip] += 1
            _trim_counter(self._ips)

    def _add_alert(self, env: dict) -> None:
        self._alerts.append(_compact_alert(env))

    def _add_stage(self, env: dict) -> None:
        data = env.get("data") or {}
        self._stages.append({
            "pipeline": data.get("pipeline"),
            "stage": data.get("stage"),
            "processed": data.get("processed"),
            "window_seconds": data.get("window_seconds"),
            "at": env.get("timestamp"),
        })

    def summary(self) -> dict:
        with self._lock:
            traffic = [
                {"domain": domain, "minute": minute, "count": count}
                for domain, buckets in self._traffic.items()
                for minute, count in sorted(buckets.items())
            ]
            top_attacks = [{"attack_type": k, "count": v} for k, v in self._attacks.most_common(TOP_N)]
            top_ips = [{"ip": k, "count": v} for k, v in self._ips.most_common(TOP_N)]
            alerts = list(reversed(self._alerts))
            stages = list(reversed(self._stages))
            recent = list(reversed(self._recent))
        return {
            "traffic": traffic,
            "top_attacks": top_attacks,
            "top_ips": top_ips,
            "alerts": alerts,
            "stages": stages,
            "recent": recent,
        }


def _compact_event(env: dict) -> dict:
    """Bản gọn của event để đẩy realtime / hiển thị feed."""
    return {
        "event": env.get("event"),
        "domain": env.get("domain"),
        "timestamp": env.get("timestamp"),
        "data": env.get("data") or {},
    }


def _compact_alert(env: dict) -> dict:
    data = env.get("data") or {}
    return {
        "domain": env.get("domain"),
        "src_ip": data.get("src_ip"),
        "url": data.get("url"),
        "attack_types": list(data.get("attack_types") or []),
        "severity": data.get("severity"),
        "score": data.get("score"),
        "threshold": data.get("threshold"),
        "reason": data.get("reason"),
        "triggered_at": env.get("timestamp"),
    }
