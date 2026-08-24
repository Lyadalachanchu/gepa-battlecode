"""Tests for experiment.scenarios against the fixture replay + a fake runner."""

import shutil
from pathlib import Path

import pytest

from evaluation.scoring import game_outcome, game_score
from experiment.candidates import CompiledCandidate
from experiment.scenarios import ScenarioError, ScenarioExecutor, load_opponents
from harness.cache import MatchCache
from harness.runner import EngineConfig, MatchRunResult
from opponents.lockfile import OpponentEntry
from optimizer.feedback_sampler import Scenario
from replay import decode_footer

FIXTURE_REPLAY = Path(__file__).parent / "fixtures" / "smoke.bc26"
ENGINE = EngineConfig.from_lock()


class FakeRunner:
    """Copies the fixture replay instead of running the engine."""

    def __init__(self):
        self.calls = []

    def run_match(self, team_a, team_b, map_name, replay_out, **kwargs):
        self.calls.append(
            {"team_a": team_a, "team_b": team_b, "map_name": map_name, **kwargs}
        )
        shutil.copyfile(FIXTURE_REPLAY, replay_out)
        return MatchRunResult(
            replay_path=str(replay_out), returncode=0, duration_s=0.1, stdout_tail=""
        )


class FakeCompiler:
    def __init__(self, compiled):
        self.compiled = compiled

    def compiled_for(self, candidate):
        return self.compiled


def _builtin_opponent(oid="examplefuncsplayer", split="dev", **overrides):
    base = dict(
        id=oid,
        repo="engine",
        commit="abc",
        package=oid,
        lineage="official",
        split=split,
        license="x",
        source_dir=None,
        classes_dir=None,
        compiled=True,
        smoke_ok=True,
    )
    base.update(overrides)
    return OpponentEntry(**base)


@pytest.fixture
def executor(tmp_path):
    compiled = CompiledCandidate(
        candidate_id="deadbeef" * 8,
        package="candidate",
        source_dir=str(tmp_path / "src"),
        classes_dir=str(tmp_path / "classes"),
    )
    runner = FakeRunner()
    ex = ScenarioExecutor(
        cache=MatchCache(tmp_path / "cache"),
        runner=runner,
        opponents={"examplefuncsplayer": _builtin_opponent()},
        compiler=FakeCompiler(compiled),
        engine=ENGINE,
        replay_dir=tmp_path / "replays",
    )
    return ex, runner, compiled


def test_record_shape_matches_loop_contract(executor):
    ex, runner, compiled = executor
    scenario = Scenario("examplefuncsplayer", "DefaultSmall", "A")
    record = ex.run_scenario(compiled, scenario)

    # Exact fields the OptimizerLoop consumes.
    assert isinstance(record["score"], float)
    assert isinstance(record["new_exceptions"], bool)
    assert record["cache_hit"] is False
    # Passthrough fields for decode_traces + analysis.
    assert Path(record["replay_path"]).exists()
    assert record["opponent"] == "examplefuncsplayer"
    assert record["map_name"] == "DefaultSmall"
    assert record["side"] == "A"

    footer = decode_footer(record["replay_path"])
    assert record["win_type"] == footer["win_type"]
    assert record["total_rounds"] == footer["total_rounds"]
    expected_outcome = game_outcome(footer["win_type"], footer["winner"], "A")
    assert record["outcome"] == expected_outcome
    assert -1.0 <= record["margin"] <= 1.0
    assert record["score"] == game_score(record["outcome"], record["margin"])

    # Candidate seated as team A, opponent as B.
    call = runner.calls[0]
    assert call["team_a"] == "candidate"
    assert call["team_b"] == "examplefuncsplayer"
    assert call["class_location_a"] == compiled.classes_dir
    assert call["class_location_b"] is None  # engine builtin


def test_cache_hit_on_second_run(executor):
    ex, runner, compiled = executor
    scenario = Scenario("examplefuncsplayer", "DefaultSmall", "A")
    first = ex.run_scenario(compiled, scenario)
    second = ex.run_scenario(compiled, scenario)
    assert len(runner.calls) == 1  # no second engine run
    assert second["cache_hit"] is True
    assert second["score"] == first["score"]
    assert second["outcome"] == first["outcome"]
    assert Path(second["replay_path"]).exists()


def test_side_b_swaps_seating_and_changes_key(executor):
    ex, runner, compiled = executor
    a = ex.run_scenario(compiled, Scenario("examplefuncsplayer", "DefaultSmall", "A"))
    b = ex.run_scenario(compiled, Scenario("examplefuncsplayer", "DefaultSmall", "B"))
    assert len(runner.calls) == 2  # different cache cells
    call = runner.calls[1]
    assert call["team_a"] == "examplefuncsplayer"
    assert call["team_b"] == "candidate"
    assert call["class_location_b"] == compiled.classes_dir
    # Same replay fixture, opposite perspective: outcomes mirror.
    assert a["outcome"] + b["outcome"] == pytest.approx(1.0)
    assert a["margin"] == pytest.approx(-b["margin"])


def test_unusable_opponent_fails_clearly(executor, tmp_path):
    ex, _, compiled = executor
    ex.opponents["broken"] = _builtin_opponent("broken", compiled=False, smoke_ok=False)
    with pytest.raises(ScenarioError, match="not usable"):
        ex.run_scenario(compiled, Scenario("broken", "DefaultSmall", "A"))
    with pytest.raises(ScenarioError, match="unknown opponent"):
        ex.run_scenario(compiled, Scenario("nope", "DefaultSmall", "A"))
    with pytest.raises(ScenarioError, match="side"):
        ex.run_scenario(compiled, Scenario("examplefuncsplayer", "DefaultSmall", "X"))


def test_load_opponents_missing_lockfile(tmp_path):
    with pytest.raises(ScenarioError, match="opponents lockfile missing"):
        load_opponents(tmp_path / "nope.json")


def test_load_opponents_real_lockfile():
    opponents = load_opponents()
    assert "examplefuncsplayer" in opponents
    assert all(e.split in ("dev", "test") for e in opponents.values())
