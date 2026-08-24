"""Evaluation: deterministic match scoring (statistics module comes later)."""
from evaluation.scoring import (
    BACKSTAB_WEIGHTS,
    COOP_WEIGHTS,
    DEFAULT_MARGIN_LAMBDA,
    NONDETERMINISTIC_WIN_TYPES,
    POINT_METRICS,
    game_outcome,
    game_score,
    is_backstabbed,
    margin_lambda_from_config,
    points_fractions,
    points_margin,
    stats_from_footer,
    unpack_alive_rat_kings,
)

__all__ = [
    "BACKSTAB_WEIGHTS",
    "COOP_WEIGHTS",
    "DEFAULT_MARGIN_LAMBDA",
    "NONDETERMINISTIC_WIN_TYPES",
    "POINT_METRICS",
    "game_outcome",
    "game_score",
    "is_backstabbed",
    "margin_lambda_from_config",
    "points_fractions",
    "points_margin",
    "stats_from_footer",
    "unpack_alive_rat_kings",
]
