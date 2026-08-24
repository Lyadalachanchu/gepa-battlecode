"""Dry-wire tests for scripts/run_pilot.py: the whole driver with fakes."""

import json
from pathlib import Path

import pytest

from optimizer.candidate import make_candidate
from optimizer.feedback_sampler import Scenario
from scripts.run_pilot import (
    Wiring,
    _prepare_run_dir,
    build_loop,
    build_scenario_pools,
    load_seed_candidate,
)
from experiment.scenarios import load_opponents

SEED_COMPONENTS = {
    "robotplayer": "public class RobotPlayer {}",
    "economy": "public class Economy { int v = 0; }",
    "combat": "public class Combat { int v = 0; }",
}

PARETO = [
    Scenario(o, "pmap", s) for o in ("opp1", "opp2") for s in ("A", "B")
]
FEEDBACK = [
    Scenario(o, m, s)
    for o in ("opp1", "opp2")
    for m in ("fm1", "fm2", "fm3", "fm4")
    for s in ("A", "B")
]


class FakeStack:
    """Deterministic fakes implementing all four injected callables."""

    def __init__(self, seed_id):
        self.seed_id = seed_id
        self.decode_calls = 0
        self.model_calls = []

    def run_scenario(self, candidate, scenario):
        base = 0.5 if candidate.candidate_id == self.seed_id else 0.7
        return {
            "score": base,
            "new_exceptions": False,
            "cache_hit": False,
            "replay_path": "/fake/replay.bc26",
            "opponent": scenario.opponent,
            "map_name": scenario.map_name,
            "side": scenario.side,
        }

    def decode_traces(self, records):
        self.decode_calls += 1
        return f"PACKED TRACES ({len(records)} games)"

    def model_call(self, payload):
        self.model_calls.append(payload)
        if payload.get("repair"):
            return {"component_source": payload.get("previous_source", "")}
        comp = payload["target_component"]
        return {
            "action": "patch",
            "target_component": comp,
            "component_source": (
                f"public class {comp.capitalize()} "
                f"{{ int v = {payload['iteration'] + 1}; }}"
            ),
        }

    def compile_check(self, components):
        return True, ""

    def wiring(self):
        return Wiring(
            run_scenario=self.run_scenario,
            decode_traces=self.decode_traces,
            model_call=self.model_call,
            compile_check=self.compile_check,
        )


def _run_two_iterations(tmp_path, arm_name):
    seed = make_candidate(SEED_COMPONENTS)
    stack = FakeStack(seed.candidate_id)
    loop = build_loop(
        arm_name=arm_name,
        optimizer_seed=0,
        calls=10,
        matches=500,
        run_dir=tmp_path / arm_name,
        wiring=stack.wiring(),
        seed_candidate=seed,
        components=("economy", "combat"),
        pareto_scenarios=PARETO,
        feedback_pool=FEEDBACK,
        iterations=2,
    )
    summary = loop.run()
    return stack, summary, tmp_path / arm_name


def test_two_iterations_replay_greedy(tmp_path):
    stack, summary, run_dir = _run_two_iterations(tmp_path, "replay_greedy")
    assert summary["iterations_completed"] == 2
    assert summary["stopped"] is None
    assert summary["model_calls_used"] == 2
    assert summary["pool_size"] == 3  # seed + 2 accepted children
    assert summary["best_macro_average"] == pytest.approx(0.7)

    state_lines = [
        json.loads(line)
        for line in (run_dir / "state.jsonl").read_text().splitlines()
    ]
    iters = [r for r in state_lines if r.get("event") == "iteration"]
    assert len(iters) == 2
    assert [r["component"] for r in iters] == ["economy", "combat"]
    assert all(r["result"] == "accepted" for r in iters)
    assert (run_dir / "run_meta.json").exists()

    # Arm B: the model payload carries decoded traces.
    assert stack.decode_calls == 2
    assert all("traces" in p for p in stack.model_calls)


def test_two_iterations_score_greedy_never_decodes(tmp_path):
    stack, summary, _ = _run_two_iterations(tmp_path, "score_greedy")
    assert summary["iterations_completed"] == 2
    assert stack.decode_calls == 0
    assert all("traces" not in p for p in stack.model_calls)
    assert all("scores" in p for p in stack.model_calls)


def test_match_budget_hard_stop(tmp_path):
    seed = make_candidate(SEED_COMPONENTS)
    stack = FakeStack(seed.candidate_id)
    loop = build_loop(
        arm_name="replay_greedy",
        optimizer_seed=0,
        calls=10,
        matches=5,  # < the 4 pareto instances + reflection matches
        run_dir=tmp_path / "budget",
        wiring=stack.wiring(),
        seed_candidate=seed,
        components=("economy", "combat"),
        pareto_scenarios=PARETO,
        feedback_pool=FEEDBACK,
        iterations=2,
    )
    summary = loop.run()
    assert summary["stopped"] == "matches"
    assert summary["matches_run"] == 5


def test_load_seed_candidate_uses_modular_seed():
    cand, mutable, is_modular = load_seed_candidate()
    assert is_modular is True
    assert set(cand.components) == {
        "robotplayer", "economy", "combat", "defense", "navigation", "strategy",
    }
    assert "robotplayer" not in mutable  # glue is never mutable
    # Frozen round-robin order from configs/experiment.yaml (PLAN.md s14).
    assert mutable == ("economy", "combat", "defense", "navigation", "strategy")


def test_build_scenario_pools_from_real_locks():
    opponents = load_opponents()
    pareto, feedback = build_scenario_pools(opponents)
    dev_usable = [
        o for o in opponents.values()
        if o.split == "dev" and o.compiled and o.smoke_ok
    ]
    assert len(pareto) == len(dev_usable) * 4 * 2  # 4 pinned Pareto maps, 2 sides
    assert len(pareto) == 48  # PLAN.md section 12 with the full dev pool
    pareto_maps = {s.map_name for s in pareto}
    feedback_maps = {s.map_name for s in feedback}
    assert pareto_maps.isdisjoint(feedback_maps)
    assert len(feedback) >= 8


def test_prepare_run_dir_refuses_accidental_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.run_pilot.PILOT_ROOT", tmp_path)
    run_dir = _prepare_run_dir("score_greedy", 0, resume=None)
    assert run_dir == tmp_path / "score_greedy-s0"
    (run_dir / "state.jsonl").write_text("{}\n")
    with pytest.raises(FileExistsError, match="--resume"):
        _prepare_run_dir("score_greedy", 0, resume=None)
    # Resume archives the old state file.
    resumed = _prepare_run_dir("score_greedy", 0, resume=str(run_dir))
    assert resumed == run_dir
    assert not (run_dir / "state.jsonl").exists()
    assert (run_dir / "state.jsonl.bak1").exists()
