"""Tests for optimizer/feedback_sampler.py: paired-design determinism."""
import pytest

from optimizer.feedback_sampler import (
    Scenario,
    ScenarioSchedule,
    disjoint_minibatch,
    map_to_feedback_scenarios,
    select_reflection_instances,
)

OPPONENTS = ["examplefuncs", "lectureplayer", "sprint1bot"]
PARETO_MAPS = ["ParetoA", "ParetoB"]
FEEDBACK_MAPS = ["FeedA", "FeedB", "FeedC", "FeedD"]


def make_pool(maps, opponents=OPPONENTS):
    return [
        Scenario(o, m, side)
        for o in opponents
        for m in maps
        for side in ("A", "B")
    ]


DEV_SCENARIOS = make_pool(PARETO_MAPS)  # 12 "Pareto instances"
FEEDBACK_POOL = make_pool(FEEDBACK_MAPS)  # 24 feedback scenarios


class TestScheduleDeterminism:
    def test_same_seed_same_iteration_same_stream(self):
        # Two schedule objects (e.g. two different arms) share the streams:
        # the arm never enters the derivation.
        s1 = ScenarioSchedule(seed=7)
        s2 = ScenarioSchedule(seed=7)
        for it in range(5):
            r1 = s1.rng_for(it, "reflection")
            r2 = s2.rng_for(it, "reflection")
            assert [r1.random() for _ in range(10)] == [r2.random() for _ in range(10)]

    def test_different_iterations_differ(self):
        s = ScenarioSchedule(seed=7)
        assert s.rng_for(0).random() != s.rng_for(1).random()

    def test_different_seeds_differ(self):
        assert ScenarioSchedule(1).rng_for(0).random() != ScenarioSchedule(2).rng_for(0).random()

    def test_streams_are_independent(self):
        s = ScenarioSchedule(seed=7)
        assert s.rng_for(3, "reflection").random() != s.rng_for(3, "minibatch").random()


class TestReflectionInstances:
    def test_weakest_by_gap(self):
        parent = [1.0, 0.2, 0.9, 0.5]
        best = [1.0, 1.0, 0.9, 0.6]  # gaps: 0, 0.8, 0, 0.1
        assert select_reflection_instances(parent, best, 2) == [1, 3]

    def test_fallback_absolute_weakest_when_parent_leads_everywhere(self):
        parent = [0.9, 0.3, 0.7]
        best = [0.9, 0.3, 0.7]  # parent IS the pool best everywhere
        assert select_reflection_instances(parent, best, 2) == [1, 2]

    def test_ties_break_by_index(self):
        parent = [0.5, 0.5, 0.5]
        best = [1.0, 1.0, 1.0]
        assert select_reflection_instances(parent, best, 2) == [0, 1]

    def test_k_larger_than_n(self):
        assert select_reflection_instances([0.1], [0.9], 5) == [0]
        assert select_reflection_instances([], [], 3) == []

    def test_length_mismatch_rejected(self):
        with pytest.raises(ValueError):
            select_reflection_instances([0.1], [0.9, 0.9], 1)


class TestMapToFeedback:
    def test_keeps_opponent_and_side_swaps_map(self):
        sched = ScenarioSchedule(seed=3)
        rng = sched.rng_for(0, "reflection")
        out = map_to_feedback_scenarios([0, 5], DEV_SCENARIOS, FEEDBACK_MAPS, rng)
        assert len(out) == 2
        for idx, scen in zip([0, 5], out):
            base = DEV_SCENARIOS[idx]
            assert scen.opponent == base.opponent
            assert scen.side == base.side
            assert scen.map_name in FEEDBACK_MAPS
            assert scen.map_name not in PARETO_MAPS  # model never sees Pareto maps

    def test_deterministic_across_schedule_objects(self):
        a = map_to_feedback_scenarios(
            [1, 2, 3], DEV_SCENARIOS, FEEDBACK_MAPS, ScenarioSchedule(9).rng_for(4, "reflection")
        )
        b = map_to_feedback_scenarios(
            [1, 2, 3], DEV_SCENARIOS, FEEDBACK_MAPS, ScenarioSchedule(9).rng_for(4, "reflection")
        )
        assert a == b

    def test_no_feedback_maps_rejected(self):
        with pytest.raises(ValueError):
            map_to_feedback_scenarios([0], DEV_SCENARIOS, [], ScenarioSchedule(0).rng_for(0))


class TestDisjointMinibatch:
    def test_disjoint_from_reflection(self):
        sched = ScenarioSchedule(seed=11, pool=FEEDBACK_POOL)
        reflection = FEEDBACK_POOL[:3]
        batch = disjoint_minibatch(sched, iteration=2, exclude=reflection, n=4)
        assert len(batch) == 4
        refl_keys = {s.key() for s in reflection}
        assert all(s.key() not in refl_keys for s in batch)
        assert len({s.key() for s in batch}) == 4  # no duplicates

    def test_deterministic_and_arm_independent(self):
        # Two schedules (two arms) at the same seed+iteration draw the same batch.
        s1 = ScenarioSchedule(seed=11, pool=FEEDBACK_POOL)
        s2 = ScenarioSchedule(seed=11, pool=FEEDBACK_POOL)
        ex = FEEDBACK_POOL[:2]
        assert disjoint_minibatch(s1, 5, ex, 4) == disjoint_minibatch(s2, 5, ex, 4)

    def test_varies_with_iteration(self):
        s = ScenarioSchedule(seed=11, pool=FEEDBACK_POOL)
        batches = {tuple(disjoint_minibatch(s, it, [], 4)) for it in range(6)}
        assert len(batches) > 1

    def test_pool_too_small_rejected(self):
        s = ScenarioSchedule(seed=0, pool=FEEDBACK_POOL[:5])
        with pytest.raises(ValueError):
            disjoint_minibatch(s, 0, exclude=FEEDBACK_POOL[:3], n=4)

    def test_explicit_pool_override(self):
        s = ScenarioSchedule(seed=1)  # no pool stored
        batch = disjoint_minibatch(s, 0, exclude=[], n=2, pool=FEEDBACK_POOL)
        assert len(batch) == 2
