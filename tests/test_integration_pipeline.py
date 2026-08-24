"""Cross-package integration: fixture replay -> decoder -> scoring -> trace."""
from pathlib import Path

from evaluation.scoring import (
    game_outcome,
    game_score,
    is_backstabbed,
    points_margin,
    stats_from_footer,
    unpack_alive_rat_kings,
)
from replay import TraceConfig, build_trace, count_tokens, decode_footer, decode_match

FIXTURE = Path(__file__).parent / "fixtures" / "smoke.bc26"


def test_unpack_alive_rat_kings():
    assert unpack_alive_rat_kings(0) == (0, 0)
    assert unpack_alive_rat_kings(1) == (1, 0)
    # 2 kings, 2500 global cheese -> engine packs 2 + 10*2500
    assert unpack_alive_rat_kings(25002) == (2, 2500)


def test_footer_to_score_pipeline():
    footer = decode_footer(FIXTURE)
    assert footer["win_type"] in {
        "RESIGNATION",
        "RATKING_DESTROYED",
        "BACKSTAB_RATKING_DESTROYED",
        "MORE_POINTS",
        "MORE_ROBOTS",
        "MORE_CHEESE",
        "TIE",
        "COIN_FLIP",
    }
    stats = stats_from_footer(footer)
    assert set(stats.keys()) == {"A", "B"}
    for team_stats in stats.values():
        # Kings must be the unpacked low digit, not the packed engine value.
        assert 0 <= team_stats["alive_kings"] <= 5
        assert team_stats["global_cheese"] >= 0

    backstabbed = is_backstabbed(footer["win_type"])
    for side in ("A", "B"):
        outcome = game_outcome(footer["win_type"], footer["winner"], side)
        margin = points_margin(stats, backstabbed, side)
        score = game_score(outcome, margin)
        assert outcome - 0.1 <= score <= outcome + 0.1
    # The mirror-match fixture ends in a coin flip: both sides score 0.5.
    if footer["win_type"] in {"TIE", "COIN_FLIP"}:
        assert game_outcome(footer["win_type"], footer["winner"], "A") == 0.5
        assert game_outcome(footer["win_type"], footer["winner"], "B") == 0.5


def test_decode_and_trace_under_budget():
    decoded = decode_match(FIXTURE)
    assert decoded["footer"]["total_rounds"] > 0
    cfg = TraceConfig.from_experiment_yaml()
    trace = build_trace(decoded, cfg)
    assert count_tokens(trace) <= cfg.replay_token_budget
