"""Acceptance gate (PLAN.md section 13).

Decides whether a child candidate replaces/enters the pool, from mean scores
on the acceptance minibatch (reflection scenarios + disjoint scenarios).

Order of checks:
1. New exceptions the parent didn't have -> reject.
2. Strict mean improvement -> accept.
3. Exact tie -> neutral-drift accept iff the sources actually differ and the
   lineage has fewer than ``max_consecutive_neutral_accepts`` consecutive
   neutral accepts; otherwise reject.
4. Anything else (worse mean) -> reject.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

__all__ = ["GateConfig", "GateDecision", "accept_child"]

# Two floats that should be byte-identical under the deterministic engine;
# tolerance only guards float-summation dust in the means.
_TIE_TOL = 1e-12


@dataclass(frozen=True)
class GateConfig:
    max_consecutive_neutral_accepts: int = 2
    reject_new_exceptions: bool = True


@dataclass(frozen=True)
class GateDecision:
    accepted: bool
    reason: str
    neutral: bool = False


def accept_child(
    parent_scores: Sequence[float],
    child_scores: Sequence[float],
    sources_differ: bool,
    consecutive_neutral: int,
    child_new_exceptions: bool,
    cfg: GateConfig,
) -> GateDecision:
    """Gate a child against its parent on the acceptance minibatch.

    ``parent_scores`` / ``child_scores`` are section-11 game scores on the
    same scenarios, in the same order.  ``consecutive_neutral`` is the count
    of neutral-drift accepts already chained in this lineage.
    """
    if len(parent_scores) != len(child_scores) or not child_scores:
        raise ValueError(
            f"score lists must be same nonzero length, got "
            f"{len(parent_scores)} vs {len(child_scores)}"
        )

    if cfg.reject_new_exceptions and child_new_exceptions:
        return GateDecision(False, "new_exceptions", neutral=False)

    parent_mean = sum(parent_scores) / len(parent_scores)
    child_mean = sum(child_scores) / len(child_scores)

    if child_mean > parent_mean + _TIE_TOL:
        return GateDecision(True, "mean_improvement", neutral=False)

    if abs(child_mean - parent_mean) <= _TIE_TOL:
        if not sources_differ:
            return GateDecision(False, "tie_identical_sources", neutral=True)
        if consecutive_neutral < cfg.max_consecutive_neutral_accepts:
            return GateDecision(True, "neutral_drift", neutral=True)
        return GateDecision(False, "neutral_cap_reached", neutral=True)

    return GateDecision(False, "mean_regression", neutral=False)
