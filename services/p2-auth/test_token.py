"""Test logic token P2 — gọi trực tiếp hàm, KHÔNG cần server.

Chạy: python /app/p2-auth/test_token.py  (thoát khác 0 nếu có test fail).
Bao phủ: token hợp lệ, sai chữ ký, hết hạn, thiếu token.
"""
import datetime

import tokens as tokens_mod

SECRET = "test-secret"
OTHER_SECRET = "other-secret"


def test_create_then_verify_ok():
    # Arrange + Act
    token, expires_in = tokens_mod.create_token(SECRET, "dashboard", ["admin"], 3600)
    claims = tokens_mod.verify_token(SECRET, token)
    # Assert
    assert claims["sub"] == "dashboard", claims
    assert claims["roles"] == ["admin"], claims
    assert expires_in == 3600, expires_in


def test_wrong_signature_rejected():
    token, _ = tokens_mod.create_token(SECRET, "dashboard", ["admin"], 3600)
    _expect_token_error(lambda: tokens_mod.verify_token(OTHER_SECRET, token),
                        "wrong signature")


def test_expired_token_rejected():
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)
    token, _ = tokens_mod.create_token(SECRET, "dashboard", ["admin"], 5, now=past)
    _expect_token_error(lambda: tokens_mod.verify_token(SECRET, token), "expired token")


def test_missing_token_rejected():
    _expect_token_error(lambda: tokens_mod.verify_token(SECRET, ""), "missing token")


def _expect_token_error(action, label):
    try:
        action()
    except tokens_mod.TokenError:
        return
    raise AssertionError(f"expected TokenError for {label}")


def _run() -> None:
    tests = [
        test_create_then_verify_ok,
        test_wrong_signature_rejected,
        test_expired_token_rejected,
        test_missing_token_rejected,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run()
