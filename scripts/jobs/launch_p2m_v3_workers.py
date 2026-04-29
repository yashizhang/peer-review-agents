from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit parallel Paper2Markdown-V3 workers.")
    parser.add_argument("--run-root", type=Path, default=Path("data/processed_papers"))
    parser.add_argument("--run-name", type=str, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--marker-binary", type=str, default="marker_single")
    parser.add_argument("--marker-timeout-seconds", type=int, default=240)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--short-unkillable-workers", type=int, default=4)
    parser.add_argument("--short-partition", type=str, default="short-unkillable")
    parser.add_argument("--rest-partition", type=str, default="unkillable")
    parser.add_argument(
        "--submit-command",
        type=str,
        default="sbatch",
        help="Slurm submit command, keep default `sbatch` for Mila.",
    )
    return parser.parse_args()


def _submit_one(
    *,
    partition: str,
    shard_index: int,
    job_name: str,
    args: argparse.Namespace,
) -> str:
    run_dir = args.run_root / args.run_name
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        args.submit_command,
        f"--partition={partition}",
        f"--job-name={job_name}",
        f"--output={log_dir}/p2m_v3_{args.run_name.replace('/', '_')}_{shard_index}_%j.out",
        f"--error={log_dir}/p2m_v3_{args.run_name.replace('/', '_')}_{shard_index}_%j.err",
        "scripts/jobs/p2m_v3_shared_worker.sbatch",
    ]
    env = {
        "RUN_ROOT": str(args.run_root),
        "RUN_NAME": args.run_name,
        "MANIFEST": str(args.manifest),
        "SHARD_INDEX": str(shard_index),
        "SHARD_COUNT": str(args.shard_count),
        "WORKER_ID": f"{job_name}_{shard_index}",
        "MARKER_BIN": args.marker_binary,
        "MARKER_TIMEOUT_SECONDS": str(args.marker_timeout_seconds),
        "MAX_RETRIES": str(args.max_retries),
    }
    env_exports = [f"{k}={v}" for k, v in env.items()]
    full_cmd = cmd[:1] + [f"--export={','.join(env_exports)}"] + cmd[1:]
    proc = subprocess.run(full_cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"sbatch failed for shard {shard_index}: {proc.stderr.strip()} {proc.stdout.strip()}".strip())
    return proc.stdout.strip().split()[-1]


def main() -> None:
    args = parse_args()
    args.run_root.mkdir(parents=True, exist_ok=True)
    run_dir = args.run_root / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.shard_count <= 0:
        raise ValueError("shard-count must be positive")
    if args.short_unkillable_workers < 0 or args.short_unkillable_workers > args.shard_count:
        raise ValueError("short-unkillable-workers must be in [0, shard-count]")

    short_count = min(args.short_unkillable_workers, args.shard_count)
    job_ids: list[str] = []
    run_name_slug = args.run_name.replace("/", "_")
    for shard_index in range(args.shard_count):
        partition = args.short_partition if shard_index < short_count else args.rest_partition
        job_name = f"p2m_v3_{run_name_slug}_{shard_index}"
        job_id = _submit_one(partition=partition, shard_index=shard_index, job_name=job_name, args=args)
        job_ids.append(job_id)
        print(f"submitted shard={shard_index} partition={partition} job_id={job_id}")

    print(" ".join(job_ids))


if __name__ == "__main__":
    main()
