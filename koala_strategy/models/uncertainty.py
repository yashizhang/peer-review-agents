from __future__ import annotations

from koala_strategy.utils import clamp


def combine_uncertainty(p_a: float, p_b: float, panel_disagreement: float = 0.0) -> float:
    disagreement = abs(float(p_a) - float(p_b))
    edge_uncertainty = 1.0 - min(1.0, abs(float(p_a + p_b) / 2.0 - 0.5) * 2.0)
    return clamp(0.45 * disagreement + 0.35 * edge_uncertainty + 0.20 * float(panel_disagreement), 0.0, 1.0)

