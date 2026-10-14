"""Unit test P1 Scope: gọi API qua TestClient với kết nối DB giả, KHÔNG cần Postgres.

Chạy: python test_scope.py (thoát khác 0 nếu có test fail).
Bao phủ: kiểm tra scope (cho phép, từ chối, domain ưu tiên hơn app, tham số sai) và
quản lý allowlist (thêm/cập nhật, suy target_type, kiểm tra đầu vào, xoá).
"""
from fastapi.testclient import TestClient

import main as main_mod


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Mô phỏng đúng các câu SQL mà main.py dùng trên bảng scope.allowlist."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})  # target -> (target_type, enabled, note)

    def execute(self, sql, params=()):
        if sql.startswith("SELECT enabled FROM scope.allowlist"):
            row = self.rows.get(params[0])
            return _Result([(row[1],)] if row else [])
        if sql.startswith("SELECT target, target_type, enabled, note"):
            return _Result([(t, *self.rows[t]) for t in sorted(self.rows)])
        if sql.startswith("INSERT INTO scope.allowlist (target, target_type, enabled, note)"):
            target, target_type, enabled, note = params
            self.rows[target] = (target_type, enabled, note)
            return _Result([])
        if sql.startswith("DELETE FROM scope.allowlist"):
            self.rows.pop(params[0], None)
            return _Result([])
        raise AssertionError(f"unexpected SQL: {sql}")


def _client(rows=None):
    conn = FakeConn(rows)
    main_mod._conn = conn
    return TestClient(main_mod.app), conn


def test_check_allowed_domain():
    client, _ = _client({"shop.local": ("domain", True, None)})
    body = client.get("/scope/check", params={"domain": "Shop.Local"}).json()
    assert body["allowed"] is True and body["target_type"] == "domain", body
    assert body["cache_ttl_seconds"] == main_mod.CACHE_TTL_SECONDS, body


def test_check_unknown_or_disabled_domain_is_denied():
    client, _ = _client({"old.local": ("domain", False, None)})
    assert client.get("/scope/check", params={"domain": "internal.local"}).json()["allowed"] is False
    assert client.get("/scope/check", params={"domain": "old.local"}).json()["allowed"] is False


def test_check_domain_takes_precedence_over_app():
    client, _ = _client({"shop-app": ("app", True, None)})
    body = client.get("/scope/check", params={"domain": "internal.local", "app": "shop-app"}).json()
    assert body["target"] == "internal.local" and body["allowed"] is False, body


def test_check_by_app_name():
    client, _ = _client({"shop-app": ("app", True, None)})
    body = client.get("/scope/check", params={"app": "shop-app"}).json()
    assert body["allowed"] is True and body["target_type"] == "app", body


def test_check_rejects_missing_or_invalid_name():
    client, _ = _client()
    for params in ({}, {"domain": "bad domain!"}, {"domain": "-leading.dash"}):
        resp = client.get("/scope/check", params=params)
        assert resp.status_code == 400, (params, resp.text)
        assert resp.json()["error"] == "invalid_request", resp.text


def test_allowlist_add_lowercases_and_infers_type():
    client, conn = _client()
    assert client.post("/scope/allowlist", json={"target": "MyApp.COM"}).json() == \
        {"target": "myapp.com", "target_type": "domain", "enabled": True}
    assert client.post("/scope/allowlist", json={"target": "billing", "enabled": False}).json() == \
        {"target": "billing", "target_type": "app", "enabled": False}
    items = client.get("/scope/allowlist").json()["items"]
    assert [i["target"] for i in items] == ["billing", "myapp.com"], items
    assert client.get("/scope/check", params={"domain": "myapp.com"}).json()["allowed"] is True


def test_allowlist_rejects_invalid_target_and_type():
    client, conn = _client()
    assert client.post("/scope/allowlist", json={"target": "bad target"}).status_code == 400
    assert client.post("/scope/allowlist", json={"target": "ok.local", "target_type": "x"}).status_code == 400
    assert conn.rows == {}, conn.rows


def test_allowlist_delete_is_idempotent():
    client, conn = _client({"shop.local": ("domain", True, None)})
    assert client.delete("/scope/allowlist/SHOP.LOCAL").json() == {"deleted": "shop.local"}
    assert client.delete("/scope/allowlist/shop.local").json() == {"deleted": "shop.local"}
    assert conn.rows == {}, conn.rows


def _run() -> None:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run()
