"""Hub đẩy sự kiện realtime cho SSE.

Thread consumer (kết nối bus riêng) đẩy event vào các asyncio.Queue của client qua
loop.call_soon_threadsafe — cách an toàn duy nhất để chạm vào asyncio từ thread khác.
"""
import asyncio
import threading

# Hàng đợi mỗi client có giới hạn: client chậm sẽ bị bỏ bớt item thay vì phình bộ nhớ.
_QUEUE_MAXSIZE = 1000


class StreamHub:
    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Gắn event loop của uvicorn (gọi trong startup, ở thread chính)."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        """Tạo queue cho một client SSE (gọi trong loop, khi mở kết nối stream)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def publish_threadsafe(self, item: dict) -> None:
        """Gọi từ thread consumer để đẩy item tới mọi client."""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._fanout, item)

    def _fanout(self, item: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                pass  # client chậm -> bỏ item này, không chặn cả hub
