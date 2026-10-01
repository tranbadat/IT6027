"""Parser chuẩn hoá một dòng access log Nginx/Apache thành dict có cấu trúc.

Hỗ trợ:
  - combined log format (Nginx mặc định, Apache "combined")
  - common log format (Apache "common": không có referer/user-agent)
  - tiền tố vhost của Apache ("vhost_combined": `example.com:80 1.2.3.4 - - [...] ...`)
  - trường mở rộng ở cuối dòng, dạng key=value (xem docs/web-server-setup.md):
        rt=<giây>      thời gian xử lý (Nginx $request_time)
        rt_us=<µs>     thời gian xử lý (Apache %D)
        ck="<cookie>"  cookie phiên (Nginx $cookie_xxx / Apache %{xxx}C)

Nguyên tắc: KHÔNG bao giờ ném exception ra ngoài với dữ liệu xấu. Kết quả luôn là một trong ba
trạng thái: "ok", "partial" (tách được phần khung nhưng request line hỏng), "failed".
Request line hỏng vẫn được giữ lại (partial) vì scanner/tấn công hay gửi request sai chuẩn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import unquote_to_bytes

MAX_DECODE_PASSES = 5
MAX_QUERY_PARAMS = 200

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}

# Trường được nháy kép; bên trong có thể chứa `\"`, `\\`, `\xHH` do server escape.
_QUOTED = r'"(?:[^"\\]|\\.)*"'

_LINE_RE = re.compile(
    r"^(?:(?P<vhost>[A-Za-z0-9.\-]+:\d+) )?"          # Apache vhost_combined (tuỳ chọn)
    r"(?P<ip>\S+) (?P<ident>\S+) (?P<user>\S+) "
    r"\[(?P<time>[^\]]+)\] "
    rf"(?P<request>{_QUOTED}) "
    r"(?P<status>\d{3}) (?P<size>\d+|-)"
    rf"(?: (?P<referer>{_QUOTED}) (?P<ua>{_QUOTED}))?"  # combined; common thì không có
    r"(?P<extra>.*)$",
    re.DOTALL,  # cho phép dòng bị gộp nhiều dòng vật lý
)
_TIME_RE = re.compile(r"^(\d{1,2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})$")
_EXTRA_RT = re.compile(r"(?:^|\s)rt=(?P<v>\d+(?:\.\d+)?)(?=\s|$)")
_EXTRA_RT_US = re.compile(r"(?:^|\s)rt_us=(?P<v>\d+)(?=\s|$)")
_EXTRA_CK = re.compile(rf'(?:^|\s)ck=(?P<v>{_QUOTED})')
_METHOD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,31}$")
_ESCAPE_RE = re.compile(rb"\\(x[0-9A-Fa-f]{2}|.)", re.DOTALL)


@dataclass
class ParseResult:
    status: str  # "ok" | "partial" | "failed"
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- giải mã chuỗi


def unescape_log_string(s: str) -> str:
    """Bỏ lớp escape do web server thêm vào (`\\xHH`, `\\"`, `\\\\`, `\\n`, `\\t`)."""
    raw = s.encode("utf-8", "surrogateescape")

    def repl(m: "re.Match[bytes]") -> bytes:
        tok = m.group(1)
        if tok[:1] == b"x" and len(tok) == 3:
            return bytes([int(tok[1:], 16)])
        return {b"n": b"\n", b"t": b"\t", b"r": b"\r"}.get(tok, tok)

    return _ESCAPE_RE.sub(repl, raw).decode("utf-8", "replace")


def percent_decode(s: str, plus_as_space: bool = False) -> str:
    if plus_as_space:
        s = s.replace("+", " ")
    return unquote_to_bytes(s).decode("utf-8", "replace")


def decode_iteratively(s: str, plus_as_space: bool = False) -> tuple[str, int]:
    """Giải mã % lặp lại tới khi ổn định (bắt double/triple encoding kiểu %252e).
    Trả (chuỗi_đã_giải_mã, số_lần_giải_mã_có_thay_đổi)."""
    passes = 0
    cur = s
    while passes < MAX_DECODE_PASSES:
        nxt = percent_decode(cur, plus_as_space)
        if nxt == cur:
            break
        cur = nxt
        passes += 1
    return cur, passes


def _dash_to_none(v: Optional[str]) -> Optional[str]:
    return None if v is None or v == "-" else v


def _unquote_field(v: Optional[str]) -> Optional[str]:
    """Bỏ nháy kép bao quanh, unescape, `-` -> None."""
    if v is None:
        return None
    inner = v[1:-1] if len(v) >= 2 and v[0] == '"' and v[-1] == '"' else v
    return _dash_to_none(unescape_log_string(inner))


# ---------------------------------------------------------------- thời gian


def parse_log_time(s: str) -> Optional[str]:
    """`10/Oct/2000:13:55:36 -0700` -> `2000-10-10T20:55:36Z`. Không phụ thuộc locale."""
    m = _TIME_RE.match(s.strip())
    if not m:
        return None
    d, mon, y, hh, mm, ss, sign, tzh, tzm = m.groups()
    if mon.capitalize() not in _MONTHS:
        return None
    try:
        local = datetime(int(y), _MONTHS[mon.capitalize()], int(d), int(hh), int(mm), int(ss))
        offset = timedelta(hours=int(tzh), minutes=int(tzm)) * (1 if sign == "+" else -1)
    except ValueError:
        return None
    return (local - offset).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- request line / URL


def _split_request_line(req: str) -> tuple[Optional[str], Optional[str], Optional[str], list[str]]:
    """Tách `METHOD TARGET PROTO`. Chịu được khoảng trắng thừa trong URL và HTTP/0.9."""
    warns: list[str] = []
    if req in ("", "-"):
        return None, None, None, ["empty_request_line"]
    parts = req.split(" ")
    method = parts[0]
    if not _METHOD_RE.match(method):
        return None, None, None, ["invalid_method"]
    if len(parts) == 1:
        return method, None, None, ["missing_target"]
    if len(parts) == 2:
        return method, parts[1], None, ["http_0_9_or_missing_protocol"]
    proto = parts[-1]
    if proto.upper().startswith("HTTP/"):
        target = " ".join(parts[1:-1])
        if len(parts) > 3:
            warns.append("space_in_target")
    else:
        # phần tử cuối không phải "HTTP/x": coi toàn bộ phần còn lại là target
        return method, " ".join(parts[1:]), None, warns + ["missing_protocol"]
    return method, target, proto, warns


def _split_target(target: str) -> tuple[str, str]:
    """Trả (path_raw, query_raw). Xử lý absolute-form `http://host/p?q`."""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", target):
        rest = target.split("://", 1)[1]
        slash = rest.find("/")
        target = rest[slash:] if slash != -1 else "/"
    path, _, query = target.partition("?")
    return path, query


def _parse_query(query_raw: str) -> tuple[list[dict[str, str]], bool]:
    params: list[dict[str, str]] = []
    if not query_raw:
        return params, False
    truncated = False
    for chunk in query_raw.split("&"):
        if not chunk:
            continue
        if len(params) >= MAX_QUERY_PARAMS:
            truncated = True
            break
        name, _, value = chunk.partition("=")
        params.append({
            "name": decode_iteratively(name, plus_as_space=True)[0],
            "value": decode_iteratively(value, plus_as_space=True)[0],
        })
    return params, truncated


# ---------------------------------------------------------------- hàm chính


def parse_line(raw: str, fallback_time: Optional[str] = None) -> ParseResult:
    """Parse một bản ghi log. `fallback_time` (ISO, thường là collected_at) dùng khi
    timestamp trong log hỏng, để downstream luôn có `request_time`."""
    line = raw.rstrip("\r\n")
    if not line.strip():
        return ParseResult("failed", error="empty_line")

    m = _LINE_RE.match(line)
    if not m:
        return ParseResult("failed", error="unrecognized_format")

    warnings: list[str] = []
    status = "ok"

    # --- thời gian
    request_time = parse_log_time(m["time"])
    time_source = "log"
    if request_time is None:
        warnings.append("invalid_timestamp")
        request_time, time_source = fallback_time, "collected_at"

    # --- request line
    request_raw = m["request"][1:-1]
    request_line = unescape_log_string(request_raw)
    method, target_raw, proto, rl_warn = _split_request_line(request_raw)
    warnings.extend(rl_warn)
    if method is None or target_raw is None:
        status = "partial"

    url = path_raw = query_raw = None
    path = query_string = None
    params: list[dict[str, str]] = []
    decode_passes = 0
    if target_raw is not None:
        url = unescape_log_string(target_raw)
        path_raw, query_raw = _split_target(url)
        path, p1 = decode_iteratively(path_raw)
        query_string, p2 = decode_iteratively(query_raw, plus_as_space=True)
        decode_passes = max(p1, p2)
        params, truncated = _parse_query(query_raw)
        if truncated:
            warnings.append("query_params_truncated")

    # --- kích thước, referer, UA
    size = 0 if m["size"] == "-" else int(m["size"])
    referer = _unquote_field(m["referer"])
    user_agent = _unquote_field(m["ua"])

    # --- trường mở rộng
    extra = m["extra"] or ""
    response_time_ms: Optional[float] = None
    if (rt := _EXTRA_RT.search(extra)):
        response_time_ms = round(float(rt["v"]) * 1000, 3)
    elif (rt_us := _EXTRA_RT_US.search(extra)):
        response_time_ms = round(int(rt_us["v"]) / 1000, 3)
    cookie = _unquote_field(ck["v"]) if (ck := _EXTRA_CK.search(extra)) else None

    if "\n" in line:
        warnings.append("multiline_record")

    data: dict[str, Any] = {
        "parse_status": status,
        "parse_warnings": warnings,
        "vhost": m["vhost"],
        "client_ip": m["ip"],
        "remote_user": _dash_to_none(m["user"]),
        "request_time": request_time,
        "request_time_source": time_source,
        "request_line": request_line,
        "method": method,
        "url": url,
        "path": path,
        "path_raw": path_raw,
        "query_string": query_string,
        "query_string_raw": query_raw,
        "query_params": params,
        "http_version": proto,
        "status": int(m["status"]),
        "response_bytes": size,
        "response_time_ms": response_time_ms,
        "headers": {"referer": referer, "user_agent": user_agent, "cookie": cookie},
        "decode_passes": decode_passes,
        "double_encoded": decode_passes >= 2,
        "raw_line": line,
    }
    return ParseResult(status, data=data, warnings=warnings)
