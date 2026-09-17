"""Unit test cho validator D2. Chạy được KHÔNG cần RabbitMQ/HTTP.

Chạy: python /app/d2-rule-manager/test_validator.py
"""
import validator as validator_mod


def _valid_body(**overrides):
    body = {
        "id": "sqli-demo", "name": "Demo SQLi", "attack_type": "sql_injection",
        "severity": "high", "target": "query_decoded",
        "pattern": "(?i)union\\s+select",
    }
    body.update(overrides)
    return body


def test_valid_rule_passes():
    result = validator_mod.validate_rule(_valid_body())
    assert result.ok, result.errors
    assert result.rule["id"] == "sqli-demo"


def test_invalid_id_rejected():
    result = validator_mod.validate_rule(_valid_body(id="Bad_ID!"))
    assert not result.ok
    assert any("id must match" in e for e in result.errors)


def test_invalid_attack_type_rejected():
    result = validator_mod.validate_rule(_valid_body(attack_type="rce"))
    assert not result.ok


def test_invalid_severity_rejected():
    result = validator_mod.validate_rule(_valid_body(severity="urgent"))
    assert not result.ok


def test_invalid_target_rejected():
    result = validator_mod.validate_rule(_valid_body(target="cookie"))
    assert not result.ok


def test_uncompilable_pattern_rejected():
    result = validator_mod.validate_rule(_valid_body(pattern="(unclosed"))
    assert not result.ok
    assert any("compile" in e for e in result.errors)


def test_nested_quantifier_rejected():
    for evil in ("(.+)+", "(.*)*", "(a+)+$", "(a+)*"):
        result = validator_mod.validate_rule(_valid_body(pattern=evil))
        assert not result.ok, f"should reject {evil}"
        assert any("nested quantifier" in e or "timed out" in e
                   for e in result.errors)


def test_overlong_pattern_rejected():
    result = validator_mod.validate_rule(_valid_body(pattern="a" * 2100))
    assert not result.ok
    assert any("too long" in e for e in result.errors)


def test_missing_pattern_rejected():
    body = _valid_body()
    del body["pattern"]
    result = validator_mod.validate_rule(body)
    assert not result.ok


def test_non_dict_rejected():
    result = validator_mod.validate_rule("not-a-dict")
    assert not result.ok


def test_description_preserved():
    result = validator_mod.validate_rule(_valid_body(description="hello"))
    assert result.ok
    assert result.rule["description"] == "hello"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:
                failures += 1
                print("FAIL", name, "->", repr(exc))
    if failures:
        raise SystemExit(f"{failures} test(s) failed")
    print("ALL PASS")
