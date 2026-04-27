"""Shared calibration utilities for the ICLR desk-reject pipeline."""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
import json


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def load_rows(runs_dir: Path) -> list[dict]:
    rows = []
    for result_path in sorted(runs_dir.glob("*/result.json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        paper_text = (result_path.parent / "paper.txt").read_text(encoding="utf-8", errors="replace")
        doc = "\n".join(
            [
                result["title"],
                f"agent_prediction {result['prediction']}",
                result["reason"],
                paper_text[:8000],
                paper_text[-4000:],
            ]
        )
        rows.append(
            {
                "forum_id": result["forum_id"],
                "title": result["title"],
                "gold_label": result["gold_label"],
                "tokens": tokenize(doc),
            }
        )
    return rows


def fit_vocab(rows: list[dict], min_df: int) -> list[str]:
    doc_freq = Counter()
    for row in rows:
        doc_freq.update(set(row["tokens"]))
    return sorted(token for token, count in doc_freq.items() if count >= min_df)


def fit_bernoulli_nb(rows: list[dict], min_df: int) -> dict:
    vocab = fit_vocab(rows, min_df=min_df)
    vocab_set = set(vocab)
    class_docs = {True: 0, False: 0}
    word_docs = {True: Counter(), False: Counter()}

    for row in rows:
        label = row["gold_label"] == "desk_rejected"
        class_docs[label] += 1
        word_docs[label].update(set(token for token in row["tokens"] if token in vocab_set))

    return {
        "vocab": vocab,
        "class_docs": {
            "true": class_docs[True],
            "false": class_docs[False],
        },
        "word_docs": {
            "true": dict(word_docs[True]),
            "false": dict(word_docs[False]),
        },
        "train_size": len(rows),
        "min_df": min_df,
    }


def _class_count(model: dict, cls: bool) -> int:
    key = "true" if cls else "false"
    class_docs = model["class_docs"]
    return class_docs.get(key, class_docs.get(cls))


def _word_counter(model: dict, cls: bool) -> Counter:
    key = "true" if cls else "false"
    word_docs = model["word_docs"]
    return Counter(word_docs.get(key, word_docs.get(cls, {})))


def predict(model: dict, tokens: list[str]) -> tuple[bool, float, float]:
    vocab = model["vocab"]
    vocab_set = set(vocab)
    seen = set(token for token in tokens if token in vocab_set)
    scores = {}

    for cls in [True, False]:
        class_count = _class_count(model, cls)
        word_docs = _word_counter(model, cls)
        logp = math.log(class_count / model["train_size"])
        for word in vocab:
            pw = (word_docs[word] + 1) / (class_count + 2)
            logp += math.log(pw if word in seen else (1 - pw))
        scores[cls] = logp

    pred = scores[True] > scores[False]
    return pred, scores[True], scores[False]


def evaluate_rows(rows: list[dict], model: dict) -> list[dict]:
    results = []
    for row in rows:
        pred, logp_true, logp_false = predict(model, row["tokens"])
        results.append(
            {
                "forum_id": row["forum_id"],
                "title": row["title"],
                "gold_label": row["gold_label"],
                "prediction": "desk_rejected" if pred else "not_desk_rejected",
                "predicted_positive": pred,
                "correct": pred == (row["gold_label"] == "desk_rejected"),
                "logp_true": logp_true,
                "logp_false": logp_false,
            }
        )
    return results


def summarize_results(results: list[dict], note: str) -> dict:
    total = len(results)
    tp = sum(result["predicted_positive"] and result["gold_label"] == "desk_rejected" for result in results)
    tn = sum((not result["predicted_positive"]) and result["gold_label"] != "desk_rejected" for result in results)
    fp = sum(result["predicted_positive"] and result["gold_label"] != "desk_rejected" for result in results)
    fn = sum((not result["predicted_positive"]) and result["gold_label"] == "desk_rejected" for result in results)
    return {
        "total": total,
        "accuracy": (tp + tn) / total,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "note": note,
    }
