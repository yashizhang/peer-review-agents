from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import load_config, resolve_path
from .utils import ensure_dir, iso_now


def log_path(name: str, config: dict[str, Any] | None = None) -> Path:
    cfg = config or load_config()
    return resolve_path(cfg, "logs_dir") / f"{name}.jsonl"


def append_jsonl(path: str | Path, record: dict[str, Any]) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    payload = {"timestamp": iso_now(), **record}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def log_event(name: str, record: dict[str, Any], config: dict[str, Any] | None = None) -> None:
    append_jsonl(log_path(name, config), record)


def log_error(error: Exception | str, context: dict[str, Any] | None = None, config: dict[str, Any] | None = None) -> None:
    message = str(error)
    log_event("errors", {"error": message, "context": context or {}}, config)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

