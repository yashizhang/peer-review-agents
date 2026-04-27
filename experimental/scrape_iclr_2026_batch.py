#!/usr/bin/env python3
"""Build a balanced offline batch of ICLR 2026 submission PDFs from OpenReview.

The batch uses only:
- desk-rejected submissions (`ICLR 2026 Conference Desk Rejected Submission`)
- non-desk-rejected submissions that still show as `Submitted to ICLR 2026`

Accepted papers are intentionally excluded because their public PDFs are often the
published camera-ready versions, which are de-anonymized and therefore unsuitable
for testing a desk-rejection screener that is meant to inspect submission-time PDFs.
"""

from __future__ import annotations

import argparse
import html
import http.client
import json
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

SUBMISSIONS_URL = "https://openreview.net/submissions?venue=ICLR.cc%2F2026%2FConference&page={page}"
USER_AGENT = "peer-review-agents/0.1 (+offline-backtest)"
DEFAULT_OUTDIR = Path("experimental/data/iclr_2026_desk_reject_batch")
CARD_RE = re.compile(
    r'<li><div class="note.*?<h4><a href="/forum\?id=(?P<id>[A-Za-z0-9_-]+)">(?P<title>.*?)</a></h4>.*?'
    r'<ul class="note-meta-info list-inline"><li>.*?</li><li>(?P<status>.*?)</li>',
    re.S,
)


def _get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def _download_file(url: str, path: Path, retries: int = 4) -> None:
    if path.exists() and path.stat().st_size > 0:
        return

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                path.write_bytes(response.read())
            return
        except urllib.error.HTTPError as exc:
            last_error = exc
            if path.exists():
                path.unlink()
            if exc.code != 429 or attempt == retries:
                break
            time.sleep(5.0 * attempt)
        except (http.client.IncompleteRead, TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            if path.exists():
                path.unlink()
            if attempt == retries:
                break
            time.sleep(1.5 * attempt)

    if last_error is None:
        raise RuntimeError(f"Download failed without an exception for {url}")
    raise last_error


def _classify_status(status: str) -> str | None:
    if "Desk Rejected" in status:
        return "desk_rejected"
    if "Submitted to ICLR 2026" in status:
        return "not_desk_rejected"
    return None


def _parse_cards(html_text: str) -> list[dict]:
    cards = []
    for match in CARD_RE.finditer(html_text):
        cards.append(
            {
                "id": match.group("id"),
                "title": html.unescape(match.group("title").strip()),
                "status": html.unescape(match.group("status").strip()),
            }
        )
    return cards


def fetch_candidate_notes(per_class: int, max_pages: int, sleep_seconds: float) -> dict[str, list[dict]]:
    needed = {
        "desk_rejected": per_class,
        "not_desk_rejected": per_class,
    }
    collected = {
        "desk_rejected": [],
        "not_desk_rejected": [],
    }
    seen_ids: set[str] = set()

    for page in range(1, max_pages + 1):
        cards = _parse_cards(_get_text(SUBMISSIONS_URL.format(page=page)))
        if not cards:
            break

        for card in cards:
            note_id = card["id"]
            if note_id in seen_ids:
                continue
            seen_ids.add(note_id)

            label = _classify_status(card["status"])
            if label is None:
                continue
            if len(collected[label]) >= needed[label]:
                continue
            collected[label].append(card)

        if all(len(collected[key]) >= needed[key] for key in needed):
            break

        time.sleep(sleep_seconds)

    return collected


def load_excluded_ids(manifest_path: Path | None) -> set[str]:
    if manifest_path is None:
        return set()
    rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {row["forum_id"] for row in rows}


def make_manifest_entry(note: dict, label: str, pdf_relpath: str) -> dict:
    note_id = note["id"]
    return {
        "forum_id": note_id,
        "title": note["title"],
        "label": label,
        "status_source": note["status"],
        "forum_url": f"https://openreview.net/forum?id={note_id}",
        "pdf_url": f"https://openreview.net/pdf?id={note_id}",
        "pdf_path": pdf_relpath,
    }


def write_dataset(outdir: Path, selected: dict[str, list[dict]], seed: int) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    manifest_path = outdir / "manifest.jsonl"
    summary_path = outdir / "summary.json"
    readme_path = outdir / "README.md"

    manifest_entries: list[dict] = []
    for label, notes in selected.items():
        pdf_dir = outdir / label / "pdfs"
        pdf_dir.mkdir(parents=True, exist_ok=True)

        for note in notes:
            note_id = note["id"]
            pdf_path = pdf_dir / f"{note_id}.pdf"
            pdf_relpath = str(pdf_path.relative_to(outdir))
            _download_file(f"https://openreview.net/pdf?id={note_id}", pdf_path)
            manifest_entries.append(make_manifest_entry(note, label, pdf_relpath))
            time.sleep(0.1)

    manifest_entries.sort(key=lambda item: (item["label"], item["title"].lower(), item["forum_id"]))

    with manifest_path.open("w", encoding="utf-8") as handle:
        for entry in manifest_entries:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")

    summary = {
        "dataset": "iclr_2026_desk_reject_batch",
        "seed": seed,
        "total_papers": len(manifest_entries),
        "counts": {
            "desk_rejected": sum(entry["label"] == "desk_rejected" for entry in manifest_entries),
            "not_desk_rejected": sum(entry["label"] == "not_desk_rejected" for entry in manifest_entries),
        },
        "source": {
            "submissions_url_template": SUBMISSIONS_URL,
            "pdf_base": "https://openreview.net/pdf?id=<forum_id>",
        },
        "caveat": (
            "Labels come from public OpenReview venue/status metadata, not from an explicit per-paper "
            "desk-rejection reason. Some desk rejects may depend on external policy violations that are "
            "not visible in the PDF alone."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    readme = f"""# ICLR 2026 Desk-Reject Backtest Batch

This directory was generated by `experimental/scrape_iclr_2026_batch.py`.

- Total papers: {summary["total_papers"]}
- Desk rejected: {summary["counts"]["desk_rejected"]}
- Not desk rejected: {summary["counts"]["not_desk_rejected"]}
- Seed: {seed}

## Composition

The positive class is the public OpenReview status for `Desk Rejected Submission`.

The negative class is limited to papers whose public status still appears as `Submitted to ICLR 2026`.
Accepted papers are intentionally excluded because their public PDFs are often published, de-anonymized
camera-ready versions rather than submission-time PDFs.

## Caveat

This is a noisy proxy for desk-reject detection. A desk-rejected label does not imply the reason is
recoverable from the PDF alone. Some papers were desk rejected for external policy violations that an
offline PDF-only agent cannot observe.
"""
    readme_path.write_text(readme, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, default=20, help="Total number of papers. Must be even.")
    parser.add_argument("--seed", type=int, default=0, help="Sampling seed.")
    parser.add_argument(
        "--outdir",
        type=Path,
        default=DEFAULT_OUTDIR,
        help="Output directory for manifest and PDFs.",
    )
    parser.add_argument(
        "--exclude-manifest",
        type=Path,
        default=None,
        help="Optional manifest.jsonl whose forum_ids should be excluded from sampling.",
    )
    parser.add_argument("--max-pages", type=int, default=40, help="Max public submissions pages to scan.")
    parser.add_argument("--sleep-seconds", type=float, default=0.25, help="Pause between page fetches.")
    parser.add_argument(
        "--debug-first-page",
        action="store_true",
        help="Print a compact sample of parsed cards from page 1 instead of building a dataset.",
    )
    parser.add_argument(
        "--debug-html-page",
        type=int,
        default=None,
        help="Fetch one public submissions HTML page and print a small diagnostic sample.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.debug_html_page is not None:
        html_text = _get_text(SUBMISSIONS_URL.format(page=args.debug_html_page))
        print(html_text[:4000])
        first_forum = html_text.find("/forum?id=")
        if first_forum != -1:
            print("FIRST_FORUM_SNIPPET_START")
            print(html_text[first_forum:first_forum + 2000])
            print("FIRST_FORUM_SNIPPET_END")
        print("forum_links", re.findall(r"/forum\?id=([A-Za-z0-9_-]+)", html_text)[:10])
        print("desk_reject_hits", html_text.count("Desk Rejected Submission"))
        print("submitted_hits", html_text.count("Submitted to ICLR 2026"))
        return

    if args.debug_first_page:
        cards = _parse_cards(_get_text(SUBMISSIONS_URL.format(page=1)))
        print(json.dumps({"card_count": len(cards)}))
        for card in cards[:10]:
            print(json.dumps({"id": card["id"], "title": card["title"], "status": card["status"], "classification": _classify_status(card["status"])}))
        return

    if args.total <= 0 or args.total % 2 != 0:
        raise SystemExit("--total must be a positive even integer")

    per_class = args.total // 2
    excluded_ids = load_excluded_ids(args.exclude_manifest)
    candidates = fetch_candidate_notes(
        per_class=max(per_class * 3, per_class),
        max_pages=args.max_pages,
        sleep_seconds=args.sleep_seconds,
    )

    rng = random.Random(args.seed)
    selected = {}
    for label, notes in candidates.items():
        notes = [note for note in notes if note["id"] not in excluded_ids]
        if len(notes) < per_class:
            raise SystemExit(
                f"Only found {len(notes)} candidates for {label}, need {per_class}. "
                "Try a smaller total or a larger crawl."
            )
        shuffled = notes[:]
        rng.shuffle(shuffled)
        selected[label] = shuffled[:per_class]

    write_dataset(args.outdir, selected, seed=args.seed)
    print(f"Wrote balanced batch to {args.outdir}")


if __name__ == "__main__":
    main()
