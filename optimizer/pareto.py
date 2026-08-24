"""GEPA Pareto candidate selection over per-instance scores (PLAN.md s13).

``scores`` maps candidate_id -> tuple of per-instance scores (one entry per
Pareto instance; 48 in the real experiment, arbitrary length here as long as
all candidates share it).

Algorithm (GEPA Algorithm 2, including dominance pruning):
1. Per instance, find the best score and collect candidates tied with it
   (ties within ``tol``).
2. Take the union of those tied-best sets.
3. Prune union members weakly dominated by another union member
   (<= everywhere and < somewhere).
4. Coverage = number of instances a surviving candidate leads.
5. Sample the parent proportional to coverage with the provided rng.
"""
from __future__ import annotations

import random
from typing import Mapping, Sequence

__all__ = [
    "select_parent",
    "frontier_members",
    "per_instance_best_sets",
    "macro_average",
    "macro_averages",
    "DEFAULT_TOL",
]

DEFAULT_TOL = 1e-9

Scores = Mapping[str, Sequence[float]]


def _check(scores: Scores) -> int:
    if not scores:
        raise ValueError("empty score table")
    lengths = {len(v) for v in scores.values()}
    if len(lengths) != 1:
        raise ValueError(f"inconsistent instance counts: {sorted(lengths)}")
    (n,) = lengths
    if n == 0:
        raise ValueError("zero instances")
    return n


def macro_average(instance_scores: Sequence[float]) -> float:
    return sum(instance_scores) / len(instance_scores)


def macro_averages(scores: Scores) -> dict[str, float]:
    return {cid: macro_average(v) for cid, v in scores.items()}


def per_instance_best_sets(scores: Scores, tol: float = DEFAULT_TOL) -> list[set[str]]:
    """For each instance, the set of candidates within ``tol`` of the max."""
    n = _check(scores)
    ids = sorted(scores)
    best_sets: list[set[str]] = []
    for i in range(n):
        best = max(scores[cid][i] for cid in ids)
        best_sets.append({cid for cid in ids if scores[cid][i] >= best - tol})
    return best_sets


def _weakly_dominates(a: Sequence[float], b: Sequence[float], tol: float) -> bool:
    """True iff a >= b everywhere (within tol) and a > b somewhere (beyond tol)."""
    ge_everywhere = all(a[i] >= b[i] - tol for i in range(len(a)))
    gt_somewhere = any(a[i] > b[i] + tol for i in range(len(a)))
    return ge_everywhere and gt_somewhere


def frontier_members(scores: Scores, tol: float = DEFAULT_TOL) -> list[str]:
    """The pruned union: candidates that lead >=1 instance and are not weakly
    dominated by another union member.  Sorted by candidate_id."""
    best_sets = per_instance_best_sets(scores, tol)
    union = sorted(set().union(*best_sets))
    survivors = [
        c
        for c in union
        if not any(
            d != c and _weakly_dominates(scores[d], scores[c], tol) for d in union
        )
    ]
    return survivors


def select_parent(
    scores: Scores,
    rng: random.Random,
    tol: float = DEFAULT_TOL,
) -> str:
    """Sample the next parent per GEPA: frontier members weighted by the
    number of instances they lead.

    Degenerate cases: a single candidate is returned outright; a candidate
    that leads every instance (after pruning it is the sole survivor) is
    always returned; identical candidates (all-tied everywhere) are sampled
    uniformly.
    """
    _check(scores)
    if len(scores) == 1:
        return next(iter(scores))
    best_sets = per_instance_best_sets(scores, tol)
    survivors = frontier_members(scores, tol)
    if len(survivors) == 1:
        return survivors[0]
    coverage = {c: sum(1 for s in best_sets if c in s) for c in survivors}
    total = sum(coverage.values())
    # Every survivor leads >= 1 instance by construction, so total >= 1.
    pick = rng.random() * total
    acc = 0.0
    for c in survivors:  # sorted order -> deterministic given the rng draw
        acc += coverage[c]
        if pick < acc:
            return c
    return survivors[-1]
