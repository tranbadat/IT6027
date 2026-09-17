"""Unit test cho parser. Chạy: python -m pytest, hoặc python test_parser.py"""
import parser as parser_mod

NGINX = ('172.23.0.1 - - [16/Sep/2026:09:11:06 +0000] '
         '"GET /api/products?id=1%27%20OR%20%271%27%3D%271 HTTP/1.1" 200 15 '
         '"-" "curl/8.7.1" "shop.local" rt=0.123 sid="demo-1"')

APACHE = ('172.23.0.1 - - [16/Sep/2026:09:20:40 +0000] '
          '"GET /api/x HTTP/1.1" 404 236 "-" "Mozilla/5.0" "blog.local" rt_us=863 sid="demo-2"')


def test_nginx_line():
    f = parser_mod.parse_line(NGINX, "nginx")
    assert f["method"] == "GET"
    assert f["path"] == "/api/products"
    assert f["query_string"] == "id=1%27%20OR%20%271%27%3D%271"
    assert "1' OR '1'='1" in f["query_decoded"]
    assert f["status"] == 200
    assert f["body_bytes"] == 15
    assert f["host"] == "shop.local"
    assert abs(f["request_time"] - 0.123) < 1e-9
    assert f["session_id"] == "demo-1"
    assert f["request_ts"] == "2026-09-16T09:11:06+00:00"


def test_apache_microseconds():
    f = parser_mod.parse_line(APACHE, "apache")
    assert f["status"] == 404
    assert abs(f["request_time"] - 0.000863) < 1e-9
    assert f["session_id"] == "demo-2"


def test_empty_fields_become_empty_string():
    line = ('10.0.0.1 - - [16/Sep/2026:09:11:06 +0000] "GET / HTTP/1.1" 200 0 '
            '"-" "-" "shop.local" rt=0.000 sid="-"')
    f = parser_mod.parse_line(line, "nginx")
    assert f["referer"] == ""
    assert f["user_agent"] == ""
    assert f["session_id"] == ""


def test_plain_combined_without_waf_fields():
    # Log combined CHUẨN của một Nginx/Apache bất kỳ (không có host/rt/sid).
    line = ('203.0.113.7 - - [17/Sep/2026:01:02:03 +0000] '
            '"GET /a?b=1%27%20OR%201=1 HTTP/1.1" 200 12 "-" "curl/8"')
    f = parser_mod.parse_line(line, "nginx")
    assert f["method"] == "GET"
    assert f["path"] == "/a"
    assert "1' OR 1=1" in f["query_decoded"]
    assert f["status"] == 200
    assert f["host"] == ""          # C2 sẽ điền domain từ envelope
    assert f["request_time"] is None
    assert f["session_id"] == ""


def test_malformed_raises():
    try:
        parser_mod.parse_line("not a log line", "nginx")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
