"""Greedy parent selection (arms A/B): argmax macro-average (PLAN.md s13)."""
from __future__ import annotations

from typing import Mapping, Sequence

from optimizer.pareto import macro_average

__all__ = ["select_parent_greedy"]


def select_parent_greedy(scores: Mapping[str, Sequence[float]]) -> str:
    """Candidate with the highest macro-average over the Pareto instances.

    Stable, deterministic tiebreak: the lexicographically smallest
    candidate_id among the tied best.
    """
    if not scores:
        raise ValueError("empty score table")
    return min(
        scores,
        key=lambda cid: (-macro_average(scores[cid]), cid),
    )
