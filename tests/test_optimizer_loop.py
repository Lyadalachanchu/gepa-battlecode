"""Dry-run tests for optimizer/loop.py with injected fakes (no engine, no
network, no model): the full iteration skeleton for all four arms."""
import json
import zlib

import pytest

from optimizer.candidate import CandidateStore, make_candidate
from optimizer.feedback_sampler import Scenario
from optimizer.gate import GateConfig
from optimizer.loop import ARMS, LoopConfig, OptimizerLoop

COMPONENTS = ("economy", "combat")

PARETO_SCENARIOS = [
    Scenario(o, m, side)
    for o in ("opp1", "opp2")
    for m in ("P1", "P2")
    for side in ("A", "B")
]  # 8 instances
FEEDBACK_POOL = [
    Scenario(o, m, side)
    for o in ("opp1", "opp2")
    for m in ("F1", "F2", "F3", "F4")
    for side in ("A", "B")
]  # 16 scenarios


def seed_candidate():
    return make_candidate({c: f"{c} level=0" for c in COMPONENTS})


def strength(candidate):
    return sum(
        int(src.split("level=")[1].split()[0]) for src in candidate.components.values()
    )


def base_score(scenario):
    # Deterministic, process-stable per-scenario base in [0, 0.1).
    return (zlib.crc32("|".join(scenario.key()).encode()) % 100) / 1000.0


class FakeRunner:
    """Deterministic engine stand-in with an exact-cache: repeated
    (candidate, scenario) plays come back as cache hits."""

    def __init__(self):
        self.memo = {}

    def __call__(self, candidate, scenario):
        key = (candidate.candidate_id, scenario.key())
        hit = key in self.memo
        if not hit:
            self.memo[key] = {
                "score": 0.3 + base_score(scenario) + 0.02 * strength(candidate),
                "new_exceptions": False,
            }
        rec = dict(self.memo[key])
        rec["cache_hit"] = hit
        return rec


class FakeDecoder:
    def __init__(self):
        self.calls = 0

    def __call__(self, records):
        self.calls += 1
        return f"TRACE({len(records)} games)"


class FakeModel:
    """Always proposes bumping the target component's level by one (a strict
    improvement under FakeRunner).  Optionally emits one compile bug first,
    then fixes it on the repair call."""

    def __init__(self, buggy_first=False):
        self.calls = 0
        self.repair_calls = 0
        self.buggy_emitted = not buggy_first

    def __call__(self, payload):
        self.calls += 1
        if payload.get("repair"):
            self.repair_calls += 1
            fixed = payload["previous_source"].replace(" BUG", "")
            return {"component_source": fixed}
        comp = payload["target_component"]
        level = int(payload["components"][comp].split("level=")[1].split()[0])
        source = f"{comp} level={level + 1}"
        if not self.buggy_emitted:
            self.buggy_emitted = True
            source += " BUG"
        return {"action": "patch", "target_component": comp, "component_source": source}


class NoChangeModel:
    def __init__(self):
        self.calls = 0

    def __call__(self, payload):
        self.calls += 1
        return {"action": "no_change"}


def compile_check(components):
    if any("BUG" in src for src in components.values()):
        return False, "error: BUG"
    return True, ""


def build_loop(tmp_path, arm_name, *, model=None, runner=None, decoder=None,
               iterations=3, model_calls=50, matches=1000, seed=7, tag=""):
    return OptimizerLoop(
        arm=ARMS[arm_name],
        cfg=LoopConfig(
            iterations=iterations,
            model_call_budget=model_calls,
            match_budget=matches,
            components=COMPONENTS,
            reflection_k=2,
            minibatch_n=4,
            gate=GateConfig(),
            seed=seed,
        ),
        store=CandidateStore(tmp_path / f"cands{tag}"),
        seed_candidate=seed_candidate(),
        pareto_scenarios=PARETO_SCENARIOS,
        feedback_pool=FEEDBACK_POOL,
        run_scenario=runner if runner is not None else FakeRunner(),
        decode_traces=decoder if decoder is not None else FakeDecoder(),
        model_call=model if model is not None else FakeModel(),
        compile_check=compile_check,
        run_dir=tmp_path / f"run{tag}",
    )


def read_state(run_dir):
    lines = (run_dir / "state.jsonl").read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


@pytest.mark.parametrize("arm_name", list(ARMS))
def test_every_arm_dry_runs_and_improves(tmp_path, arm_name):
    decoder = FakeDecoder()
    model = FakeModel()
    loop = build_loop(tmp_path, arm_name, model=model, decoder=decoder)
    summary = loop.run()

    assert summary["iterations_completed"] == 3
    assert summary["stopped"] is None
    assert summary["pool_size"] == 4  # seed + 3 accepted children
    assert summary["model_calls_used"] == 3
    # Every child strictly improves, so the best is not the seed.
    assert summary["best_candidate"] != loop.seed_id
    seed_avg = sum(loop.scores[loop.seed_id]) / len(PARETO_SCENARIOS)
    assert summary["best_macro_average"] > seed_avg

    # Trajectories decoded only for arms that see them (B/C/D).
    assert (decoder.calls > 0) == ARMS[arm_name].use_trajectories

    records = read_state(loop.run_dir)
    iters = [r for r in records if r["event"] == "iteration"]
    assert [r["iteration"] for r in iters] == [0, 1, 2]
    assert all(r["result"] == "accepted" for r in iters)
    assert all(r["arm"] == arm_name for r in iters)
    # Component round-robin order.
    assert [r["component"] for r in iters] == ["economy", "combat", "economy"]
    # Merge attempted only in arm D, on accepted iterations.
    if ARMS[arm_name].use_merge:
        assert all("merge" in r for r in iters)
    else:
        assert all("merge" not in r for r in iters)
    assert records[-1]["event"] == "summary"
    assert (loop.run_dir / "run_meta.json").exists()


def test_compile_repair_path_counts_against_budget(tmp_path):
    model = FakeModel(buggy_first=True)
    loop = build_loop(tmp_path, "score_greedy", model=model, iterations=2)
    summary = loop.run()
    assert model.repair_calls == 1
    # 2 proposals + 1 repair.
    assert summary["model_calls_used"] == 3
    records = read_state(loop.run_dir)
    first = [r for r in records if r.get("iteration") == 0][0]
    assert first["compiled_first_try"] is False
    assert first["result"] == "accepted"


def test_no_change_action_consumes_call_but_adds_nothing(tmp_path):
    model = NoChangeModel()
    loop = build_loop(tmp_path, "replay_greedy", model=model, iterations=2)
    summary = loop.run()
    assert summary["pool_size"] == 1  # seed only
    assert summary["model_calls_used"] == 2
    assert all(
        r["result"] == "no_change"
        for r in read_state(loop.run_dir)
        if r["event"] == "iteration"
    )


def test_model_call_budget_hard_stop(tmp_path):
    loop = build_loop(tmp_path, "score_greedy", iterations=5, model_calls=2)
    summary = loop.run()
    assert summary["stopped"] == "model_calls"
    assert summary["model_calls_used"] == 2
    assert summary["iterations_completed"] == 2
    records = read_state(loop.run_dir)
    assert any(r["event"] == "budget_exhausted" and r["which"] == "model_calls"
               for r in records)


def test_match_budget_hard_stop(tmp_path):
    # Seed Pareto eval alone costs 8 matches; budget 9 dies in reflection.
    loop = build_loop(tmp_path, "score_greedy", iterations=5, matches=9)
    summary = loop.run()
    assert summary["stopped"] == "matches"
    assert summary["matches_run"] == 9
    assert summary["iterations_completed"] == 0


def test_cache_hits_exempt_from_match_budget(tmp_path):
    class AllCachedRunner(FakeRunner):
        def __call__(self, candidate, scenario):
            rec = super().__call__(candidate, scenario)
            rec["cache_hit"] = True
            return rec

    # Zero match budget, yet the loop completes: every play is a cache hit.
    loop = build_loop(tmp_path, "score_greedy", runner=AllCachedRunner(),
                      iterations=2, matches=0)
    summary = loop.run()
    assert summary["stopped"] is None
    assert summary["iterations_completed"] == 2
    assert summary["matches_run"] == 0
    assert summary["cache_hits"] > 0


def test_paired_design_schedules_shared_across_arms(tmp_path):
    # Same optimizer seed, different arms: iteration-0 reflection scenarios
    # and gate minibatch derive only from (seed, iteration), so they match.
    loop_a = build_loop(tmp_path, "score_greedy", iterations=1, seed=13, tag="A")
    loop_b = build_loop(tmp_path, "gepa_pareto", iterations=1, seed=13, tag="B")
    loop_a.run()
    loop_b.run()
    rec_a = [r for r in read_state(loop_a.run_dir) if r["event"] == "iteration"][0]
    rec_b = [r for r in read_state(loop_b.run_dir) if r["event"] == "iteration"][0]
    assert rec_a["reflection_instances"] == rec_b["reflection_instances"]
    assert rec_a["reflection_scenarios"] == rec_b["reflection_scenarios"]

    # A different optimizer seed produces a different schedule.
    loop_c = build_loop(tmp_path, "score_greedy", iterations=1, seed=14, tag="C")
    loop_c.run()
    rec_c = [r for r in read_state(loop_c.run_dir) if r["event"] == "iteration"][0]
    assert rec_c["reflection_scenarios"] != rec_a["reflection_scenarios"]


def test_children_are_persisted_with_lineage(tmp_path):
    loop = build_loop(tmp_path, "replay_greedy", iterations=2)
    loop.run()
    store = loop.store
    ids = store.all_ids()
    assert len(ids) == 3  # seed + 2 children
    children = [store.get(cid) for cid in ids if cid != loop.seed_id]
    by_gen = sorted(children, key=lambda c: c.generation)
    assert by_gen[0].parents == (loop.seed_id,)
    assert by_gen[1].parents == (by_gen[0].candidate_id,)
    assert by_gen[0].changed_component == "economy"
    assert by_gen[1].changed_component == "combat"


def test_merge_arm_merges_complementary_lineages(tmp_path):
    """Force a frontier with two complementary lineages, then let a
    gepa_full iteration's merge step combine them."""
    from optimizer.loop import ArmConfig

    loop = build_loop(tmp_path, "gepa_full", iterations=0)
    store = loop.store
    root = store.get(loop.seed_id)
    a = store.add(make_candidate(
        {**dict(root.components), "economy": "economy level=5"},
        parents=(loop.seed_id,), generation=1, changed_component="economy"))
    b = store.add(make_candidate(
        {**dict(root.components), "combat": "combat level=5"},
        parents=(loop.seed_id,), generation=1, changed_component="combat"))
    # Complementary specialists: each leads half the instances.
    loop.scores[a.candidate_id] = tuple(
        1.0 if i < 4 else 0.0 for i in range(len(PARETO_SCENARIOS)))
    loop.scores[b.candidate_id] = tuple(
        0.0 if i < 4 else 1.0 for i in range(len(PARETO_SCENARIOS)))

    result = loop._attempt_merge_step()
    assert result["merged"] is not None
    merged = store.get(result["merged"])
    assert merged.components["economy"] == "economy level=5"
    assert merged.components["combat"] == "combat level=5"
    assert set(merged.parents) == {a.candidate_id, b.candidate_id}
    # The merged child was evaluated on the Pareto instances.
    assert merged.candidate_id in loop.scores
    assert len(loop.scores[merged.candidate_id]) == len(PARETO_SCENARIOS)
