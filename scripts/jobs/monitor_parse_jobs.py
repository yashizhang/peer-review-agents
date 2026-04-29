from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor Paper2Markdown-V3 parse jobs every N seconds.")
    parser.add_argument("job_ids", nargs="+", help="Slurm job IDs to monitor.")
    parser.add_argument("--sleep", type=int, default=300, help="Polling interval in seconds.")
    parser.add_argument("--run-root", type=Path, default=Path("data/processed_papers"))
    parser.add_argument("--run-name", type=str, default="")
    parser.add_argument("--max-iterations", type=int, default=0, help="0 means unlimited.")
    parser.add_argument("--once", action="store_true", help="Print once and exit.")
    return parser.parse_args()


def _job_ids_summary(job_ids: list[str]) -> set[str]:
    return {jid for jid in job_ids if jid}


def _active_jobs(job_ids: set[str]) -> set[str]:
    if not job_ids:
        return set()
    proc = subprocess.run(
        ["squeue", "-h", "-o", "%A"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"squeue failed: {proc.stderr.strip()} {proc.stdout.strip()}".strip())
    active = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return job_ids.intersection(active)


def _read_summary(run_dir: Path, run_name: str) -> str:
    if not run_name:
        return "run_name not provided"
    run_root = run_dir / run_name
    status_file = run_root / "parse_status.txt"
    if not status_file.exists():
        return "no summary yet"
    return status_file.read_text(encoding="utf-8")


def main() -> None:
    args = parse_args()
    watch_set = _job_ids_summary(args.job_ids)
    iteration = 0

    while True:
        iteration += 1
        active = _active_jobs(watch_set)
        done = watch_set - active
        print(f"iter={iteration} total={len(watch_set)} active={len(active)} done={len(done)}")
        if run_summary := _read_summary(args.run_root, args.run_name):
            print(run_summary.strip())
        if args.once:
            break
        if not active:
            break
        if args.max_iterations and iteration >= args.max_iterations:
            break
        time.sleep(args.sleep)


if __name__ == "__main__":
    main()
