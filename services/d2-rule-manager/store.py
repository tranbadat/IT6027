"""D2 — Lưu/đọc rule dưới dạng file YAML trong RULES_DIR.

Mỗi rule một file `<id>.yaml` (một mapping). Đọc chịu được cả file mapping lẫn list
để liệt kê rule do nguồn khác tạo (đồng bộ hành vi D1).
"""
import glob
import os

import yaml

# Thứ tự key khi ghi ra file cho dễ đọc và ổn định trong git.
_KEY_ORDER = ("id", "name", "attack_type", "severity", "target",
              "pattern", "description")


def _rule_path(rules_dir, rule_id):
    return os.path.join(rules_dir, f"{rule_id}.yaml")


def rule_exists(rules_dir, rule_id):
    return os.path.isfile(_rule_path(rules_dir, rule_id))


def _coerce_docs(doc):
    if isinstance(doc, dict):
        return [doc]
    if isinstance(doc, list):
        return [d for d in doc if isinstance(d, dict)]
    return []


def list_rules(rules_dir):
    """Đọc mọi rule trong thư mục. Trả list dict (bất biến với caller)."""
    rules = []
    for path in sorted(glob.glob(os.path.join(rules_dir, "*.yaml"))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                doc = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError):
            continue
        rules.extend(_coerce_docs(doc))
    return rules


def get_rule(rules_dir, rule_id):
    """Đọc một rule theo id. Trả dict hoặc None nếu không có/không hợp lệ."""
    path = _rule_path(rules_dir, rule_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        return None
    docs = _coerce_docs(doc)
    return docs[0] if docs else None


def _ordered(rule):
    """Sắp xếp key theo _KEY_ORDER để ghi file gọn gàng. Trả dict mới."""
    ordered = {key: rule[key] for key in _KEY_ORDER if key in rule}
    for key, value in rule.items():  # giữ lại field lạ nếu có
        if key not in ordered:
            ordered[key] = value
    return ordered


def save_rule(rules_dir, rule):
    """Ghi rule ra `<id>.yaml` (một mapping). Trả rule đã ghi."""
    os.makedirs(rules_dir, exist_ok=True)
    path = _rule_path(rules_dir, rule["id"])
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(_ordered(rule), handle, sort_keys=False,
                       allow_unicode=True, default_flow_style=False)
    return rule


def delete_rule(rules_dir, rule_id):
    """Xoá file rule. Trả True nếu đã xoá, False nếu không tồn tại."""
    path = _rule_path(rules_dir, rule_id)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True
