"""Theo dõi file log liên tục kiểu `tail -F`.

Dùng polling thay vì inotify vì inotify không đáng tin trên volume mount của Docker
(bind mount từ Windows/macOS, NFS...). Chu kỳ poll mặc định 0.25s nên độ trễ vẫn "gần tức thời".

Xử lý:
  - rotation kiểu rename + tạo file mới (logrotate mặc định, `nginx -s reopen`):
    đọc nốt file cũ tới EOF rồi mở file mới từ đầu -> không mất dòng nào ghi sát lúc rotate
  - rotation kiểu copytruncate: phát hiện kích thước nhỏ hơn offset -> đọc lại từ đầu.
    Giới hạn: nếu file bị cắt rồi ghi lại vượt quá offset cũ trong CHƯA TỚI một chu kỳ poll thì
    không phát hiện được (giới hạn chung của mọi tail dựa trên kích thước). Nên dùng rotation
    kiểu rename + reopen (`nginx -s reopen` / postrotate USR1) thay vì copytruncate.
  - file chưa tồn tại lúc khởi động hoặc bị xoá tạm thời giữa các lần rotate
  - dòng ghi dở (chưa có '\\n'): giữ trong buffer, chỉ phát ra khi đủ dòng
  - dòng dài bất thường: cắt ở max_line_bytes, bỏ phần dư tới hết dòng (chặn tốn RAM)
  - resume sau restart nhờ (inode, offset) lưu bởi StateStore
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger("c1.tailer")


@dataclass
class Line:
    text: str
    offset_after: int      # offset (trong file hiện tại) ngay sau dòng này
    inode: int
    truncated: bool = False
    line_count: int = 1    # >1 khi nhiều dòng vật lý được gộp thành một bản ghi


class StateStore:
    """Lưu (inode, offset) đã xử lý xong cho từng nguồn, ghi nguyên tử để không hỏng file khi crash."""

    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self._state: dict[str, dict[str, int]] = {}
        self._dirty = False
        self._last_flush = 0.0
        self._lock = threading.Lock()
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self._state = json.load(f)
            except (OSError, ValueError):
                log.warning("state file %s hỏng, bỏ qua và đọc lại từ đầu cấu hình", path)

    def get(self, name: str) -> Optional[dict[str, int]]:
        with self._lock:
            return self._state.get(name)

    def update(self, name: str, inode: int, offset: int) -> None:
        with self._lock:
            self._state[name] = {"inode": inode, "offset": offset}
            self._dirty = True

    def flush(self, force: bool = False, min_interval: float = 1.0) -> None:
        if not self.path:
            return
        now = time.monotonic()
        with self._lock:
            if not self._dirty or (not force and now - self._last_flush < min_interval):
                return
            payload = json.dumps(self._state)
            self._dirty = False
            self._last_flush = now
        d = os.path.dirname(self.path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".c1-state-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.path)
        except OSError:
            log.exception("không ghi được state file %s", self.path)
            try:
                os.unlink(tmp)
            except OSError:
                pass


class FileTailer:
    def __init__(
        self,
        path: str,
        start_at: str = "end",                  # "end" | "beginning" (chỉ áp dụng lần mở đầu tiên, không có state)
        resume: Optional[dict[str, int]] = None,  # {"inode":..,"offset":..} từ StateStore
        max_line_bytes: int = 65536,
        read_chunk: int = 1 << 20,
    ) -> None:
        if start_at not in ("end", "beginning"):
            raise ValueError("start_at phải là 'end' hoặc 'beginning'")
        self.path = path
        self.start_at = start_at
        self.max_line_bytes = max_line_bytes
        self.read_chunk = read_chunk
        self._resume = resume
        self._fh = None
        self._inode = 0
        self._attempted = False   # đã thử mở lần đầu chưa (dù thành công hay không)
        self._buf = b""
        self._buf_start = 0       # offset của byte đầu tiên trong _buf
        self._discarding = False  # đang bỏ phần dư của dòng quá dài

    def open(self) -> None:
        """Chốt vị trí bắt đầu NGAY (gọi lúc khởi động service) thay vì đợi lần poll đầu, để
        start_at="end" nghĩa là "từ thời điểm service khởi động" một cách xác định."""
        if self._fh is None and not self._attempted:
            self._open()

    # ---- mở / đóng
    def _open(self) -> bool:
        first = not self._attempted
        self._attempted = True
        try:
            fh = open(self.path, "rb")
        except (FileNotFoundError, PermissionError):
            return False
        st = os.fstat(fh.fileno())
        if first:
            pos = 0
            if self._resume and self._resume.get("inode") == st.st_ino and self._resume["offset"] <= st.st_size:
                pos = self._resume["offset"]
            elif self.start_at == "end" and not self._resume:
                pos = st.st_size
            # có state nhưng inode đã đổi (rotate lúc service tắt) -> đọc từ đầu file mới
        else:
            # file xuất hiện SAU lúc khởi động hoặc file mới sau rotation: toàn bộ là log mới
            pos = 0
        fh.seek(pos)
        self._fh, self._inode = fh, st.st_ino
        self._buf, self._buf_start, self._discarding = b"", pos, False
        log.info("mở %s (inode=%s, offset=%s)", self.path, st.st_ino, pos)
        return True

    def _close(self) -> None:
        if self._fh:
            self._fh.close()
        self._fh = None

    def close(self) -> None:
        self._close()

    # ---- đọc
    def _drain(self, out: list[Line]) -> None:
        assert self._fh is not None
        while True:
            chunk = self._fh.read(self.read_chunk)
            if not chunk:
                break
            self._consume(chunk, out)

    def _consume(self, chunk: bytes, out: list[Line]) -> None:
        self._buf += chunk
        while True:
            nl = self._buf.find(b"\n")
            if nl == -1:
                # Không có dòng hoàn chỉnh; chặn buffer phình vô hạn với dòng quá dài.
                if len(self._buf) > self.max_line_bytes and not self._discarding:
                    head = self._buf[: self.max_line_bytes]
                    consumed_to = self._buf_start + len(self._buf)
                    out.append(self._mk(head, consumed_to, truncated=True))
                    self._discarding = True
                    self._buf_start = consumed_to
                    self._buf = b""
                elif self._discarding:
                    self._buf_start += len(self._buf)
                    self._buf = b""
                return
            raw, self._buf = self._buf[:nl], self._buf[nl + 1:]
            end = self._buf_start + nl + 1
            self._buf_start = end
            if self._discarding:  # đây là phần đuôi của dòng đã bị cắt
                self._discarding = False
                continue
            truncated = len(raw) > self.max_line_bytes
            out.append(self._mk(raw[: self.max_line_bytes], end, truncated))

    def _mk(self, raw: bytes, offset_after: int, truncated: bool) -> Line:
        text = raw.rstrip(b"\r").decode("utf-8", "replace")
        return Line(text=text, offset_after=offset_after, inode=self._inode, truncated=truncated)

    def poll(self) -> list[Line]:
        """Trả các dòng mới hoàn chỉnh kể từ lần gọi trước (có thể rỗng)."""
        out: list[Line] = []
        if self._fh is None and not self._open():
            return out
        assert self._fh is not None
        self._drain(out)

        try:
            st_path = os.stat(self.path)
        except FileNotFoundError:
            return out  # file cũ bị đổi tên, file mới chưa xuất hiện: giữ handle cũ chờ tiếp
        st_fh = os.fstat(self._fh.fileno())

        if (st_path.st_ino, st_path.st_dev) != (st_fh.st_ino, st_fh.st_dev):
            # Rotation kiểu rename. Đọc nốt lần cuối (chống race), phát nốt dòng dở, rồi qua file mới.
            self._drain(out)
            if self._buf and not self._discarding:
                out.append(self._mk(self._buf, self._buf_start + len(self._buf), False))
            self._close()
            log.info("phát hiện rotation của %s, chuyển sang file mới", self.path)
            if self._open():  # đọc ngay file mới, không đợi thêm một chu kỳ poll
                self._drain(out)
        elif st_path.st_size < self._fh.tell():
            # copytruncate: file bị cắt ngắn tại chỗ
            log.info("phát hiện truncate của %s, đọc lại từ đầu", self.path)
            self._fh.seek(0)
            self._buf, self._buf_start, self._discarding = b"", 0, False
            self._drain(out)
        return out


class MultilineJoiner:
    """Gộp các dòng vật lý không khớp `start_pattern` vào bản ghi trước đó.
    Mặc định tắt: Nginx/Apache đều escape xuống dòng nên mỗi request đúng một dòng. Chỉ bật khi
    log format tuỳ biến có thể chèn xuống dòng."""

    DEFAULT_START = r"^\S+ \S+ \S+(?: \S+)? \["  # `ip - - [` hoặc `vhost:port ip - - [`

    def __init__(
        self,
        start_pattern: str = DEFAULT_START,
        flush_after: float = 1.0,
        max_lines: int = 50,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.start = re.compile(start_pattern)
        self.flush_after = flush_after
        self.max_lines = max_lines
        self._clock = clock
        self._cur: Optional[Line] = None
        self._last_feed = 0.0

    def feed(self, line: Line) -> list[Line]:
        out: list[Line] = []
        self._last_feed = self._clock()
        if self._cur is not None and not self.start.match(line.text) and self._cur.line_count < self.max_lines:
            self._cur.text += "\n" + line.text
            self._cur.offset_after = line.offset_after
            self._cur.line_count += 1
            self._cur.truncated = self._cur.truncated or line.truncated
            return out
        if self._cur is not None:
            out.append(self._cur)
        self._cur = Line(line.text, line.offset_after, line.inode, line.truncated, 1)
        return out

    def flush_if_stale(self) -> list[Line]:
        if self._cur is not None and self._clock() - self._last_feed >= self.flush_after:
            cur, self._cur = self._cur, None
            return [cur]
        return []

    def flush(self) -> list[Line]:
        cur, self._cur = self._cur, None
        return [cur] if cur else []
