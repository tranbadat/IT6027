"""Cấu hình nguồn log của C1 (YAML). Xem config/c1.example.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import yaml


@dataclass
class SourceConfig:
    name: str
    type: str                      # "file" | "syslog"
    domain: str = ""               # domain mặc định của nguồn
    server: str = "unknown"        # "nginx" | "apache" | "unknown" (chỉ để ghi nhãn)
    # file
    path: str = ""
    start_at: str = "end"
    max_line_bytes: int = 65536
    multiline: Optional[dict[str, Any]] = None   # {"enabled": bool, "start_pattern": str, "flush_after": float}
    # syslog
    protocol: str = "udp"
    listen: str = "0.0.0.0:5514"
    tag_domains: dict[str, str] = field(default_factory=dict)
    host_domains: dict[str, str] = field(default_factory=dict)


@dataclass
class C1Config:
    sources: list[SourceConfig]
    queue_size: int = 10000
    poll_interval: float = 0.25


def load_config(path: str) -> C1Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    sources: list[SourceConfig] = []
    names: set[str] = set()
    for i, s in enumerate(raw.get("sources") or []):
        if "name" not in s or "type" not in s:
            raise ValueError(f"sources[{i}] thiếu 'name' hoặc 'type'")
        if s["type"] not in ("file", "syslog"):
            raise ValueError(f"sources[{i}].type phải là 'file' hoặc 'syslog'")
        if s["name"] in names:
            raise ValueError(f"tên nguồn bị trùng: {s['name']}")
        names.add(s["name"])
        sc = SourceConfig(**s)
        if sc.type == "file" and not sc.path:
            raise ValueError(f"nguồn {sc.name}: thiếu 'path'")
        if sc.type == "file" and not sc.domain:
            raise ValueError(f"nguồn {sc.name}: file log cần 'domain'")
        if sc.type == "syslog" and not (sc.domain or sc.tag_domains or sc.host_domains):
            raise ValueError(f"nguồn {sc.name}: syslog cần 'domain' mặc định hoặc tag_domains/host_domains")
        sources.append(sc)
    if not sources:
        raise ValueError("cấu hình không có nguồn log nào")
    return C1Config(
        sources=sources,
        queue_size=int(raw.get("queue_size", 10000)),
        poll_interval=float(raw.get("poll_interval", 0.25)),
    )
