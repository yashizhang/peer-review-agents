from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from koala_strategy.config import load_config, resolve_path
from koala_strategy.logging_utils import read_jsonl


def _count(path: Path) -> int:
    return len(read_jsonl(path))


def main() -> None:
    cfg = load_config()
    logs = resolve_path(cfg, "logs_dir")
    console = Console()
    table = Table(title="Koala Agent Dashboard")
    table.add_column("Metric")
    table.add_column("Value")
    for name in ["actions", "verdicts", "errors", "notifications", "heartbeats"]:
        table.add_row(name, str(_count(logs / f"{name}.jsonl")))
    model_metrics = resolve_path(cfg, "model_dir") / "metrics.json"
    if model_metrics.exists():
        metrics = json.loads(model_metrics.read_text(encoding="utf-8"))
        test = metrics.get("global_test", {})
        table.add_row("test AUROC", f"{test.get('auroc', 0):.4f}" if test else "n/a")
        table.add_row("test log loss", f"{test.get('log_loss', 0):.4f}" if test else "n/a")
        table.add_row("test Brier", f"{test.get('brier', 0):.4f}" if test else "n/a")
    console.print(table)


if __name__ == "__main__":
    main()

