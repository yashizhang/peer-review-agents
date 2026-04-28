from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

import numpy as np

from koala_strategy.schemas import ICLRTrainingExample, PaperRecord


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def title_similarity(a: str | None, b: str | None) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def abstract_similarity(a: str | None, b: str | None) -> float:
    aa = _norm(a)
    bb = _norm(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    a_words = set(aa.split())
    b_words = set(bb.split())
    jaccard = len(a_words & b_words) / max(1, len(a_words | b_words))
    seq = SequenceMatcher(None, aa[:4000], bb[:4000]).ratio()
    return 0.55 * seq + 0.45 * jaccard


def embedding_similarity(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None:
        return 0.0
    a = np.asarray(a).reshape(-1)
    b = np.asarray(b).reshape(-1)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def is_near_duplicate(
    koala_paper: PaperRecord,
    train_paper: ICLRTrainingExample,
    koala_embedding: np.ndarray | None = None,
    train_embedding: np.ndarray | None = None,
) -> bool:
    return (
        title_similarity(koala_paper.title, train_paper.title) >= 0.85
        or abstract_similarity(koala_paper.abstract, train_paper.abstract) >= 0.92
        or embedding_similarity(koala_embedding, train_embedding) >= 0.90
    )


def filter_contaminated_neighbors(
    koala_paper: PaperRecord,
    candidates: list[ICLRTrainingExample],
    max_return: int | None = None,
) -> list[ICLRTrainingExample]:
    clean = [candidate for candidate in candidates if not is_near_duplicate(koala_paper, candidate)]
    return clean[:max_return] if max_return is not None else clean

