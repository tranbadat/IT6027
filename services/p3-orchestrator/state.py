"""P3 — Trạng thái pipeline dùng chung giữa các thread (thread consumer, thread
publisher, thread HTTP). Mọi truy cập bọc trong Lock; snapshot/drain trả bản sao mới.
"""
import threading


class PipelineState:
    """Đếm event theo (domain, stage) và mốc thời gian event cuối, an toàn đa luồng."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counts = {}          # (domain, stage) -> tổng số đã nhận
        self._last_event_at = {}   # (domain, stage) -> ISO timestamp
        self._published = {}        # (domain, stage) -> tổng tại lần publish trước

    def record(self, domain, stage, when):
        """Ghi nhận 1 event: tăng bộ đếm và cập nhật thời điểm cuối."""
        key = (domain, stage)
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            if when:
                self._last_event_at[key] = when

    def drain_changes(self):
        """Trả [(domain, stage, processed)] có thay đổi kể từ lần publish trước
        và cập nhật mốc đã publish. processed = số event trong cửa sổ."""
        with self._lock:
            changes = []
            for (domain, stage), total in self._counts.items():
                delta = total - self._published.get((domain, stage), 0)
                if delta > 0:
                    changes.append((domain, stage, delta))
                    self._published[(domain, stage)] = total
            return changes

    def snapshot(self):
        """Bản sao trạng thái cho HTTP /pipelines (list dict, đã sắp xếp)."""
        with self._lock:
            return [
                {
                    "domain": domain,
                    "stage": stage,
                    "count": self._counts[(domain, stage)],
                    "last_event_at": self._last_event_at.get((domain, stage)),
                }
                for (domain, stage) in sorted(self._counts)
            ]
