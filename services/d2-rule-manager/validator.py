"""D2 — Kiểm tra rule trước khi lưu.

Validate schema (id, attack_type, severity, target, pattern) và CHẶN regex nguy hiểm:
- giới hạn độ dài pattern;
- heuristic phát hiện nested quantifier ((.+)+, (a+)+, (.*)* ...);
- chạy thử match trên chuỗi tấn công mẫu trong thread có timeout ~0.5s.
"""
import re
import threading

# Field hợp lệ (đồng bộ với D1 matcher.VALID_TARGETS).
VALID_TARGETS = frozenset({
    "path_decoded", "query_decoded", "path", "query_string",
    "user_agent", "referer", "url_decoded", "any",
})
VALID_ATTACK_TYPES = frozenset({
    "sql_injection", "xss", "path_traversal",     # đề tài (bắt buộc)
    "command_injection", "lfi", "rfi",            # mở rộng theo OWASP
    "sensitive_file", "log4shell", "ssti", "scanner",
})
VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
KNOWN_FIELDS = ("id", "name", "attack_type", "severity", "target",
                "pattern", "description")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_PATTERN_LEN = 2000
# Heuristic: nhóm chứa quantifier rồi lại bị quantify -> (x+)+, (.*)*, (a+)+ ...
_NESTED_QUANTIFIER_RE = re.compile(r"\([^)]*[+*][^)]*\)\s*[+*]")
_MATCH_TIMEOUT_SECONDS = 0.5
# Chuỗi ép backtracking để lộ regex ăn CPU.
_EVIL_SAMPLES = ("a" * 60 + "!", ("a1" * 40) + "\n" + "!" * 5)


class ValidationResult:
    """Kết quả validate bất biến."""

    __slots__ = ("ok", "errors", "rule")

    def __init__(self, ok, errors, rule):
        self.ok = ok
        self.errors = tuple(errors)
        self.rule = rule  # dict đã chuẩn hoá (None nếu lỗi)


def _match_within_timeout(pattern, sample, timeout):
    """Chạy pattern.search(sample) trong thread daemon. True nếu xong kịp giờ."""
    state = {"done": False}

    def _run():
        try:
            pattern.search(sample)
        except Exception:
            pass
        state["done"] = True

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    return state["done"]


def _check_dangerous_regex(pattern_str, errors):
    """Thêm lỗi vào errors nếu pattern có nguy cơ ReDoS. Trả regex đã compile hoặc None."""
    if len(pattern_str) > MAX_PATTERN_LEN:
        errors.append(f"pattern too long (> {MAX_PATTERN_LEN} chars)")
        return None
    if _NESTED_QUANTIFIER_RE.search(pattern_str):
        errors.append("pattern rejected: nested quantifier (possible ReDoS)")
        return None
    try:
        compiled = re.compile(pattern_str)
    except re.error as exc:
        errors.append(f"pattern does not compile: {exc}")
        return None
    for sample in _EVIL_SAMPLES:
        if not _match_within_timeout(compiled, sample, _MATCH_TIMEOUT_SECONDS):
            errors.append("pattern rejected: match timed out (possible ReDoS)")
            return None
    return compiled


def _check_schema(body, errors):
    """Kiểm tra các field schema, thêm lỗi vào errors."""
    rule_id = body.get("id")
    if not isinstance(rule_id, str) or not ID_RE.match(rule_id):
        errors.append("id must match ^[a-z0-9][a-z0-9-]{0,63}$")
    if not body.get("name"):
        errors.append("name is required")
    if body.get("attack_type") not in VALID_ATTACK_TYPES:
        errors.append(f"attack_type must be one of {sorted(VALID_ATTACK_TYPES)}")
    if body.get("severity") not in VALID_SEVERITIES:
        errors.append(f"severity must be one of {sorted(VALID_SEVERITIES)}")
    if body.get("target") not in VALID_TARGETS:
        errors.append(f"target must be one of {sorted(VALID_TARGETS)}")


def _normalized_rule(body):
    """Trả dict rule chỉ gồm field đã biết, thứ tự ổn định. Immutable."""
    rule = {
        "id": body["id"],
        "name": body["name"],
        "attack_type": body["attack_type"],
        "severity": body["severity"],
        "target": body["target"],
        "pattern": body["pattern"],
    }
    if body.get("description"):
        rule["description"] = str(body["description"])
    return rule


def validate_rule(body):
    """Validate một rule thô. Trả ValidationResult (không ném lỗi)."""
    errors = []
    if not isinstance(body, dict):
        return ValidationResult(False, ["request body must be a JSON object"], None)

    _check_schema(body, errors)
    pattern_str = body.get("pattern")
    if not isinstance(pattern_str, str) or pattern_str == "":
        errors.append("pattern is required and must be a non-empty string")
    else:
        _check_dangerous_regex(pattern_str, errors)

    if errors:
        return ValidationResult(False, errors, None)
    return ValidationResult(True, (), _normalized_rule(body))
