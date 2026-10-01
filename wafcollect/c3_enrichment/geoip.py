"""Tra cứu GeoIP theo địa chỉ IP bằng database MaxMind (GeoLite2-City hoặc GeoLite2-Country, file .mmdb).

Database KHÔNG kèm trong repo (giấy phép MaxMind + dung lượng). Cách lấy: xem README mục GeoIP.
Không có database -> service vẫn chạy, trường geo.status = "disabled".
"""
from __future__ import annotations

import ipaddress
import logging
from collections import OrderedDict
from typing import Any, Optional

log = logging.getLogger("c3.geoip")


class GeoResolver:
    def __init__(self, db_path: Optional[str] = None, reader: Any = None, cache_size: int = 20000) -> None:
        self._reader = reader
        self._kind = "city"
        if reader is None and db_path:
            try:
                import geoip2.database

                self._reader = geoip2.database.Reader(db_path)
                dbtype = self._reader.metadata().database_type.lower()
                self._kind = "country" if "country" in dbtype else "city"
                log.info("nạp GeoIP database %s (%s)", db_path, dbtype)
            except Exception as e:  # noqa: BLE001
                log.error("không nạp được GeoIP database %s: %s. Tiếp tục không có geo.", db_path, e)
        elif reader is not None:
            self._kind = getattr(reader, "kind", "city")
        self._cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._cache_size = cache_size

    @property
    def enabled(self) -> bool:
        return self._reader is not None

    def lookup(self, ip: str) -> dict[str, Any]:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return {"status": "invalid"}
        if not addr.is_global:
            return {"status": "non_public"}  # private/loopback/link-local/reserved: không có vị trí địa lý
        if self._reader is None:
            return {"status": "disabled"}
        key = str(addr)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        result = self._query(key)
        self._cache[key] = result
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return result

    def _query(self, ip: str) -> dict[str, Any]:
        try:
            rec = self._reader.country(ip) if self._kind == "country" else self._reader.city(ip)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "AddressNotFoundError":
                return {"status": "not_found"}
            log.warning("lỗi tra GeoIP cho %s: %s", ip, e)
            return {"status": "error"}
        out: dict[str, Any] = {
            "status": "ok",
            "country_code": rec.country.iso_code,
            "country_name": rec.country.name,
        }
        if self._kind == "city":
            out["city"] = rec.city.name
            out["latitude"] = rec.location.latitude
            out["longitude"] = rec.location.longitude
        return out
