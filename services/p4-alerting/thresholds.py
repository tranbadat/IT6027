"""Phân giải ngưỡng cảnh báo riêng theo domain.

ALERT_THRESHOLDS có dạng "domain:score,domain2:score". Có thể dùng khoá "*" hoặc
"default" làm ngưỡng mặc định chung cho domain không được liệt kê.
"""

# Khoá đặc biệt đại diện cho ngưỡng mặc định khi domain không có cấu hình riêng.
_FALLBACK_KEYS = ("*", "default")


def parse_thresholds(raw: str | None) -> dict[str, float]:
    """Chuyển chuỗi cấu hình thành dict {domain: score}.

    Mục sai định dạng (thiếu ":" hoặc score không phải số) bị bỏ qua, không ném lỗi,
    để một mục hỏng không làm chết cả service. Luôn trả về dict mới (immutable input).
    """
    result: dict[str, float] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        domain, _, score = item.partition(":")
        domain = domain.strip()
        if not domain:
            continue
        try:
            result[domain] = float(score.strip())
        except ValueError:
            continue
    return result


def resolve_threshold(thresholds: dict[str, float], domain: str, fallback: float) -> float:
    """Trả ngưỡng áp dụng cho domain: ưu tiên cấu hình riêng, rồi khoá mặc định, rồi fallback."""
    if domain in thresholds:
        return thresholds[domain]
    for key in _FALLBACK_KEYS:
        if key in thresholds:
            return thresholds[key]
    return fallback
