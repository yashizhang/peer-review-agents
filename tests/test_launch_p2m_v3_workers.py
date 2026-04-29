from __future__ import annotations

import argparse
from pathlib import Path

from scripts.jobs.launch_p2m_v3_workers import _build_submit_command, _sbatch_time_args


def test_sbatch_time_args() -> None:
    assert _sbatch_time_args("03:00:00", "short-unkillable") == [
        "--time=03:00:00",
        "--partition=short-unkillable",
    ]


def test_build_submit_command_includes_per_partition_time(tmp_path: Path) -> None:
    args = argparse.Namespace(
        run_root=tmp_path,
        run_name="icml26_test",
        submit_command="sbatch",
    )

    cmd = _build_submit_command(
        args=args,
        partition="short-unkillable",
        shard_index=0,
        job_name="p2m_v3_icml26_test_0",
        time_limit="03:00:00",
    )

    assert "--time=03:00:00" in cmd
    assert "--partition=short-unkillable" in cmd
    assert cmd[-1].endswith("p2m_v3_shared_worker.sbatch")
