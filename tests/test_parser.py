import pytest

from wafcollect.c2_parsing.parser import (
    decode_iteratively, parse_line, parse_log_time, unescape_log_string,
)

NGINX = ('203.0.113.9 - - [21/Sep/2026:10:15:30 +0700] "GET /search?q=1%27%20OR%20%271%27%3D%271&x=a+b HTTP/1.1" '
         '200 512 "https://ex.com/" "Mozilla/5.0 (X11)" rt=0.012 ck="PHPSESSID=abc123"')


def ok(line, **kw):
    r = parse_line(line, **kw)
    assert r.status in ("ok", "partial"), r
    return r.data


# ---------- định dạng chuẩn
def test_nginx_combined_extended():
    d = ok(NGINX)
    assert d["parse_status"] == "ok"
    assert d["client_ip"] == "203.0.113.9"
    assert d["method"] == "GET" and d["http_version"] == "HTTP/1.1"
    assert d["path"] == "/search"
    assert d["query_string"] == "q=1' OR '1'='1&x=a b"
    assert d["query_string_raw"] == "q=1%27%20OR%20%271%27%3D%271&x=a+b"
    assert d["query_params"] == [{"name": "q", "value": "1' OR '1'='1"}, {"name": "x", "value": "a b"}]
    assert d["status"] == 200 and d["response_bytes"] == 512
    assert d["headers"] == {"referer": "https://ex.com/", "user_agent": "Mozilla/5.0 (X11)", "cookie": "PHPSESSID=abc123"}
    assert d["response_time_ms"] == 12.0
    assert d["request_time"] == "2026-09-21T03:15:30Z"  # +0700 -> UTC
    assert d["request_time_source"] == "log"


def test_apache_common_no_referer_ua():
    d = ok('127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0" 200 2326')
    assert d["remote_user"] == "frank"
    assert d["headers"]["referer"] is None and d["headers"]["user_agent"] is None
    assert d["request_time"] == "2000-10-10T20:55:36Z"


def test_apache_vhost_prefix_and_rt_us_and_dash_size():
    d = ok('shop.example.com:443 2001:db8::1 - - [10/Oct/2000:13:55:36 +0000] "POST /login HTTP/2.0" 302 - "-" "curl/8" rt_us=1500')
    assert d["vhost"] == "shop.example.com:443"
    assert d["client_ip"] == "2001:db8::1"
    assert d["response_bytes"] == 0
    assert d["response_time_ms"] == 1.5


def test_ipv6_first_token_is_not_mistaken_for_vhost():
    d = ok('::1 - - [10/Oct/2000:13:55:36 +0000] "GET / HTTP/1.1" 200 5 "-" "-"')
    assert d["vhost"] is None and d["client_ip"] == "::1"


# ---------- URL encode
def test_double_and_triple_encoding_detected():
    d = ok('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET /a/%252e%252e/%252e%252e/etc/passwd HTTP/1.1" 404 0 "-" "-"')
    assert d["path"] == "/a/../../etc/passwd"
    assert d["path_raw"] == "/a/%252e%252e/%252e%252e/etc/passwd"
    assert d["decode_passes"] == 2 and d["double_encoded"] is True


def test_iterative_decode_counts():
    assert decode_iteratively("abc") == ("abc", 0)
    assert decode_iteratively("%2e") == (".", 1)
    assert decode_iteratively("%252e") == (".", 2)
    assert decode_iteratively("%25252e") == (".", 3)


def test_decode_loop_is_bounded():
    s = "%" + "25" * 50 + "41"
    _, passes = decode_iteratively(s)
    assert passes == 5  # MAX_DECODE_PASSES


def test_invalid_percent_sequences_do_not_crash():
    d = ok('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET /a%zz%4?x=%&y=%G1 HTTP/1.1" 400 0 "-" "-"')
    assert d["path"] == "/a%zz%4"
    assert d["query_params"] == [{"name": "x", "value": "%"}, {"name": "y", "value": "%G1"}]


def test_utf8_percent_and_hex_escapes_from_nginx():
    # nginx ghi byte không in được thành \xHH; %E2%9C%93 là dấu ✓ (UTF-8)
    d = ok(r'1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET /caf\xC3\xA9?q=%E2%9C%93 HTTP/1.1" 200 1 "-" "Bot\x22quoted\x22"')
    assert d["path"] == "/café"
    assert d["query_string"] == "q=✓"
    assert d["headers"]["user_agent"] == 'Bot"quoted"'


def test_null_byte_and_invalid_utf8_are_safe_for_json():
    import json
    d = ok('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET /a%00.php?x=%ff%fe HTTP/1.1" 200 1 "-" "-"')
    assert "\x00" in d["path"]
    json.dumps(d)  # không được ném lỗi


def test_plus_is_space_only_in_query_not_path():
    d = ok('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET /a+b?q=c+d HTTP/1.1" 200 1 "-" "-"')
    assert d["path"] == "/a+b" and d["query_string"] == "q=c d"


def test_escaped_quote_and_backslash_in_fields():
    d = ok(r'1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET / HTTP/1.1" 200 1 "http://x/\"y" "UA \\ end"')
    assert d["headers"]["referer"] == 'http://x/"y'
    assert d["headers"]["user_agent"] == "UA \\ end"


def test_many_query_params_are_capped():
    q = "&".join(f"p{i}=v" for i in range(500))
    r = parse_line(f'1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET /?{q} HTTP/1.1" 200 1 "-" "-"')
    assert len(r.data["query_params"]) == 200
    assert "query_params_truncated" in r.data["parse_warnings"]


def test_absolute_form_target():
    d = ok('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET http://victim.com/admin?x=1 HTTP/1.1" 404 1 "-" "-"')
    assert d["path"] == "/admin" and d["query_string"] == "x=1"
    assert d["url"] == "http://victim.com/admin?x=1"


# ---------- dòng lỗi định dạng
@pytest.mark.parametrize("line", ["", "   ", "hello world", "GET / HTTP/1.1", "1.2.3.4 - - [x] no request 200 1"])
def test_unparseable_lines_fail_cleanly(line):
    r = parse_line(line)
    assert r.status == "failed" and r.data is None and r.error


def test_tls_handshake_garbage_is_partial_not_dropped():
    r = parse_line(r'1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "\x16\x03\x01\x02\x00\x01\x00\x01\xFC\x03\x03" 400 150 "-" "-"')
    assert r.status == "partial"
    assert r.data["method"] is None and r.data["path"] is None
    assert r.data["status"] == 400 and "invalid_method" in r.data["parse_warnings"]
    assert r.data["request_line"].startswith("\x16\x03\x01")


def test_dash_request_line_from_408():
    r = parse_line('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "-" 408 0 "-" "-"')
    assert r.status == "partial" and "empty_request_line" in r.warnings


def test_http_0_9_style_request():
    d = ok('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET /old" 200 1 "-" "-"')
    assert d["method"] == "GET" and d["path"] == "/old" and d["http_version"] is None
    assert d["parse_status"] == "ok"


def test_spaces_inside_url_are_kept():
    d = ok('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET /a b/c?x=1 2 HTTP/1.1" 400 1 "-" "-"')
    assert d["url"] == "/a b/c?x=1 2" and "space_in_target" in d["parse_warnings"]


def test_invalid_timestamp_falls_back_to_collected_at():
    d = ok('1.2.3.4 - - [31/Feb/2026:99:99:99 +0000] "GET / HTTP/1.1" 200 1 "-" "-"', fallback_time="2026-09-21T03:00:00Z")
    assert d["request_time"] == "2026-09-21T03:00:00Z"
    assert d["request_time_source"] == "collected_at"
    assert "invalid_timestamp" in d["parse_warnings"]


def test_very_long_url_ok():
    d = ok('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET /' + "a" * 50000 + ' HTTP/1.1" 414 1 "-" "-"')
    assert len(d["path"]) == 50001


# ---------- nhiều dòng
def test_multiline_record_is_parsed_and_flagged():
    raw = ('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET /x HTTP/1.1" 200 1 "-" "UA"\n'
           "  continuation line from custom format")
    r = parse_line(raw)
    assert r.status == "ok" and "multiline_record" in r.data["parse_warnings"]
    assert r.data["path"] == "/x"


def test_newline_escaped_by_apache_in_user_agent():
    d = ok(r'1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET / HTTP/1.1" 200 1 "-" "line1\nline2"')
    assert d["headers"]["user_agent"] == "line1\nline2"


# ---------- tiện ích
def test_parse_log_time_locale_independent_and_strict():
    assert parse_log_time("10/Oct/2000:13:55:36 -0700") == "2000-10-10T20:55:36Z"
    assert parse_log_time("01/Jan/2026:00:30:00 +0100") == "2025-12-31T23:30:00Z"
    assert parse_log_time("10/Xyz/2000:13:55:36 +0000") is None
    assert parse_log_time("garbage") is None


def test_unescape_helpers():
    assert unescape_log_string(r"a\x41b") == "aAb"
    assert unescape_log_string(r"\"q\"") == '"q"'
    assert unescape_log_string(r"\xC3\xA9") == "é"
