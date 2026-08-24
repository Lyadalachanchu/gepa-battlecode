"""Tests for evaluation/scoring.py (PLAN.md section 11)."""
import math

import pytest

from evaluation.scoring import (
    BACKSTAB_WEIGHTS,
    COOP_WEIGHTS,
    DEFAULT_MARGIN_LAMBDA,
    game_outcome,
    game_score,
    is_backstabbed,
    margin_lambda_from_config,
    points_fractions,
    points_margin,
)


def stats(a_cat, a_kings, a_cheese, b_cat, b_kings, b_cheese):
    return {
        "A": {"cat_damage": a_cat, "alive_kings": a_kings, "cheese_transferred": a_cheese},
        "B": {"cat_damage": b_cat, "alive_kings": b_kings, "cheese_transferred": b_cheese},
    }


class TestGameOutcome:
    def test_win(self):
        assert game_outcome("RATKING_DESTROYED", "A", "A") == 1.0

    def test_loss(self):
        assert game_outcome("MORE_POINTS", "A", "B") == 0.0

    @pytest.mark.parametrize("wt", ["TIE", "COIN_FLIP"])
    @pytest.mark.parametrize("winner", ["A", "B"])
    def test_tie_and_coin_flip_ignore_winner(self, wt, winner):
        # COIN_FLIP is Math.random() in the engine; never trust its winner byte.
        assert game_outcome(wt, winner, "A") == 0.5
        assert game_outcome(wt, winner, "B") == 0.5

    @pytest.mark.parametrize(
        "wt",
        [
            "RESIGNATION",
            "RATKING_DESTROYED",
            "BACKSTAB_RATKING_DESTROYED",
            "MORE_POINTS",
            "MORE_ROBOTS",
            "MORE_CHEESE",
        ],
    )
    def test_decisive_types_use_winner(self, wt):
        assert game_outcome(wt, "B", "B") == 1.0
        assert game_outcome(wt, "B", "A") == 0.0


class TestPointsFractions:
    def test_basic_shares(self):
        f = points_fractions(stats(30, 1, 70, 10, 3, 30))
        assert f["A"]["cat_damage"] == pytest.approx(0.75)
        assert f["B"]["cat_damage"] == pytest.approx(0.25)
        assert f["A"]["alive_kings"] == pytest.approx(0.25)
        assert f["A"]["cheese_transferred"] == pytest.approx(0.7)

    def test_shares_sum_to_one(self):
        f = points_fractions(stats(3, 2, 1, 5, 0, 9))
        for metric in ("cat_damage", "alive_kings", "cheese_transferred"):
            assert f["A"][metric] + f["B"][metric] == pytest.approx(1.0)

    def test_zero_denominator_gives_half_shares(self):
        f = points_fractions(stats(0, 0, 0, 0, 0, 0))
        for team in ("A", "B"):
            for metric in ("cat_damage", "alive_kings", "cheese_transferred"):
                assert f[team][metric] == 0.5

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            points_fractions(stats(-1, 0, 0, 1, 0, 0))

    def test_requires_two_teams(self):
        with pytest.raises(ValueError):
            points_fractions({"A": {"cat_damage": 1, "alive_kings": 1, "cheese_transferred": 1}})


class TestPointsMargin:
    def test_weights_sum_to_one(self):
        assert sum(COOP_WEIGHTS.values()) == pytest.approx(1.0)
        assert sum(BACKSTAB_WEIGHTS.values()) == pytest.approx(1.0)

    def test_symmetric_stats_zero_margin(self):
        s = stats(10, 2, 50, 10, 2, 50)
        assert points_margin(s, backstabbed=False, side="A") == pytest.approx(0.0)
        assert points_margin(s, backstabbed=True, side="B") == pytest.approx(0.0)

    def test_total_domination_is_plus_one(self):
        s = stats(10, 2, 50, 0, 0, 0)
        assert points_margin(s, backstabbed=False, side="A") == pytest.approx(1.0)
        assert points_margin(s, backstabbed=False, side="B") == pytest.approx(-1.0)

    def test_coop_weighting_exact(self):
        # A shares: cat 0.75, kings 0.25, cheese 0.7
        s = stats(30, 1, 70, 10, 3, 30)
        my = 0.5 * 0.75 + 0.3 * 0.25 + 0.2 * 0.7
        expected = my - (1.0 - my)
        assert points_margin(s, backstabbed=False, side="A") == pytest.approx(expected)

    def test_backstab_weighting_shifts_to_kings(self):
        # A dominates kings, B dominates cat damage; backstab must favor A more.
        s = stats(10, 4, 50, 90, 0, 50)
        coop = points_margin(s, backstabbed=False, side="A")
        backstab = points_margin(s, backstabbed=True, side="A")
        assert backstab > coop
        my_b = 0.3 * 0.1 + 0.5 * 1.0 + 0.2 * 0.5
        assert backstab == pytest.approx(2 * my_b - 1)

    def test_antisymmetric_between_sides(self):
        s = stats(3, 1, 9, 7, 2, 4)
        for bs in (False, True):
            assert points_margin(s, bs, "A") == pytest.approx(-points_margin(s, bs, "B"))

    def test_unknown_side_rejected(self):
        with pytest.raises(ValueError):
            points_margin(stats(1, 1, 1, 1, 1, 1), False, "C")


class TestGameScore:
    def test_default_lambda(self):
        assert DEFAULT_MARGIN_LAMBDA == pytest.approx(0.1)
        assert game_score(1.0, 0.5) == pytest.approx(1.05)
        assert game_score(0.0, -1.0) == pytest.approx(-0.1)

    def test_outcome_dominates(self):
        # A win with the worst margin still beats a loss with the best margin.
        assert game_score(1.0, -1.0) > game_score(0.0, 1.0)
        assert game_score(0.5, -1.0) > game_score(0.0, 1.0)

    def test_score_bounds(self):
        for outcome in (0.0, 0.5, 1.0):
            for margin in (-1.0, 0.0, 1.0):
                s = game_score(outcome, margin)
                assert outcome - 0.1 - 1e-12 <= s <= outcome + 0.1 + 1e-12

    def test_margin_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            game_score(1.0, 1.5)

    def test_explicit_lambda(self):
        assert game_score(0.5, 1.0, lam=0.25) == pytest.approx(0.75)


class TestBackstabbed:
    def test_from_win_type_prefix(self):
        assert is_backstabbed("BACKSTAB_RATKING_DESTROYED")
        assert not is_backstabbed("RATKING_DESTROYED")
        assert not is_backstabbed("MORE_POINTS")

    def test_flag_overrides(self):
        assert is_backstabbed("MORE_POINTS", backstab_flag=True)
        assert not is_backstabbed("MORE_POINTS", backstab_flag=False)
        # Flag False does not un-backstab an explicit BACKSTAB_ winType.
        assert is_backstabbed("BACKSTAB_RATKING_DESTROYED", backstab_flag=False)


def test_margin_lambda_from_experiment_yaml():
    from pathlib import Path

    cfg = Path(__file__).resolve().parent.parent / "configs" / "experiment.yaml"
    lam = margin_lambda_from_config(cfg)
    assert math.isclose(lam, 0.1)
