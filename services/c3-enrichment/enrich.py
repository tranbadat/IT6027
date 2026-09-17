"""Logic làm giàu cho C3: geoIP, đếm tần suất request, gom session.

Tách riêng khỏi main để unit test không cần RabbitMQ hay GeoIP DB thật.
"""
import ipaddress
import os
import time
from collections import OrderedDict, deque

import geoip2.database
import geoip2.errors

from common import logging as log

_WINDOW_SECONDS = 60
_MAX_TRACKED_IPS = 10000
_MAX_TIMESTAMPS_PER_IP = 1000


def _is_private_ip(src_ip: str) -> bool:
    """True nếu IP không định tuyến công cộng (private/loopback/... ) hoặc không hợp lệ."""
    try:
        ip = ipaddress.ip_address(src_ip)
    except ValueError:
        return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


class RequestRateCounter:
    """Đếm sliding-window 60s theo (domain, src_ip), giới hạn bộ nhớ kiểu LRU."""

    def __init__(self, window_seconds: int = _WINDOW_SECONDS,
                 max_ips: int = _MAX_TRACKED_IPS,
                 max_per_ip: int = _MAX_TIMESTAMPS_PER_IP):
        self._window = window_seconds
        self._max_ips = max_ips
        self._max_per_ip = max_per_ip
        self._hits: "OrderedDict[tuple[str, str], deque]" = OrderedDict()

    def record(self, domain: str, src_ip: str, now: float | None = None) -> int:
        """Ghi nhận một request và trả về số request của IP đó trong 60s gần nhất."""
        moment = time.time() if now is None else now
        key = (domain, src_ip)
        timestamps = self._hits.get(key)
        if timestamps is None:
            timestamps = deque()
            self._hits[key] = timestamps
        self._hits.move_to_end(key)  # đánh dấu vừa dùng (đuôi = mới nhất)
        timestamps.append(moment)
        self._trim_window(timestamps, moment)
        self._cap_per_ip(timestamps)
        self._cap_capacity()
        return len(timestamps)

    def _trim_window(self, timestamps: deque, now: float) -> None:
        cutoff = now - self._window
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

    def _cap_per_ip(self, timestamps: deque) -> None:
        while len(timestamps) > self._max_per_ip:
            timestamps.popleft()

    def _cap_capacity(self) -> None:
        while len(self._hits) > self._max_ips:
            self._hits.popitem(last=False)  # loại (domain, ip) cũ nhất


class GeoResolver:
    """Tra cứu geoIP, mở Reader một lần và tái dùng. Fail mềm về geo=null."""

    def __init__(self, db_path: str | None, reader=None):
        self._db_path = db_path
        self._reader = reader
        self._warned_missing = False
        if reader is None:
            self._open()

    def _open(self) -> None:
        if self._db_path and os.path.exists(self._db_path):
            try:
                self._reader = geoip2.database.Reader(self._db_path)
                log.info("geoip database loaded", path=self._db_path)
            except Exception as exc:  # file hỏng / sai định dạng
                log.error("cannot open geoip database", path=self._db_path, error=str(exc))
                self._reader = None

    def lookup(self, src_ip: str) -> dict | None:
        """Trả về {country_iso, country, city} hoặc None (IP private / không tra được / thiếu DB)."""
        if self._reader is None:
            self._warn_missing_once()
            return None
        if not src_ip or _is_private_ip(src_ip):
            return None
        try:
            resp = self._reader.city(src_ip)
        except geoip2.errors.AddressNotFoundError:
            return None
        except Exception as exc:  # IP sai định dạng hoặc lỗi reader
            log.warning("geoip lookup failed", ip=src_ip, error=str(exc))
            return None
        return {
            "country_iso": resp.country.iso_code,
            "country": resp.country.name,
            "city": resp.city.name,
        }

    def _warn_missing_once(self) -> None:
        if not self._warned_missing:
            log.warning("geoip database not available, geo=null", path=self._db_path)
            self._warned_missing = True


def resolve_session_id(data: dict) -> str:
    """Lấy session_id từ data; nếu rỗng thì tạo khoá phiên từ src_ip."""
    session_id = (data.get("session_id") or "").strip()
    if session_id:
        return session_id
    src_ip = (data.get("src_ip") or "").strip()
    return f"ip-{src_ip}" if src_ip else "ip-unknown"


def build_enriched_data(normalized: dict, geo: dict | None,
                        req_count_1m: int, session_id: str) -> dict:
    """Tạo data cho log.enriched. KHÔNG mutate dict đầu vào — luôn tạo dict mới."""
    enriched = dict(normalized)
    enriched["geo"] = geo
    enriched["req_count_1m"] = req_count_1m
    enriched["session_id"] = session_id
    return enriched
