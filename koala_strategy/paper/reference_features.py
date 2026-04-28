from __future__ import annotations

import re


def reference_year_stats(references: list[str]) -> dict[str, float | int]:
    years = [int(y) for y in re.findall(r"\b(20[0-2]\d|19[8-9]\d)\b", "\n".join(references))]
    if not years:
        return {"reference_year_count": 0, "recent_reference_ratio": 0.0, "median_reference_year": 0}
    years_sorted = sorted(years)
    median = years_sorted[len(years_sorted) // 2]
    return {
        "reference_year_count": len(years),
        "recent_reference_ratio": sum(y >= 2022 for y in years) / len(years),
        "median_reference_year": median,
    }

