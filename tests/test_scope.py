import threading

from wafcollect.common.scope import Decision, ScopeClient, StaticScope
from wafcollect.tools.mock_scope import make_server


def start(allowed):
    srv = make_server(allowed, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_allowed_and_denied():
    srv, url = start({"ok.example.com"})
    try:
        c = ScopeClient(url)
        assert c.check("ok.example.com") is Decision.ALLOWED
        assert c.check("evil.example.com") is Decision.DENIED
        assert c.check("") is Decision.DENIED
    finally:
        srv.shutdown()


def test_unreachable_is_unavailable_not_denied_and_not_cached_long():
    now = [0.0]
    c = ScopeClient("http://127.0.0.1:9", timeout=0.3, error_ttl=2.0, clock=lambda: now[0])
    assert c.check("a.com") is Decision.UNAVAILABLE
    # thông báo lỗi chỉ cache ngắn: sau error_ttl phải hỏi lại
    srv, url = start({"a.com"})
    try:
        c.base_url = url
        assert c.check("a.com") is Decision.UNAVAILABLE   # còn trong TTL lỗi
        now[0] = 3.0
        assert c.check("a.com") is Decision.ALLOWED
    finally:
        srv.shutdown()


def test_result_is_cached():
    srv, url = start({"a.com"})
    now = [0.0]
    c = ScopeClient(url, allow_ttl=60, clock=lambda: now[0])
    assert c.check("a.com") is Decision.ALLOWED
    srv.shutdown()   # tắt server: kết quả cache vẫn dùng được
    now[0] = 30.0
    assert c.check("a.com") is Decision.ALLOWED
    now[0] = 61.0
    assert c.check("a.com") is Decision.UNAVAILABLE


def test_malformed_response_is_unavailable(monkeypatch):
    c = ScopeClient("http://x")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(b'{"nope": 1}'))
    assert c._fetch("a.com") is Decision.UNAVAILABLE


def test_bearer_token_is_sent(monkeypatch):
    seen = {}

    def fake(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        return _Resp(b'{"allowed": true}')

    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert ScopeClient("http://x", token="T0K").check("a.com") is Decision.ALLOWED
    assert seen["auth"] == "Bearer T0K"


def test_static_scope():
    s = StaticScope({"a.com"})
    assert s.check("a.com") is Decision.ALLOWED and s.check("b.com") is Decision.DENIED


class _Resp:
    def __init__(self, body): self._b = body
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
