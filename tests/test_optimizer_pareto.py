"""Tests for optimizer/pareto.py and optimizer/greedy.py."""
import random
from collections import Counter

import pytest

from optimizer.greedy import select_parent_greedy
from optimizer.pareto import (
    frontier_members,
    macro_average,
    macro_averages,
    per_instance_best_sets,
    select_parent,
)


class TestHelpers:
    def test_macro_average(self):
        assert macro_average([1.0, 0.0, 0.5, 0.5]) == pytest.approx(0.5)
        assert macro_averages({"a": (1.0, 0.0), "b": (0.5, 0.5)}) == {
            "a": pytest.approx(0.5),
            "b": pytest.approx(0.5),
        }

    def test_per_instance_best_sets_with_ties(self):
        scores = {"a": (1.0, 0.5), "b": (1.0 - 1e-12, 0.9)}
        best = per_instance_best_sets(scores)
        assert best[0] == {"a", "b"}  # tie within 1e-9
        assert best[1] == {"b"}

    def test_inconsistent_lengths_rejected(self):
        with pytest.raises(ValueError):
            per_instance_best_sets({"a": (1.0,), "b": (1.0, 2.0)})
        with pytest.raises(ValueError):
            select_parent({}, random.Random(0))


class TestFrontier:
    def test_specialist_survives(self):
        # b is much worse on average but uniquely best on instance 2:
        # a Pareto specialist that greedy would never pick.
        scores = {
            "a": (1.0, 1.0, 0.0, 1.0),
            "b": (0.0, 0.0, 1.0, 0.0),
        }
        assert frontier_members(scores) == ["a", "b"]

    def test_dominated_union_member_pruned(self):
        # c ties a on instance 0 (so it enters the union) but is weakly
        # dominated by a (equal there, strictly worse on instance 1).
        scores = {
            "a": (1.0, 1.0),
            "c": (1.0, 0.5),
        }
        assert frontier_members(scores) == ["a"]

    def test_non_leader_never_in_frontier(self):
        scores = {
            "a": (1.0, 0.0),
            "b": (0.0, 1.0),
            "mediocre": (0.6, 0.6),  # decent everywhere, best nowhere
        }
        assert frontier_members(scores) == ["a", "b"]


class TestSelectParent:
    def test_single_candidate(self):
        assert select_parent({"only": (0.3, 0.3)}, random.Random(0)) == "only"

    def test_one_leader_always_returned(self):
        scores = {
            "champ": (1.0, 1.0, 1.0),
            "x": (0.9, 1.0, 0.2),
            "y": (0.0, 0.5, 0.9),
        }
        for seed in range(20):
            assert select_parent(scores, random.Random(seed)) == "champ"

    def test_all_identical_uniform(self):
        scores = {c: (0.5, 0.5, 0.5) for c in ("a", "b", "c")}
        counts = Counter(
            select_parent(scores, random.Random(seed)) for seed in range(600)
        )
        assert set(counts) == {"a", "b", "c"}
        for c in counts.values():
            assert 130 <= c <= 270  # ~200 each

    def test_proportional_to_coverage(self):
        # a leads 3 instances, b leads 1: expect ~3:1 sampling.
        scores = {
            "a": (1.0, 1.0, 1.0, 0.0),
            "b": (0.0, 0.0, 0.0, 1.0),
        }
        counts = Counter(
            select_parent(scores, random.Random(seed)) for seed in range(2000)
        )
        frac_a = counts["a"] / 2000
        assert 0.70 <= frac_a <= 0.80  # expected 0.75

    def test_dominated_candidate_never_sampled(self):
        scores = {
            "a": (1.0, 1.0, 0.2),
            "dominated": (1.0, 0.9, 0.1),  # ties instance 0, worse elsewhere
            "b": (0.0, 0.0, 1.0),
        }
        picks = {select_parent(scores, random.Random(seed)) for seed in range(300)}
        assert "dominated" not in picks
        assert picks == {"a", "b"}

    def test_deterministic_given_rng_state(self):
        scores = {"a": (1.0, 0.0), "b": (0.0, 1.0)}
        assert select_parent(scores, random.Random(42)) == select_parent(
            scores, random.Random(42)
        )


class TestGreedy:
    def test_argmax_macro_average(self):
        scores = {
            "worse": (0.2, 0.2, 0.2),
            "best": (0.9, 0.1, 0.9),
            "mid": (0.5, 0.5, 0.5),
        }
        assert select_parent_greedy(scores) == "best"

    def test_greedy_ignores_specialists(self):
        # Contrast with Pareto: greedy picks the generalist.
        scores = {
            "generalist": (0.6, 0.6, 0.6, 0.6),
            "specialist": (1.0, 0.0, 0.0, 0.0),
        }
        assert select_parent_greedy(scores) == "generalist"

    def test_stable_tiebreak_by_id(self):
        scores = {"bbb": (0.5, 0.5), "aaa": (0.5, 0.5), "ccc": (0.4, 0.6)}
        assert select_parent_greedy(scores) == "aaa"

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            select_parent_greedy({})
