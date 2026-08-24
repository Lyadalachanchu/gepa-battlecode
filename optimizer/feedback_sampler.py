"""Paired-design scenario scheduling (PLAN.md sections 12-14).

All randomness here derives ONLY from (optimizer_seed, iteration, stream
name) via sha256 -> random.Random.  The arm never enters the derivation, so
every arm at the same seed and iteration index sees the same schedule
(common random numbers): lucky scenarios cancel in paired differences.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Optional, Sequence

__all__ = [
    "Scenario",
    "ScenarioSchedule",
    "select_reflection_instances",
    "map_to_feedback_scenarios",
    "disjoint_minibatch",
]


@dataclass(frozen=True, order=True)
class Scenario:
    """One playable cell: (opponent, map, side).  Deterministic outcome."""

    opponent: str
    map_name: str
    side: str

    def key(self) -> tuple[str, str, str]:
        return (self.opponent, self.map_name, self.side)


class ScenarioSchedule:
    """Deterministic per-iteration RNG streams for the paired design.

    Two ScenarioSchedule objects built with the same seed (even in different
    processes or different arms) produce identical streams for the same
    (iteration, stream) pair.  ``pool`` optionally holds the feedback-set
    scenario universe used by :func:`disjoint_minibatch`.
    """

    def __init__(self, seed: int, pool: Sequence[Scenario] = ()):
        self.seed = int(seed)
        self.pool: tuple[Scenario, ...] = tuple(pool)

    def rng_for(self, iteration: int, stream: str = "default") -> random.Random:
        material = f"gepa-battlecode:{self.seed}:{int(iteration)}:{stream}"
        digest = hashlib.sha256(material.encode("utf-8")).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))


def select_reflection_instances(
    parent_scores: Sequence[float],
    pool_best_scores: Sequence[float],
    k: int,
) -> list[int]:
    """Indices of the parent's weakest Pareto instances.

    Primary ranking: gap = pool_best - parent, descending (largest deficit
    first).  Fallback when the parent leads everywhere (all gaps <= 0):
    the parent's absolute-weakest instances (lowest parent score first).
    Ties break by instance index for determinism.
    """
    if len(parent_scores) != len(pool_best_scores):
        raise ValueError("parent/pool score length mismatch")
    n = len(parent_scores)
    if n == 0 or k <= 0:
        return []
    gaps = [pool_best_scores[i] - parent_scores[i] for i in range(n)]
    tol = 1e-12
    if all(g <= tol for g in gaps):
        order = sorted(range(n), key=lambda i: (parent_scores[i], i))
    else:
        order = sorted(range(n), key=lambda i: (-gaps[i], i))
    return order[: min(k, n)]


def map_to_feedback_scenarios(
    instances: Sequence[int],
    dev_scenarios: Sequence[Scenario],
    feedback_maps: Sequence[str],
    rng: random.Random,
) -> list[Scenario]:
    """Map Pareto instances to analogous feedback-set scenarios.

    Keeps each instance's opponent and side, swaps its (Pareto) map for a
    feedback map drawn with ``rng``.  Never reuses a Pareto map: the model
    must never see Pareto replays (PLAN section 12).
    """
    if not feedback_maps:
        raise ValueError("no feedback maps")
    out: list[Scenario] = []
    seen: set[tuple[str, str, str]] = set()
    for idx in instances:
        base = dev_scenarios[idx]
        candidates = sorted(feedback_maps)
        chosen = rng.choice(candidates)
        scenario = Scenario(base.opponent, chosen, base.side)
        # Avoid duplicate scenarios when two weak instances share opponent+side.
        tries = 0
        while scenario.key() in seen and tries < len(candidates):
            chosen = rng.choice(candidates)
            scenario = Scenario(base.opponent, chosen, base.side)
            tries += 1
        seen.add(scenario.key())
        out.append(scenario)
    return out


def disjoint_minibatch(
    schedule: ScenarioSchedule,
    iteration: int,
    exclude: Sequence[Scenario],
    n: int,
    pool: Optional[Sequence[Scenario]] = None,
) -> list[Scenario]:
    """``n`` acceptance scenarios disjoint from the reflection ones.

    Drawn without replacement from the feedback pool by the paired-schedule
    rng for this iteration (stream "minibatch").  Raises if the pool cannot
    supply ``n`` disjoint scenarios.
    """
    universe = tuple(pool) if pool is not None else schedule.pool
    excluded = {s.key() for s in exclude}
    eligible = sorted(s for s in universe if s.key() not in excluded)
    if len(eligible) < n:
        raise ValueError(
            f"pool has only {len(eligible)} scenarios disjoint from the "
            f"reflection set; {n} requested"
        )
    rng = schedule.rng_for(iteration, "minibatch")
    return rng.sample(eligible, n)
