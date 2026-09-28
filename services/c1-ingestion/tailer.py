"""Tail kiểu poll cho các file access log của Nginx/Apache.

Tách riêng khỏi main để unit test không cần RabbitMQ. Theo dõi mỗi file bằng
(inode, offset) và một buffer bytes cho phần dòng chưa hoàn chỉnh; chỉ phát ra
dòng đã kết thúc bằng '\\n'. Xử lý log rotation và truncation.
"""
import glob
import os
from dataclasses import dataclass
from typing import NamedTuple

from common import logging as log

# Chỉ nhận đúng file kết thúc bằng hậu tố này (bỏ qua *.access.log.1 đã rotate).
_SUFFIX = ".access.log"
# Chỉ quét hai thư mục con này; tên thư mục cũng là log_source.
_SOURCES = ("nginx", "apache")


class LineRecord(NamedTuple):
    """Một dòng log hoàn chỉnh kèm ngữ cảnh nguồn."""
    domain: str
    log_source: str
    log_path: str
    line: str


@dataclass
class _FileState:
    """Trạng thái theo dõi nội bộ cho một file (được cập nhật tại chỗ)."""
    domain: str
    log_source: str
    inode: int
    offset: int
    buf: bytes = b""


class LogTailer:
    """Poll thư mục log và trả về các dòng mới sau mỗi lần gọi poll()."""

    def __init__(self, log_dir: str, read_existing: bool = False,
                 domain_override: str = ""):
        self._log_dir = log_dir
        self._read_existing = read_existing
        # Khi cắm vào Nginx của một app cụ thể (file thường tên access.log), đặt
        # C1_DOMAIN để mọi dòng được gán đúng domain thay vì suy từ tên file.
        self._domain_override = domain_override or ""
        self._states: dict[str, _FileState] = {}
        self._initialized = False

    def poll(self) -> list[LineRecord]:
        """Đồng bộ danh sách file rồi đọc mọi dòng mới. Không bao giờ ném lỗi I/O ra ngoài."""
        is_initial = not self._initialized
        self._sync_files(is_initial)
        self._initialized = True

        records: list[LineRecord] = []
        for path in list(self._states):
            self._collect_from(path, records)
        return records

    def _collect_from(self, path: str, records: list[LineRecord]) -> None:
        state = self._states[path]
        lines = self._read_new_lines(path, state)
        if lines is None:  # file biến mất giữa chừng
            del self._states[path]
            log.info("stop tracking file", path=path)
            return
        for line in lines:
            if line:  # bỏ qua dòng rỗng
                records.append(LineRecord(state.domain, state.log_source, path, line))

    def _sync_files(self, is_initial: bool) -> None:
        found = self._discover()
        for path, (domain, log_source) in found.items():
            if path not in self._states:
                from_end = is_initial and not self._read_existing
                self._states[path] = self._new_state(path, domain, log_source, from_end)
        for path in list(self._states):
            if path not in found:
                del self._states[path]
                log.info("stop tracking file", path=path)

    def _discover(self) -> dict[str, tuple[str, str]]:
        """Trả về map path -> (domain, log_source) cho mọi *access.log hiện có.

        Khớp cả 'shop.local.access.log' lẫn 'access.log' mặc định của nhiều app
        (nhưng vẫn bỏ qua bản rotate như access.log.1). Domain suy từ tên file,
        hoặc lấy từ C1_DOMAIN nếu được đặt.
        """
        found: dict[str, tuple[str, str]] = {}
        for log_source in _SOURCES:
            pattern = os.path.join(self._log_dir, log_source, "*access.log")
            for path in glob.glob(pattern):
                domain = self._domain_for(os.path.basename(path))
                if domain:
                    found[path] = (domain, log_source)
                else:
                    log.warning("cannot derive domain; set C1_DOMAIN", path=path)
        return found

    def _domain_for(self, name: str) -> str:
        if self._domain_override:
            return self._domain_override
        if name.endswith(_SUFFIX):          # <domain>.access.log
            return name[: -len(_SUFFIX)]
        if name.endswith(".log"):           # access.log -> "access" (nên đặt C1_DOMAIN)
            return name[: -len(".log")]
        return name

    def _new_state(self, path: str, domain: str, log_source: str, from_end: bool) -> _FileState:
        inode, offset = 0, 0
        try:
            stat = os.stat(path)
            inode = stat.st_ino
            offset = stat.st_size if from_end else 0
        except OSError as exc:
            log.warning("cannot stat new file", path=path, error=str(exc))
        log.info("tracking file", path=path, domain=domain,
                 log_source=log_source, from_end=from_end)
        return _FileState(domain, log_source, inode, offset)

    def _read_new_lines(self, path: str, state: _FileState) -> list[str] | None:
        """Đọc byte mới từ offset -> EOF. None nếu file biến mất."""
        try:
            stat = os.stat(path)
        except OSError:
            return None

        if stat.st_ino != state.inode or stat.st_size < state.offset:
            # Rotation (đổi inode) hoặc truncate (kích thước < offset) -> mở lại từ đầu.
            state.inode = stat.st_ino
            state.offset = 0
            state.buf = b""

        if stat.st_size == state.offset:
            return []

        chunk = self._read_chunk(path, state.offset)
        if not chunk:
            return []
        state.offset += len(chunk)
        return self._split_lines(state, chunk)

    @staticmethod
    def _read_chunk(path: str, offset: int) -> bytes:
        try:
            with open(path, "rb") as handle:
                handle.seek(offset)
                return handle.read()
        except OSError as exc:
            log.warning("cannot read file", path=path, error=str(exc))
            return b""

    @staticmethod
    def _split_lines(state: _FileState, chunk: bytes) -> list[str]:
        data = state.buf + chunk
        parts = data.split(b"\n")
        state.buf = parts[-1]  # phần dở dang, giữ lại cho lần sau
        # Đọc utf-8, ký tự lỗi -> thay thế (theo spec).
        return [part.decode("utf-8", errors="replace") for part in parts[:-1]]
