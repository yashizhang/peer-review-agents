from __future__ import annotations

from bisect import bisect_right

from koala_strategy.utils import clamp


def probability_to_quality_percentile(
    p_accept: float,
    domain: str | None = None,
    current_pool_distribution: list[float] | None = None,
) -> float:
    p = clamp(float(p_accept), 0.0, 1.0)
    if current_pool_distribution:
        sorted_pool = sorted(float(x) for x in current_pool_distribution)
        return bisect_right(sorted_pool, p) / len(sorted_pool)
    return p


def percentile_to_koala_score(q: float) -> float:
    q = clamp(float(q), 0.0, 1.0)
    if q < 0.20:
        score = 1.5 + 1.5 * q / 0.20
    elif q < 0.73:
        score = 3.0 + 2.0 * (q - 0.20) / 0.53
    elif q < 0.90:
        score = 5.0 + 2.0 * (q - 0.73) / 0.17
    elif q < 0.98:
        score = 7.0 + 2.0 * (q - 0.90) / 0.08
    else:
        score = 9.0 + 1.0 * (q - 0.98) / 0.02
    return clamp(score, 0.0, 10.0)


def shrink_score_for_uncertainty(score: float, uncertainty: float, prior_score: float = 4.8) -> float:
    confidence = clamp(1.0 - float(uncertainty), 0.0, 1.0)
    return clamp(confidence * float(score) + (1.0 - confidence) * prior_score, 0.0, 10.0)


def score_range(score: float, uncertainty: float) -> tuple[float, float]:
    width = 0.35 + 1.2 * clamp(uncertainty, 0.0, 1.0)
    return (round(clamp(score - width, 0, 10), 2), round(clamp(score + width, 0, 10), 2))

