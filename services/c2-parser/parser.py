"""Parse định dạng waf_combined của Nginx/Apache (combined + host, rt, sid).

Tách riêng để unit test không cần RabbitMQ.
"""
import datetime
import re
from urllib.parse import unquote

# combined + '"$host" (rt=..|rt_us=..) sid="..."'
_LINE_RE = re.compile(
    r'^(?P<remote>\S+) '
    r'\S+ '                             # ident (bỏ qua)
    r'(?P<user>\S+) '
    r'\[(?P<time>[^\]]+)\] '
    r'"(?P<request>[^"]*)" '
    r'(?P<status>\d{3}|-) '
    r'(?P<bytes>\d+|-) '
    r'"(?P<referer>(?:[^"\\]|\\.)*)" '
    r'"(?P<ua>(?:[^"\\]|\\.)*)"'
    # Ba trường mở rộng của waf_combined là TUỲ CHỌN: log combined chuẩn của bất kỳ
    # Nginx/Apache nào (không có host/rt/sid) vẫn parse được -> "cắm vào là chạy".
    r'(?: "(?P<host>[^"]*)")?'
    r'(?: rt=(?P<rt>[\d.]+)| rt_us=(?P<rt_us>\d+))?'
    r'(?: sid="(?P<sid>[^"]*)")?'
    r'\s*$'
)

# "METHOD /path?query HTTP/1.1"
_REQUEST_RE = re.compile(r'^(?P<method>[A-Z!-~]+) (?P<target>\S+) (?P<protocol>HTTP/[\d.]+)$')

_DASH_FIELDS = ("referer", "user_agent", "session_id")


def _dash_to_empty(value: str) -> str:
    return "" if value == "-" else value


def _parse_time(raw: str) -> str | None:
    """time_local kiểu '16/Sep/2026:09:11:06 +0000' -> RFC 3339. None nếu không parse được."""
    try:
        dt = datetime.datetime.strptime(raw, "%d/%b/%Y:%H:%M:%S %z")
        return dt.isoformat()
    except ValueError:
        return None


def parse_line(raw: str, log_source: str) -> dict:
    """Trả về dict trường đã chuẩn hoá. Ném ValueError nếu dòng sai định dạng."""
    match = _LINE_RE.match(raw.rstrip("\n"))
    if match is None:
        raise ValueError("line does not match waf_combined format")
    parts = match.groupdict()

    request = parts["request"]
    req_match = _REQUEST_RE.match(request)
    if req_match is None:
        # Request lỗi (ví dụ client gửi rác): vẫn giữ lại để phân tích, không vứt cả dòng.
        method, target, protocol = "", request, ""
    else:
        method = req_match.group("method")
        target = req_match.group("target")
        protocol = req_match.group("protocol")

    path, _, query_string = target.partition("?")

    status = None if parts["status"] == "-" else int(parts["status"])
    body_bytes = 0 if parts["bytes"] == "-" else int(parts["bytes"])

    if parts.get("rt") is not None:
        request_time = float(parts["rt"])
    elif parts.get("rt_us") is not None:
        request_time = int(parts["rt_us"]) / 1_000_000.0
    else:
        request_time = None

    request_ts = _parse_time(parts["time"])

    return {
        "src_ip": parts["remote"],
        "remote_user": _dash_to_empty(parts["user"]),
        "method": method,
        "path": path,
        "query_string": query_string,
        "protocol": protocol,
        "status": status,
        "body_bytes": body_bytes,
        "referer": _dash_to_empty(parts["referer"]),
        "user_agent": _dash_to_empty(parts["ua"]),
        "host": parts.get("host") or "",  # rỗng nếu log combined chuẩn (không có "$host")
        "request_time": request_time,
        "session_id": _dash_to_empty(parts.get("sid") or ""),
        "request_ts": request_ts,
        "log_time_raw": parts["time"],
        "log_source": log_source,
        # Bản giải mã percent-encoding để bước phát hiện dùng trực tiếp.
        "path_decoded": unquote(path),
        "query_decoded": unquote(query_string),
    }
