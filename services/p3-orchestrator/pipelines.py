"""P3 — Nạp cấu hình pipeline từ thư mục YAML (mount read-only từ config/pipelines)."""
import os

import yaml

from common import logging as log

_YAML_SUFFIXES = (".yaml", ".yml")


def load_pipelines(directory):
    """Đọc mọi *.yaml/*.yml trong directory. Trả list dict đã chuẩn hoá."""
    if not os.path.isdir(directory):
        log.warning("pipelines dir not found", directory=directory)
        return []
    pipelines = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(_YAML_SUFFIXES):
            continue
        doc = _read_yaml(os.path.join(directory, name))
        if doc is not None:
            pipelines.append(_normalize(doc, name))
    log.info("pipelines loaded", count=len(pipelines))
    return pipelines


def domain_index(pipelines):
    """Trả dict domain -> tên pipeline để gắn vào event stage.completed."""
    return {p["domain"]: p["pipeline"] for p in pipelines if p["domain"]}


def _read_yaml(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.error("cannot load pipeline file", path=path, error=str(exc))
        return None


def _normalize(doc, filename):
    """Chuẩn hoá 1 file cấu hình thành dict cố định (immutable output)."""
    stem = filename.rsplit(".", 1)[0]
    pipeline = doc.get("pipeline") or stem
    return {
        "pipeline": pipeline,
        "domain": doc.get("domain") or pipeline,
        "stages": list(doc.get("stages") or []),
    }
