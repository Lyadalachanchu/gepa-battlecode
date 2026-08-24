"""Match scoring (PLAN.md section 11).

Pure, deterministic functions computing per-game scores from replay-derived
facts: the MatchFooter winType/winner and the final-round per-team aggregates.

Conventions
-----------
* ``side`` and ``winner`` are team keys, normally ``"A"`` / ``"B"``.
* ``final_team_stats`` maps exactly two team keys to a stats mapping with keys
  ``cat_damage``, ``alive_kings``, ``cheese_transferred`` (numbers).
* ``win_type`` is a WinType enum *name*: RESIGNATION, RATKING_DESTROYED,
  BACKSTAB_RATKING_DESTROYED, MORE_POINTS, MORE_ROBOTS, MORE_CHEESE, TIE,
  COIN_FLIP.

Reported metrics use ``game_outcome`` only; search internals (gate, Pareto
instance scores) use ``game_score`` = outcome + lambda * margin.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

__all__ = [
    "DEFAULT_MARGIN_LAMBDA",
    "NONDETERMINISTIC_WIN_TYPES",
    "COOP_WEIGHTS",
    "BACKSTAB_WEIGHTS",
    "POINT_METRICS",
    "game_outcome",
    "points_fractions",
    "points_margin",
    "game_score",
    "is_backstabbed",
    "margin_lambda_from_config",
    "unpack_alive_rat_kings",
    "stats_from_footer",
]

# WinType names whose winner byte is meaningless for scoring: TIE means no
# winner, and COIN_FLIP is decided by uninstrumented Math.random() -- the one
# nondeterministic bit in the engine.  Both score 0.5 regardless of winner.
NONDETERMINISTIC_WIN_TYPES = frozenset({"TIE", "COIN_FLIP"})

# Official points formula weights over (cat_damage, alive_kings,
# cheese_transferred) fractional shares.
POINT_METRICS = ("cat_damage", "alive_kings", "cheese_transferred")
COOP_WEIGHTS = {"cat_damage": 0.5, "alive_kings": 0.3, "cheese_transferred": 0.2}
BACKSTAB_WEIGHTS = {"cat_damage": 0.3, "alive_kings": 0.5, "cheese_transferred": 0.2}

DEFAULT_MARGIN_LAMBDA = 0.1


def game_outcome(win_type: str, winner: str, side: str) -> float:
    """1.0 if ``side`` won, 0.0 if it lost, 0.5 for TIE/COIN_FLIP.

    Never trust the raw winner byte for tie-ish winTypes (PLAN section 5,
    rule 3): COIN_FLIP is the engine's only nondeterminism.
    """
    if win_type in NONDETERMINISTIC_WIN_TYPES:
        return 0.5
    return 1.0 if winner == side else 0.0


def _two_sides(final_team_stats: Mapping[str, Mapping[str, float]]) -> tuple[str, str]:
    keys = sorted(final_team_stats.keys())
    if len(keys) != 2:
        raise ValueError(f"expected exactly 2 teams, got {keys!r}")
    return keys[0], keys[1]


def points_fractions(
    final_team_stats: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, float]]:
    """Per-team fractional shares of each points metric.

    For each metric, team share = team_value / (sum over both teams); a zero
    denominator yields 0.5 shares for both teams (no information either way).
    Shares for the two teams always sum to exactly 1.0 per metric.
    """
    a, b = _two_sides(final_team_stats)
    out: dict[str, dict[str, float]] = {a: {}, b: {}}
    for metric in POINT_METRICS:
        va = float(final_team_stats[a][metric])
        vb = float(final_team_stats[b][metric])
        if va < 0 or vb < 0:
            raise ValueError(f"negative value for {metric}: {va}, {vb}")
        total = va + vb
        if total == 0:
            fa = 0.5
        else:
            fa = va / total
        out[a][metric] = fa
        out[b][metric] = 1.0 - fa
    return out


def points_margin(
    final_team_stats: Mapping[str, Mapping[str, float]],
    backstabbed: bool,
    side: str,
) -> float:
    """Normalized points differential in [-1, 1] from the official formulas.

    coop weights:     0.5 catDamage + 0.3 livingKings + 0.2 cheeseTransferred
    backstab weights: 0.3 catDamage + 0.5 livingKings + 0.2 cheeseTransferred

    margin = my_points_share - opp_points_share.  Since the per-metric shares
    of the two teams sum to 1 and the weights sum to 1, this equals
    2 * my_points_share - 1 and lies in [-1, 1].
    """
    a, b = _two_sides(final_team_stats)
    if side not in (a, b):
        raise ValueError(f"side {side!r} not in teams {(a, b)!r}")
    opp = b if side == a else a
    weights = BACKSTAB_WEIGHTS if backstabbed else COOP_WEIGHTS
    fracs = points_fractions(final_team_stats)
    my_points = sum(weights[m] * fracs[side][m] for m in POINT_METRICS)
    opp_points = sum(weights[m] * fracs[opp][m] for m in POINT_METRICS)
    margin = my_points - opp_points
    # Clamp against float dust only; mathematically already in [-1, 1].
    return max(-1.0, min(1.0, margin))


def game_score(
    outcome: float,
    margin: float,
    lam: float = DEFAULT_MARGIN_LAMBDA,
) -> float:
    """Search-internal continuous score: outcome + lam * margin.

    With lam = 0.1 and margin in [-1, 1], score lies in
    [outcome - 0.1, outcome + 0.1]: outcome always dominates (a win beats any
    loss regardless of margins).
    """
    if not -1.0 - 1e-12 <= margin <= 1.0 + 1e-12:
        raise ValueError(f"margin out of [-1, 1]: {margin}")
    return outcome + lam * margin


def is_backstabbed(win_type: str, backstab_flag: Optional[bool] = None) -> bool:
    """Whether the game ended in the backstab regime.

    True when the winType carries the BACKSTAB_ prefix, or when an explicit
    trace-derived flag (the cooperation -> backstab flip, a T2 event) says so.
    """
    if backstab_flag is not None and backstab_flag:
        return True
    return win_type.startswith("BACKSTAB_")


def margin_lambda_from_config(config_path: str | Path) -> float:
    """Read scoring.margin_lambda from an experiment.yaml file."""
    import yaml

    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return float(cfg["scoring"]["margin_lambda"])


def unpack_alive_rat_kings(raw: int) -> tuple[int, int]:
    """Unpack the replay's teamAliveRatKings field into (kings, global_cheese).

    The engine packs both stats into one int: GameWorld.java:1013 at the pinned
    commit writes ``numRatKings + 10 * teamCheese``.  Kings are capped at 5
    (GameConstants.MAX_NUMBER_OF_RAT_KINGS), so the low decimal digit is the
    true king count and the rest is the team's global cheese.
    """
    if raw < 0:
        raise ValueError(f"negative teamAliveRatKings: {raw}")
    return raw % 10, raw // 10


def stats_from_footer(
    footer: Mapping[str, object],
) -> dict[str, dict[str, float]]:
    """Adapt ``replay.decode_footer`` output to scoring's final_team_stats.

    Maps the decoder's per-team keys (``alive_rat_kings`` is the packed engine
    field) to the ``cat_damage`` / ``alive_kings`` / ``cheese_transferred``
    shape that ``points_margin`` consumes, plus ``global_cheese`` for the
    MORE_CHEESE tiebreak and diagnostics.
    """
    raw_stats = footer["final_team_stats"]  # type: ignore[index]
    out: dict[str, dict[str, float]] = {}
    for team, stats in raw_stats.items():  # type: ignore[union-attr]
        kings, cheese = unpack_alive_rat_kings(int(stats["alive_rat_kings"]))
        out[team] = {
            "cat_damage": float(stats["cat_damage"]),
            "alive_kings": float(kings),
            "cheese_transferred": float(stats["cheese_transferred"]),
            "global_cheese": float(cheese),
        }
    return out
