"""D1 — Engine khớp mẫu tự viết cho log.enriched.

Nạp rule từ RULES_DIR/*.yaml (mỗi file là MỘT rule dạng mapping hoặc DANH SÁCH rule),
compile regex sẵn, và khớp trên các field ứng viên dựng từ log.enriched.data.
Rule lỗi -> log warning và bỏ qua, KHÔNG crash.
"""
import glob
import os
import re
import time

import yaml

from common import logging as log

# Field hợp lệ để khớp. url_decoded và "any" là field ảo (dựng lúc chạy).
VALID_TARGETS = (
    "path_decoded", "query_decoded", "path", "query_string",
    "user_agent", "referer", "url_decoded", "any",
)
# "any" khớp trên tập field này.
ANY_TARGETS = ("path_decoded", "query_decoded", "user_agent", "referer")
# Các nhóm tấn công phát hiện được từ access log (tham chiếu OWASP Top 10).
# 3 nhóm đầu là yêu cầu bắt buộc của đề tài; phần còn lại mở rộng.
VALID_ATTACK_TYPES = (
    "sql_injection", "xss", "path_traversal",   # đề tài (A03, A01)
    "command_injection", "lfi", "rfi",          # A03 injection / A01
    "sensitive_file", "log4shell", "ssti",      # A05 / A06 / A03
    "scanner",                                   # dò quét / recon
)
VALID_SEVERITIES = ("low", "medium", "high", "critical")

MAX_MATCHED_LEN = 200
_RELOAD_INTERVAL_SECONDS = 5
_REQUIRED_FIELDS = ("id", "name", "attack_type", "severity", "target", "pattern")


class Rule:
    """Một rule bất biến với regex đã compile."""

    __slots__ = ("id", "name", "attack_type", "severity", "target",
                 "pattern", "description")

    def __init__(self, id, name, attack_type, severity, target, pattern,
                 description=""):
        self.id = id
        self.name = name
        self.attack_type = attack_type
        self.severity = severity
        self.target = target
        self.pattern = pattern  # re.Pattern đã compile
        self.description = description


def _compile_rule(raw):
    """Chuyển một dict rule thô thành Rule. Trả None nếu không hợp lệ (đã log)."""
    if not isinstance(raw, dict):
        log.warning("skip rule: not a mapping", rule=str(raw)[:80])
        return None
    missing = [f for f in _REQUIRED_FIELDS if not raw.get(f)]
    if missing:
        log.warning("skip rule: missing fields", id=raw.get("id"), missing=missing)
        return None
    if raw["target"] not in VALID_TARGETS:
        log.warning("skip rule: invalid target", id=raw["id"], target=raw["target"])
        return None
    if raw["attack_type"] not in VALID_ATTACK_TYPES:
        log.warning("skip rule: invalid attack_type", id=raw["id"],
                    attack_type=raw["attack_type"])
        return None
    if raw["severity"] not in VALID_SEVERITIES:
        log.warning("skip rule: invalid severity", id=raw["id"],
                    severity=raw["severity"])
        return None
    try:
        compiled = re.compile(raw["pattern"])
    except re.error as exc:
        log.warning("skip rule: bad regex", id=raw["id"], error=str(exc))
        return None
    return Rule(
        id=str(raw["id"]),
        name=str(raw["name"]),
        attack_type=raw["attack_type"],
        severity=raw["severity"],
        target=raw["target"],
        pattern=compiled,
        description=str(raw.get("description") or ""),
    )


def _coerce_docs(doc):
    """File YAML có thể là MỘT mapping hoặc DANH SÁCH mapping."""
    if isinstance(doc, dict):
        return [doc]
    if isinstance(doc, list):
        return doc
    return []


def load_rules(rules_dir):
    """Nạp và compile mọi rule từ rules_dir/*.yaml. Trả tuple bất biến các Rule."""
    rules = []
    for path in sorted(glob.glob(os.path.join(rules_dir, "*.yaml"))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                doc = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError) as exc:
            log.warning("skip rule file: cannot read/parse", path=path, error=str(exc))
            continue
        for raw in _coerce_docs(doc):
            rule = _compile_rule(raw)
            if rule is not None:
                rules.append(rule)
    return tuple(rules)


def build_fields(data):
    """Dựng tập field ứng viên để khớp từ log.enriched.data. Trả dict mới."""
    path_decoded = data.get("path_decoded") or data.get("path") or ""
    query_decoded = data.get("query_decoded") or data.get("query_string") or ""
    url_decoded = path_decoded + (("?" + query_decoded) if query_decoded else "")
    return {
        "path": data.get("path") or "",
        "query_string": data.get("query_string") or "",
        "path_decoded": path_decoded,
        "query_decoded": query_decoded,
        "user_agent": data.get("user_agent") or "",
        "referer": data.get("referer") or "",
        "url_decoded": url_decoded,
    }


def _build_url(data):
    """url = path + ("?" + query_string) nếu có query."""
    path = data.get("path") or ""
    query = data.get("query_string") or ""
    return path + (("?" + query) if query else "")


def _make_detection(rule, field, match, data):
    """Dựng attack.detected.data từ một lần khớp. Immutable."""
    return {
        "url": _build_url(data),
        "method": data.get("method") or "",
        "src_ip": data.get("src_ip") or "",
        "host": data.get("host") or "",
        "rule_id": rule.id,
        "rule_name": rule.name,
        "attack_type": rule.attack_type,
        "severity": rule.severity,
        "field": field,
        "matched_value": match.group(0)[:MAX_MATCHED_LEN],
        "evidence": rule.description or f"rule {rule.id} matched on {field}",
    }


def match_event(rules, data):
    """Khớp mọi rule trên một log.enriched.data. Trả list detection dict."""
    fields = build_fields(data)
    detections = []
    for rule in rules:
        targets = ANY_TARGETS if rule.target == "any" else (rule.target,)
        for target in targets:
            value = fields.get(target, "")
            if not value:
                continue
            match = rule.pattern.search(value)
            if match:
                detections.append(_make_detection(rule, target, match, data))
                break  # một rule khớp một lần là đủ
    return detections


class RuleStore:
    """Giữ tập rule hiện tại và tự nạp lại khi file thay đổi (throttle ~5s)."""

    def __init__(self, rules_dir, interval=_RELOAD_INTERVAL_SECONDS):
        self._dir = rules_dir
        self._interval = interval
        self._rules = ()
        self._signature = ()
        self._last_check = 0.0

    def _current_signature(self):
        """Chữ ký (path, mtime) phát hiện thêm/sửa/xoá file."""
        entries = []
        for path in sorted(glob.glob(os.path.join(self._dir, "*.yaml"))):
            try:
                entries.append((path, os.path.getmtime(path)))
            except OSError:
                pass
        return tuple(entries)

    def load(self):
        """Nạp lại toàn bộ rule ngay lập tức."""
        self._rules = load_rules(self._dir)
        self._signature = self._current_signature()
        self._last_check = time.time()
        return self._rules

    def maybe_reload(self):
        """Nạp lại nếu quá interval và file có thay đổi. Trả tập rule hiện tại."""
        now = time.time()
        if now - self._last_check < self._interval:
            return self._rules
        self._last_check = now
        signature = self._current_signature()
        if signature != self._signature:
            log.info("rules changed, reloading", dir=self._dir)
            self._rules = load_rules(self._dir)
            self._signature = signature
        return self._rules

    @property
    def rules(self):
        return self._rules
