#!/usr/bin/env python3
"""Offline backtest harness for the axis-panel-master agent."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import IncompleteRead
from pathlib import Path
from urllib.request import urlopen

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - only needed for PDF-backed runs
    PdfReader = None

RETRYABLE_SESSION_ERROR = "Read-only file system (os error 30)"
AXIS_KEYS = [
    "evidence_completeness",
    "clarity_reproducibility",
    "practical_scope",
    "technical_soundness",
    "novelty_positioning",
]
DECISION_BANDS = [
    "clear reject",
    "weak reject",
    "weak accept",
    "strong accept",
    "spotlight",
]
CONFIDENCE_LEVELS = ["low", "medium", "high"]

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "decision_band": {"type": "string", "enum": DECISION_BANDS},
        "confidence": {"type": "string", "enum": CONFIDENCE_LEVELS},
        "summary": {"type": "string"},
        "strongest_accept_reason": {"type": "string"},
        "strongest_reject_reason": {"type": "string"},
        "public_comment": {"type": "string"},
        "axis_scores": {
            "type": "object",
            "properties": {key: {"type": "number"} for key in AXIS_KEYS},
            "required": AXIS_KEYS,
            "additionalProperties": False,
        },
    },
    "required": [
        "score",
        "decision_band",
        "confidence",
        "summary",
        "strongest_accept_reason",
        "strongest_reject_reason",
        "public_comment",
        "axis_scores",
    ],
    "additionalProperties": False,
}

TASK_PROMPT = """This is an offline backtest for the axis-panel-master agent.

Read the local paper files in the current working directory and produce the agent's offline judgment.

Available files:
- `AGENTS.md`: the axis-panel system prompt
- `paper.txt`: text extracted from the submission PDF
- `paper.pdf`: the submission PDF itself when available

Constraints:
- Work only from local files in this directory.
- Do not use web browsing, Koala/MCP tools, git operations, notifications, or outside knowledge about the paper.
- Ignore AGENTS.md instructions that require live platform actions such as posting comments, reading existing discussion, fetching notifications, or pushing reasoning files.
- Emulate the five-axis panel workflow locally. If you cannot literally spawn sub-agents, perform five explicitly separated internal passes before synthesis.
- Be conservative about PDF extraction noise. You may use `paper.pdf` for layout cues if needed, but use only local evidence.
- Do not reveal chain-of-thought. Return only the requested structured result.

Output requirements:
- Return JSON only.
- `score` must be a number from 0.0 to 10.0.
- `decision_band` must be one of:
  - `clear reject`
  - `weak reject`
  - `weak accept`
  - `strong accept`
  - `spotlight`
- `confidence` must be one of `low`, `medium`, or `high`.
- `summary` should be 2-4 sentences covering the main reasons for the judgment.
- `strongest_accept_reason` should capture the strongest pro-accept point.
- `strongest_reject_reason` should capture the strongest concern pushing toward reject.
- `public_comment` should be one concise, evidence-backed comment in the style the agent would post publicly, under 250 words.
- `axis_scores` must include 0-10 scores for:
  - `evidence_completeness`
  - `clarity_reproducibility`
  - `practical_scope`
  - `technical_soundness`
  - `novelty_positioning`
"""

OFFLINE_AGENT_PREAMBLE = """# Offline Backtest Harness

This run is an offline evaluation harness for a single already-selected paper.

Environment contract:
- No Koala platform, notifications, MCP tools, or live paper discussion are available.
- Do not attempt to browse the web, push commits, post comments, submit verdicts, or inspect external review metadata.
- Use only local files in the working directory.
- Replace any live-platform workflow step with local-only analysis.
- If subagent tooling is unavailable, emulate the five internal review passes sequentially and synthesize locally.
- The required final deliverable is the structured JSON requested in `task.txt`.

---

"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openreview-dir", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--backtest-dir-file", type=Path, default=None)
    parser.add_argument("--backtest-key", default=None)
    parser.add_argument(
        "--agent-dir",
        type=Path,
        default=Path("agent_configs/axis-panel-master"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("experimental/results/axis_panel_master_backtest"),
    )
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--end-index", type=int, default=None)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--use-full-pdf", action="store_true")
    parser.add_argument(
        "--include-statuses",
        default="accepted,rejected",
        help="Comma-separated statuses to include from papers.jsonl or manifest.",
    )
    parser.add_argument("--accepted-limit", type=int, default=None)
    parser.add_argument("--rejected-limit", type=int, default=None)
    parser.add_argument("--sample-seed", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def load_backtest_dir_mapping(path: Path) -> dict[str, Path]:
    mapping = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid backtest.dir line: {raw_line!r}")
        key, value = line.split("=", 1)
        cleaned = value.strip().strip('"').strip("'")
        mapping[key.strip()] = Path(cleaned)
    return mapping


def build_openreview_dossier(paper: dict) -> str:
    sections = [
        "=== SUBMISSION DOSSIER ===",
        "This dossier was assembled from submission-visible OpenReview fields only.",
        "Final decisions, venue status, reviews, meta-reviews, authors, and author IDs were intentionally omitted.",
        "",
        f"Title: {paper.get('title') or ''}",
    ]
    tldr = (paper.get("tldr") or "").strip()
    if tldr:
        sections.extend(["", "TL;DR:", tldr])
    keywords = paper.get("keywords") or []
    if keywords:
        sections.extend(["", "Keywords:", ", ".join(str(item) for item in keywords)])
    abstract = (paper.get("abstract") or "").strip()
    sections.extend(["", "Abstract:", abstract or "[missing abstract]"])
    return "\n".join(sections).strip() + "\n"


def normalize_status(status: str | None) -> int | None:
    if status == "accepted":
        return 1
    if status in {"rejected", "desk_rejected"}:
        return 0
    return None


def normalize_manifest_row(row: dict) -> dict | None:
    status = row.get("status")
    accepted = row.get("accepted")
    if accepted is None:
        accepted = normalize_status(status)
    elif accepted in {0, 1}:
        accepted = int(accepted)
    else:
        accepted = 1 if bool(accepted) else 0
    if accepted is None:
        return None
    return {
        "forum_id": row["forum_id"],
        "title": row["title"],
        "status": status or ("accepted" if accepted else "rejected"),
        "accepted": accepted,
        "openreview_forum_url": row.get("openreview_forum_url") or row.get("forum_url"),
        "pdf_url": row.get("pdf_url"),
        "paper_text": row.get("paper_text"),
        "categories": row.get("koala_or_fallback_categories") or [],
    }


def load_manifest(path: Path, include_statuses: set[str]) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        normalized = normalize_manifest_row(json.loads(line))
        if normalized is None:
            continue
        if normalized["status"] not in include_statuses:
            continue
        rows.append(normalized)
    return rows


def load_rows(
    openreview_dir: Path,
    include_statuses: set[str],
    sample_seed: int,
    accepted_limit: int | None,
    rejected_limit: int | None,
) -> list[dict]:
    accepted_rows = []
    rejected_rows = []
    papers_path = openreview_dir / "papers.jsonl"
    with papers_path.open(encoding="utf-8") as handle:
        for line in handle:
            paper = json.loads(line)
            status = paper.get("status")
            if status not in include_statuses:
                continue
            accepted = normalize_status(status)
            if accepted is None:
                continue
            row = {
                "forum_id": paper["id"],
                "title": paper["title"],
                "status": status,
                "accepted": accepted,
                "openreview_forum_url": paper.get("openreview_forum_url"),
                "pdf_url": paper.get("pdf_url_from_note") or paper.get("openreview_pdf_url"),
                "paper_text": build_openreview_dossier(paper),
                "categories": paper.get("koala_or_fallback_categories") or [],
            }
            if accepted == 1:
                accepted_rows.append(row)
            else:
                rejected_rows.append(row)

    rng = random.Random(sample_seed)
    rng.shuffle(accepted_rows)
    rng.shuffle(rejected_rows)

    if accepted_limit is not None:
        accepted_rows = accepted_rows[:accepted_limit]
    if rejected_limit is not None:
        rejected_rows = rejected_rows[:rejected_limit]

    rows = accepted_rows + rejected_rows
    rng.shuffle(rows)
    return rows


def prepare_codex_home(home_root: Path) -> None:
    codex_home = home_root / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    source = Path.home() / ".codex"
    for name in ["auth.json", "config.toml", "version.json", "installation_id"]:
        src = source / name
        if src.exists():
            shutil.copy2(src, codex_home / name)


def write_agent_file(agent_dir: Path, run_dir: Path) -> None:
    prompt = (agent_dir / "system_prompt.md").read_text(encoding="utf-8")
    (run_dir / "AGENTS.md").write_text(OFFLINE_AGENT_PREAMBLE + prompt, encoding="utf-8")


def download_pdf(url: str, path: Path) -> None:
    last_error = None
    for attempt in range(5):
        try:
            with urlopen(url, timeout=120) as response:
                path.write_bytes(response.read())
            return
        except IncompleteRead as exc:
            last_error = exc
        except OSError as exc:
            last_error = exc
        if path.exists():
            path.unlink()
        time.sleep(1.0 + attempt)
    raise RuntimeError(f"failed to download PDF from {url}: {last_error}")


def extract_pdf_text(pdf_path: Path) -> str:
    if PdfReader is None:
        raise RuntimeError("pypdf is required for full-PDF evaluation but is not installed")
    reader = PdfReader(str(pdf_path))
    parts = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        parts.append(f"=== PAGE {index} ===\n{text.strip()}\n")
    joined = "\n".join(parts).strip() + "\n"
    cleaned = joined.replace("\x00", "")
    cleaned = "".join(ch if ch == "\n" or ch == "\t" or ord(ch) >= 32 else " " for ch in cleaned)
    return cleaned.encode("utf-8", "replace").decode("utf-8")


def decision_band_accepts(decision_band: str) -> bool:
    return decision_band in {"weak accept", "strong accept", "spotlight"}


def score_accepts(score: float) -> bool:
    return score >= 5.0


def run_one(
    row: dict,
    agent_dir: Path,
    output_dir: Path,
    model: str,
    overwrite: bool,
    use_full_pdf: bool,
) -> dict:
    forum_id = row["forum_id"]
    paper_dir = output_dir / "runs" / forum_id
    paper_dir.mkdir(parents=True, exist_ok=True)

    result_path = paper_dir / "result.json"
    if result_path.exists() and not overwrite:
        return json.loads(result_path.read_text(encoding="utf-8"))

    if use_full_pdf:
        pdf_url = row.get("pdf_url")
        if not pdf_url:
            raise RuntimeError(f"Row {forum_id} is missing pdf_url for --use-full-pdf")
        pdf_path = paper_dir / "paper.pdf"
        download_pdf(pdf_url, pdf_path)
        paper_text = extract_pdf_text(pdf_path)
    else:
        paper_text = row.get("paper_text")
        if not paper_text:
            raise RuntimeError(f"Row {forum_id} is missing paper_text for dossier mode")

    write_agent_file(agent_dir, paper_dir)
    (paper_dir / "paper.txt").write_text(paper_text, encoding="utf-8")
    (paper_dir / "schema.json").write_text(json.dumps(SCORE_SCHEMA, indent=2) + "\n", encoding="utf-8")
    (paper_dir / "task.txt").write_text(TASK_PROMPT, encoding="utf-8")

    tmp_dir = paper_dir / ".runtime" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    home_root = paper_dir / ".runtime" / "home"
    prepare_codex_home(home_root)

    output_message = paper_dir / "last_message.json"
    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--model",
        model,
        "--output-schema",
        str(paper_dir / "schema.json"),
        "--output-last-message",
        str(output_message),
        "-C",
        str(paper_dir),
        TASK_PROMPT,
    ]

    child_env = os.environ.copy()
    child_env["TMPDIR"] = str(tmp_dir)
    child_env["HOME"] = str(home_root)
    completed = None
    for attempt in range(8):
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False, env=child_env)
        if completed.returncode == 0:
            break
        if RETRYABLE_SESSION_ERROR not in completed.stderr or attempt == 7:
            raise RuntimeError(
                f"codex exec failed for {forum_id} with exit code {completed.returncode}\n"
                f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}"
            )
        time.sleep(1.0 + attempt)

    prediction = json.loads(output_message.read_text(encoding="utf-8"))
    score = float(prediction["score"])
    decision_band = prediction["decision_band"]
    result = {
        "forum_id": forum_id,
        "title": row["title"],
        "status": row["status"],
        "accepted": row["accepted"],
        "openreview_forum_url": row.get("openreview_forum_url"),
        "pdf_url": row.get("pdf_url"),
        "categories": row.get("categories") or [],
        "score": score,
        "decision_band": decision_band,
        "confidence": prediction["confidence"],
        "summary": prediction["summary"],
        "strongest_accept_reason": prediction["strongest_accept_reason"],
        "strongest_reject_reason": prediction["strongest_reject_reason"],
        "public_comment": prediction["public_comment"],
        "axis_scores": prediction["axis_scores"],
        "score_predicted_accept": score_accepts(score),
        "band_predicted_accept": decision_band_accepts(decision_band),
        "score_correct": score_accepts(score) == (row["accepted"] == 1),
        "band_correct": decision_band_accepts(decision_band) == (row["accepted"] == 1),
        "score_band_consistent": score_accepts(score) == decision_band_accepts(decision_band),
    }
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def run_manifest(rows: list[dict], runner, workers: int) -> list[dict]:
    if workers <= 1:
        results = []
        for row in rows:
            print(f"Running {row['forum_id']} ({row['status']})", flush=True)
            results.append(runner(row))
        return results

    ordered_results = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for index, row in enumerate(rows):
            print(f"Queued {row['forum_id']} ({row['status']})", flush=True)
            future = executor.submit(runner, row)
            futures[future] = index

        for future in as_completed(futures):
            index = futures[future]
            result = future.result()
            ordered_results[index] = result
            print(f"Completed {result['forum_id']} ({result['status']})", flush=True)

    return ordered_results


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def summarize_binary(results: list[dict], key: str) -> dict:
    predicted_positive = [bool(item[key]) for item in results]
    gold_positive = [item["accepted"] == 1 for item in results]
    tp = sum(pred and gold for pred, gold in zip(predicted_positive, gold_positive))
    tn = sum((not pred) and (not gold) for pred, gold in zip(predicted_positive, gold_positive))
    fp = sum(pred and (not gold) for pred, gold in zip(predicted_positive, gold_positive))
    fn = sum((not pred) and gold for pred, gold in zip(predicted_positive, gold_positive))
    return {
        "accuracy": (tp + tn) / len(results) if results else None,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
    }


def average_axis_scores(results: list[dict]) -> dict[str, float] | None:
    if not results:
        return None
    averages = {}
    for key in AXIS_KEYS:
        averages[key] = sum(float(item["axis_scores"][key]) for item in results) / len(results)
    return averages


def summarize(results: list[dict]) -> dict:
    accepted_results = [item for item in results if item["accepted"] == 1]
    rejected_results = [item for item in results if item["accepted"] == 0]
    xs = [float(item["score"]) for item in results]
    ys = [float(item["accepted"]) for item in results]
    score_consistent = sum(item["score_band_consistent"] for item in results)
    return {
        "total": len(results),
        "accepted": len(accepted_results),
        "rejected": len(rejected_results),
        "mean_score_accepted": (sum(item["score"] for item in accepted_results) / len(accepted_results))
        if accepted_results
        else None,
        "mean_score_rejected": (sum(item["score"] for item in rejected_results) / len(rejected_results))
        if rejected_results
        else None,
        "pearson_score_vs_accept": pearson(xs, ys),
        "score_threshold_5": summarize_binary(results, key="score_predicted_accept"),
        "decision_band_accept": summarize_binary(results, key="band_predicted_accept"),
        "score_band_consistency_rate": (score_consistent / len(results)) if results else None,
        "mean_axis_scores_accepted": average_axis_scores(accepted_results),
        "mean_axis_scores_rejected": average_axis_scores(rejected_results),
    }


def main() -> None:
    args = parse_args()
    if args.clean and args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.workers <= 0:
        raise SystemExit("--workers must be positive")

    include_statuses = {part.strip() for part in args.include_statuses.split(",") if part.strip()}
    if not include_statuses:
        raise SystemExit("--include-statuses must not be empty")

    openreview_dir = args.openreview_dir
    if args.backtest_dir_file is not None or args.backtest_key is not None:
        if args.backtest_dir_file is None or args.backtest_key is None:
            raise SystemExit("--backtest-dir-file and --backtest-key must be provided together")
        mapping = load_backtest_dir_mapping(args.backtest_dir_file)
        if args.backtest_key not in mapping:
            raise SystemExit(f"{args.backtest_key!r} not found in {args.backtest_dir_file}")
        openreview_dir = mapping[args.backtest_key]

    if args.manifest is not None:
        rows = load_manifest(args.manifest, include_statuses=include_statuses)
    else:
        if openreview_dir is None:
            raise SystemExit("Provide --manifest, --openreview-dir, or --backtest-dir-file with --backtest-key")
        rows = load_rows(
            openreview_dir=openreview_dir,
            include_statuses=include_statuses,
            sample_seed=args.sample_seed,
            accepted_limit=args.accepted_limit,
            rejected_limit=args.rejected_limit,
        )

    rows = rows[args.start_index : args.end_index]
    if args.limit is not None:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit("No papers matched the requested filters")

    (args.output_dir / "input_manifest.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n",
        encoding="utf-8",
    )

    def runner(row: dict) -> dict:
        return run_one(
            row=row,
            agent_dir=args.agent_dir,
            output_dir=args.output_dir,
            model=args.model,
            overwrite=args.overwrite,
            use_full_pdf=args.use_full_pdf,
        )

    results = run_manifest(rows, runner=runner, workers=args.workers)
    summary = summarize(results)
    (args.output_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=True) for item in results) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
