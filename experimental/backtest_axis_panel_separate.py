#!/usr/bin/env python3
"""Offline backtest harness for the axis-panel-separate agent."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import IncompleteRead
from pathlib import Path
from urllib.request import urlopen

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]

from experimental.review_calibrator import apply_calibration, select_category_priors

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
FIXED_THRESHOLD = 6.4
RETRYABLE_SESSION_ERROR = "Read-only file system (os error 30)"

BASE_SCHEMA = {
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
        "axis_notes": {
            "type": "object",
            "properties": {key: {"type": "string"} for key in AXIS_KEYS},
            "required": AXIS_KEYS,
            "additionalProperties": False,
        },
        "memory_queries": {"type": "array", "items": {"type": "string"}},
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
        "axis_notes",
        "memory_queries",
    ],
    "additionalProperties": False,
}

CALIBRATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "delta_score": {"type": "number"},
        "suggested_decision_band": {"type": "string", "enum": DECISION_BANDS},
        "confidence": {"type": "string", "enum": CONFIDENCE_LEVELS},
        "use_priors": {"type": "boolean"},
        "calibration_note": {"type": "string"},
    },
    "required": [
        "delta_score",
        "suggested_decision_band",
        "confidence",
        "use_priors",
        "calibration_note",
    ],
    "additionalProperties": False,
}

OFFLINE_AGENT_PREAMBLE = """# Offline Separate-Calibration Backtest Harness

This run is an offline evaluation harness for a single already-selected paper.

Environment contract:
- No Koala platform, notifications, MCP tools, or live paper discussion are available.
- Do not browse the web or use outside knowledge about the paper.
- Use only local files in the working directory plus the supplied category-prior artifact.
- Replace any live-platform workflow step with local-only analysis.
- The required deliverable is the structured JSON requested in `task.txt`.

---

"""

BASE_TASK_PROMPT = """Stage A: paper-first review only.

Read the local paper files in this directory and produce a paper-only `base_result`.

Available files:
- `AGENTS.md`
- `paper.txt`
- `paper.pdf` when present

Rules:
- Work only from local target-paper files.
- Run five explicitly separated internal passes before synthesis.
- Stop before any memory, priors, or cross-paper calibration.
- `axis_notes` must contain 1-3 sentences per axis using only target-paper evidence.
- `memory_queries` must be paper-derived audit questions or concern labels that a later calibrator could use. They are not evidence and should not mention any other paper id.
- Return JSON only.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openreview-dir", type=Path, default=None)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "experimental/results/axis_panel_master_backtest_iclr2026_100/input_manifest.jsonl",
    )
    parser.add_argument("--backtest-dir-file", type=Path, default=None)
    parser.add_argument("--backtest-key", default=None)
    parser.add_argument("--agent-dir", type=Path, default=REPO_ROOT / "agent_configs/axis-panel-separate")
    parser.add_argument(
        "--priors-path",
        type=Path,
        default=REPO_ROOT / "experimental/artifacts/axis_panel_separate/review_priors_by_category.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "experimental/results/axis_panel_separate_backtest_iclr2026_100",
    )
    parser.add_argument(
        "--baseline-results",
        type=Path,
        default=REPO_ROOT / "experimental/results/axis_panel_master_backtest_iclr2026_100/results.jsonl",
    )
    parser.add_argument("--model", default="gpt-5.5")
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
        mapping[key.strip()] = Path(value.strip().strip('"').strip("'"))
    return mapping


def build_openreview_dossier(paper: dict) -> str:
    sections = [
        "=== SUBMISSION DOSSIER ===",
        "This dossier was assembled from submission-visible OpenReview fields only.",
        "Final decisions, venue status, reviews, meta-reviews, authors, and author IDs were intentionally omitted.",
        "",
        f"Title: {paper.get('title') or ''}",
    ]
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
        "categories": row.get("koala_or_fallback_categories") or row.get("categories") or [],
    }


def load_manifest(path: Path, include_statuses: set[str]) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        normalized = normalize_manifest_row(json.loads(line))
        if normalized is None or normalized["status"] not in include_statuses:
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
    with (openreview_dir / "papers.jsonl").open(encoding="utf-8") as handle:
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
        parts.append(f"=== PAGE {index} ===\n{(page.extract_text() or '').strip()}\n")
    return "\n".join(parts).strip().replace("\x00", "") + "\n"


def run_codex_json(run_dir: Path, model: str, schema: dict, prompt: str, output_name: str) -> dict:
    schema_path = run_dir / f"{output_name}_schema.json"
    output_message = run_dir / f"{output_name}_last_message.json"
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    tmp_dir = run_dir / ".runtime" / "tmp"
    home_root = run_dir / ".runtime" / "home"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    prepare_codex_home(home_root)
    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--model",
        model,
        "-c",
        'model_reasoning_effort="xhigh"',
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_message),
        "-C",
        str(run_dir),
        prompt,
    ]
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_dir)
    env["HOME"] = str(home_root)
    completed = None
    for attempt in range(8):
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
        if completed.returncode == 0:
            break
        if RETRYABLE_SESSION_ERROR not in completed.stderr or attempt == 7:
            raise RuntimeError(
                f"codex exec failed in {run_dir} with exit code {completed.returncode}\n"
                f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}"
            )
        time.sleep(1.0 + attempt)
    return json.loads(output_message.read_text(encoding="utf-8"))


def score_accepts(score: float, threshold: float = FIXED_THRESHOLD) -> bool:
    return score >= threshold


def decision_band_accepts(decision_band: str) -> bool:
    return decision_band in {"weak accept", "strong accept", "spotlight"}


def sanitize_text(text: str) -> str:
    return str(text).encode("utf-8", "replace").decode("utf-8")


def prepare_paper(run_dir: Path, row: dict, use_full_pdf: bool) -> None:
    if use_full_pdf:
        pdf_url = row.get("pdf_url")
        if not pdf_url:
            raise RuntimeError(f"Row {row['forum_id']} is missing pdf_url for --use-full-pdf")
        pdf_path = run_dir / "paper.pdf"
        download_pdf(pdf_url, pdf_path)
        paper_text = extract_pdf_text(pdf_path)
    else:
        paper_text = row.get("paper_text")
        if not paper_text:
            raise RuntimeError(f"Row {row['forum_id']} is missing paper_text for dossier mode")
    (run_dir / "paper.txt").write_text(sanitize_text(paper_text), encoding="utf-8")


def run_one(
    row: dict,
    agent_dir: Path,
    priors: dict,
    output_dir: Path,
    model: str,
    overwrite: bool,
    use_full_pdf: bool,
) -> dict:
    run_dir = output_dir / "runs" / row["forum_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "result.json"
    if result_path.exists() and not overwrite:
        return json.loads(result_path.read_text(encoding="utf-8"))

    write_agent_file(agent_dir, run_dir)
    prepare_paper(run_dir, row=row, use_full_pdf=use_full_pdf)
    base_result_path = run_dir / "base_result.json"
    if base_result_path.exists() and not overwrite:
        base_result = json.loads(base_result_path.read_text(encoding="utf-8"))
    else:
        base_result = run_codex_json(run_dir=run_dir, model=model, schema=BASE_SCHEMA, prompt=BASE_TASK_PROMPT, output_name="base")
        base_result_path.write_text(json.dumps(base_result, indent=2) + "\n", encoding="utf-8")

    selected_priors = select_category_priors(priors=priors, categories=row.get("categories") or [])
    calibrator_prompt = (agent_dir / "calibrator_prompt.md").read_text(encoding="utf-8").format(
        categories_json=json.dumps(row.get("categories") or [], ensure_ascii=True, indent=2),
        base_result_json=json.dumps(base_result, ensure_ascii=True, indent=2),
        priors_json=json.dumps(selected_priors, ensure_ascii=True, indent=2),
    )
    calibration = run_codex_json(
        run_dir=run_dir,
        model=model,
        schema=CALIBRATOR_SCHEMA,
        prompt=calibrator_prompt,
        output_name="calibration",
    )
    (run_dir / "calibration_result.json").write_text(json.dumps(calibration, indent=2) + "\n", encoding="utf-8")
    fused = apply_calibration(base_result=base_result, calibration=calibration)
    final_score = float(fused["score"])
    final_band = fused["decision_band"]
    result = {
        "variant": "separate",
        "forum_id": row["forum_id"],
        "title": row["title"],
        "status": row["status"],
        "accepted": row["accepted"],
        "openreview_forum_url": row.get("openreview_forum_url"),
        "pdf_url": row.get("pdf_url"),
        "categories": row.get("categories") or [],
        "score": final_score,
        "decision_band": final_band,
        "confidence": fused["confidence"],
        "summary": base_result["summary"],
        "strongest_accept_reason": base_result["strongest_accept_reason"],
        "strongest_reject_reason": base_result["strongest_reject_reason"],
        "public_comment": base_result["public_comment"],
        "axis_scores": base_result["axis_scores"],
        "axis_notes": base_result["axis_notes"],
        "memory_queries": base_result["memory_queries"],
        "base_score": float(base_result["score"]),
        "base_decision_band": base_result["decision_band"],
        "base_confidence": base_result["confidence"],
        "score_predicted_accept": score_accepts(final_score, threshold=5.0),
        "band_predicted_accept": decision_band_accepts(final_band),
        "score_correct": score_accepts(final_score, threshold=5.0) == (row["accepted"] == 1),
        "band_correct": decision_band_accepts(final_band) == (row["accepted"] == 1),
        "score_band_consistent": score_accepts(final_score, threshold=5.0) == decision_band_accepts(final_band),
        "score_delta": round(final_score - float(base_result["score"]), 4),
        "band_changed": bool(fused["band_changed"]),
        "memory_used": bool(fused["memory_used"]),
        "separate_diagnostics": {
            "selected_categories": sorted(selected_priors["categories"]),
            "delta_score_requested": fused["delta_score_requested"],
            "delta_score_applied": fused["delta_score_applied"],
            "suggested_decision_band": fused["suggested_decision_band"],
            "applied_decision_band": fused["applied_decision_band"],
            "calibration_note": fused["calibration_note"],
        },
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
            futures[executor.submit(runner, row)] = index
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


def binary_metrics(results: list[dict], threshold: float) -> dict:
    tp = tn = fp = fn = 0
    for item in results:
        predicted = score_accepts(float(item["score"]), threshold=threshold)
        gold = item["accepted"] == 1
        tp += int(predicted and gold)
        tn += int((not predicted) and (not gold))
        fp += int(predicted and (not gold))
        fn += int((not predicted) and gold)
    total = len(results)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = None
    if precision is not None and recall is not None and (precision + recall):
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "threshold": threshold,
        "accuracy": (tp + tn) / total if total else None,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def threshold_sweep(results: list[dict]) -> dict:
    thresholds = sorted({round(float(item["score"]), 2) for item in results} | {FIXED_THRESHOLD})
    best_key = None
    best_metrics = None
    for threshold in thresholds:
        metrics = binary_metrics(results, threshold=threshold)
        key = (
            metrics["accuracy"] if metrics["accuracy"] is not None else -1.0,
            -metrics["fp"],
            metrics["recall"] if metrics["recall"] is not None else -1.0,
            -threshold,
        )
        if best_key is None or key > best_key:
            best_key = key
            best_metrics = metrics
    return best_metrics


def load_baseline_results(path: Path) -> dict[str, dict]:
    return {
        row["forum_id"]: row
        for row in (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def summarize(results: list[dict], baseline_results: dict[str, dict], baseline_path: Path, output_dir: Path) -> dict:
    accepted_results = [item for item in results if item["accepted"] == 1]
    rejected_results = [item for item in results if item["accepted"] == 0]
    xs = [float(item["score"]) for item in results]
    ys = [float(item["accepted"]) for item in results]
    baseline_pairs = [
        (float(item["score"]), float(baseline_results[item["forum_id"]]["score"]))
        for item in results
        if item["forum_id"] in baseline_results
    ]
    flips = []
    for item in results:
        baseline = baseline_results.get(item["forum_id"])
        if not baseline:
            continue
        baseline_accept = score_accepts(float(baseline["score"]), threshold=FIXED_THRESHOLD)
        variant_accept = score_accepts(float(item["score"]), threshold=FIXED_THRESHOLD)
        if baseline_accept != variant_accept:
            flips.append(
                {
                    "forum_id": item["forum_id"],
                    "title": item["title"],
                    "gold_accepted": item["accepted"],
                    "baseline_score": baseline["score"],
                    "variant_score": item["score"],
                    "baseline_accept_6_4": baseline_accept,
                    "variant_accept_6_4": variant_accept,
                }
            )
    flips_path = output_dir / "baseline_flips.jsonl"
    flips_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=True) for item in flips) + ("\n" if flips else ""),
        encoding="utf-8",
    )
    fixed = binary_metrics(results, threshold=FIXED_THRESHOLD)
    baseline_fixed = binary_metrics(list(baseline_results.values()), threshold=FIXED_THRESHOLD)
    return {
        "variant": "separate",
        "total": len(results),
        "accepted": len(accepted_results),
        "rejected": len(rejected_results),
        "pearson_score_vs_accept": pearson(xs, ys),
        "accepted_rejected_mean_score_gap": (
            (sum(item["score"] for item in accepted_results) / len(accepted_results))
            - (sum(item["score"] for item in rejected_results) / len(rejected_results))
        )
        if accepted_results and rejected_results
        else None,
        "fixed_threshold_6_4": fixed,
        "threshold_sweep_best": threshold_sweep(results),
        "baseline_comparison": {
            "baseline_results_path": str(baseline_path),
            "score_correlation": pearson([pair[0] for pair in baseline_pairs], [pair[1] for pair in baseline_pairs]) if baseline_pairs else None,
            "baseline_fixed_threshold_6_4": baseline_fixed,
            "flip_count": len(flips),
            "flips_file": str(flips_path),
            "promotion_gate": {
                "beats_accuracy": fixed["accuracy"] is not None
                and baseline_fixed["accuracy"] is not None
                and fixed["accuracy"] > baseline_fixed["accuracy"],
                "matches_accuracy_reduces_fp": fixed["accuracy"] == baseline_fixed["accuracy"] and fixed["fp"] < baseline_fixed["fp"],
            },
        },
    }


def main() -> None:
    args = parse_args()
    if args.clean and args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.workers <= 0:
        raise SystemExit("--workers must be positive")
    priors = json.loads(args.priors_path.read_text(encoding="utf-8"))
    include_statuses = {part.strip() for part in args.include_statuses.split(",") if part.strip()}
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
            priors=priors,
            output_dir=args.output_dir,
            model=args.model,
            overwrite=args.overwrite,
            use_full_pdf=args.use_full_pdf,
        )

    results = run_manifest(rows, runner=runner, workers=args.workers)
    baseline_results = load_baseline_results(args.baseline_results)
    summary = summarize(results, baseline_results=baseline_results, baseline_path=args.baseline_results, output_dir=args.output_dir)
    (args.output_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=True) for item in results) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
