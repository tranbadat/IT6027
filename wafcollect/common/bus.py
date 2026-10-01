"""Event bus.

Interface `EventBus` tách biệt với broker cụ thể. Người 3 sở hữu Event Bus của hệ thống;
nếu nhóm chọn broker khác Redis (RabbitMQ...), chỉ cần viết thêm một lớp con ở file này.

- RedisStreamBus: mỗi tên event là một Redis Stream, mỗi service là một consumer group.
  Chọn Streams thay vì Pub/Sub vì service consumer restart không làm mất event.
- InMemoryBus: đồng bộ, dùng cho unit test và chạy thử cả pipeline trong một process.
"""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

from . import events

log = logging.getLogger("bus")

Handler = Callable[[dict[str, Any]], None]


class RetryLater(Exception):
    """Handler báo "chưa xử lý được lúc này" (Scope Service/bus tạm thời không khả dụng).
    Message sẽ không bị ack và được thử lại."""


class BusUnavailable(RetryLater):
    """Không publish được lên broker."""


class EventBus(ABC):
    @abstractmethod
    def publish(self, envelope: dict[str, Any]) -> None:
        """Publish envelope lên kênh có tên envelope['event']. Ném BusUnavailable nếu lỗi."""

    @abstractmethod
    def subscribe(self, event: str, group: str, handler: Handler) -> None:
        """Đăng ký handler cho một event. `group` = tên service (mỗi group nhận đủ mọi message)."""

    @abstractmethod
    def run(self, stop: threading.Event) -> None:
        """Chặn cho tới khi stop được set, trong lúc đó dispatch message tới handler."""

    def close(self) -> None:  # pragma: no cover - mặc định không làm gì
        pass


class InMemoryBus(EventBus):
    def __init__(self) -> None:
        self._subs: dict[str, list[tuple[str, Handler]]] = {}
        self.published: list[dict[str, Any]] = []  # để test kiểm tra

    def publish(self, envelope: dict[str, Any]) -> None:
        self.published.append(envelope)
        for _group, handler in list(self._subs.get(envelope["event"], [])):
            handler(envelope)

    def subscribe(self, event: str, group: str, handler: Handler) -> None:
        self._subs.setdefault(event, []).append((group, handler))

    def run(self, stop: threading.Event) -> None:
        stop.wait()

    def of(self, event: str) -> list[dict[str, Any]]:
        return [e for e in self.published if e["event"] == event]


class RedisStreamBus(EventBus):
    def __init__(
        self,
        url: str | None = None,
        client: Any = None,
        prefix: str = "waf:events:",
        maxlen: int = 100_000,
        consumer: str | None = None,
        block_ms: int = 1000,
    ) -> None:
        import redis  # import trễ để unit test không bắt buộc có redis

        self._redis_mod = redis
        self.r = client or redis.Redis.from_url(url, decode_responses=True)
        self.prefix = prefix
        self.maxlen = maxlen
        # Tên consumer phải ổn định giữa các lần restart để đọc lại được message chưa ack.
        self.consumer = consumer or "default"
        self.block_ms = block_ms
        self._subs: list[tuple[str, str, Handler]] = []
        self._stop: threading.Event | None = None

    def _key(self, event: str) -> str:
        return f"{self.prefix}{event}"

    def publish(self, envelope: dict[str, Any]) -> None:
        try:
            self.r.xadd(
                self._key(envelope["event"]),
                {"payload": events.dumps(envelope)},
                maxlen=self.maxlen,
                approximate=True,
            )
        except self._redis_mod.RedisError as e:
            raise BusUnavailable(str(e)) from e

    def subscribe(self, event: str, group: str, handler: Handler) -> None:
        self._subs.append((event, group, handler))

    def _ensure_group(self, key: str, group: str) -> None:
        try:
            # id="0": consumer khởi động sau vẫn đọc được các event phát ra trước đó.
            self.r.xgroup_create(key, group, id="0", mkstream=True)
        except self._redis_mod.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def _dispatch(self, key: str, group: str, msg_id: str, fields: dict, handler: Handler) -> bool:
        """Trả True nếu đã xử lý xong (ack), False nếu bị dừng giữa chừng."""
        assert self._stop is not None
        try:
            envelope = events.loads(fields["payload"])
        except Exception:  # noqa: BLE001
            log.exception("bỏ qua message hỏng %s trong %s", msg_id, key)
            self.r.xack(key, group, msg_id)
            return True
        backoff = 0.5
        while True:
            try:
                handler(envelope)
                break
            except RetryLater as e:
                log.warning("handler chưa xử lý được (%s), thử lại sau %.1fs", e, backoff)
                if self._stop.wait(backoff):
                    return False  # để nguyên trạng thái pending, lần chạy sau sẽ đọc lại
                backoff = min(backoff * 2, 10.0)
            except Exception:  # noqa: BLE001
                log.exception("handler lỗi với message %s, bỏ qua để không kẹt pipeline", msg_id)
                break
        self.r.xack(key, group, msg_id)
        return True

    def _consume(self, event: str, group: str, handler: Handler) -> None:
        assert self._stop is not None
        key = self._key(event)
        self._ensure_group(key, group)
        pending_done = False
        while not self._stop.is_set():
            try:
                read_id = ">" if pending_done else "0"  # "0": đọc lại message pending của lần chạy trước
                resp = self.r.xreadgroup(
                    group, self.consumer, {key: read_id}, count=100,
                    block=None if not pending_done else self.block_ms,
                )
                batch = resp[0][1] if resp else []
                if not pending_done and not batch:
                    pending_done = True
                    continue
                for msg_id, fields in batch:
                    if not self._dispatch(key, group, msg_id, fields, handler):
                        return
            except self._redis_mod.RedisError as e:
                log.error("mất kết nối Redis (%s), thử lại sau 2s", e)
                if self._stop.wait(2.0):
                    return

    def run(self, stop: threading.Event) -> None:
        self._stop = stop
        threads = [
            threading.Thread(target=self._consume, args=s, name=f"consume-{s[0]}", daemon=True)
            for s in self._subs
        ]
        for t in threads:
            t.start()
        stop.wait()
        for t in threads:
            t.join(timeout=self.block_ms / 1000 + 2)

    def close(self) -> None:
        try:
            self.r.close()
        except Exception:  # noqa: BLE001
            pass


def create_bus(backend: str, redis_url: str = "", consumer: str | None = None) -> EventBus:
    if backend == "memory":
        return InMemoryBus()
    if backend == "redis":
        return RedisStreamBus(url=redis_url or "redis://localhost:6379/0", consumer=consumer)
    raise ValueError(f"BUS_BACKEND không hợp lệ: {backend!r} (redis|memory)")
