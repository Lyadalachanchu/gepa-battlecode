"""Tests for experiment.direct_runner: classpath resolution + direct exec."""

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from experiment.direct_runner import (
    ClasspathResolutionError,
    DirectRunner,
    HeadlessRuntime,
    _extract_marked,
    resolve_headless_runtime,
)
from harness.runner import EngineConfig, MatchRunError

ENGINE = EngineConfig.from_lock()


def _fake_gradle_output(classpath: str, classloc: str) -> bytes:
    return (
        "Picked up JAVA_TOOL_OPTIONS: -Dwhatever\n"
        "GEPA_DEFAULT_CLASSLOC_BEGIN\n"
        f"{classloc}\n"
        "GEPA_DEFAULT_CLASSLOC_END\n"
        "GEPA_CLASSPATH_BEGIN\n"
        f"{classpath}\n"
        "GEPA_CLASSPATH_END\n"
        ":headless SKIPPED\n"
    ).encode()


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_extract_marked():
    out = _fake_gradle_output("/a.jar:/b", "/b").decode()
    assert _extract_marked(out, "GEPA_CLASSPATH_BEGIN", "GEPA_CLASSPATH_END") == "/a.jar:/b"
    with pytest.raises(ClasspathResolutionError):
        _extract_marked("no markers here", "GEPA_CLASSPATH_BEGIN", "GEPA_CLASSPATH_END")


def test_resolve_with_mocked_subprocess(tmp_path):
    jar = tmp_path / "engine.jar"
    jar.write_bytes(b"jar")
    classes = tmp_path / "classes"
    classes.mkdir()
    cp = f"{jar}:{classes}"

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeProc(_fake_gradle_output(cp, str(classes)))

    cache = tmp_path / "engine_classpath.txt"
    rt = resolve_headless_runtime(engine=ENGINE, cache_path=cache, _run=fake_run)
    assert rt.classpath == cp
    assert rt.default_class_location == str(classes)
    assert rt.engine_commit == ENGINE.commit
    assert len(calls) == 1
    # The probe must be a dry-run with the external init script.
    assert "--dry-run" in calls[0]
    assert any("--init-script" == c for c in calls[0])

    # Cached: second resolution never invokes gradle.
    rt2 = resolve_headless_runtime(engine=ENGINE, cache_path=cache, _run=fake_run)
    assert rt2 == rt
    assert len(calls) == 1

    # Stale cache (wrong engine commit) forces re-resolution.
    stale = json.loads(cache.read_text())
    stale["engine_commit"] = "not-the-pinned-commit"
    cache.write_text(json.dumps(stale))
    rt3 = resolve_headless_runtime(engine=ENGINE, cache_path=cache, _run=fake_run)
    assert len(calls) == 2
    assert rt3.engine_commit == ENGINE.commit

    # Corrupt cache also forces re-resolution rather than crashing.
    cache.write_text("{not json")
    rt4 = resolve_headless_runtime(engine=ENGINE, cache_path=cache, _run=fake_run)
    assert len(calls) == 3
    assert rt4 == rt


def test_resolve_rejects_missing_entries(tmp_path):
    def fake_run(cmd, **kwargs):
        return _FakeProc(_fake_gradle_output("/nonexistent/x.jar", "/nonexistent"))

    with pytest.raises(ClasspathResolutionError):
        resolve_headless_runtime(
            engine=ENGINE, cache_path=tmp_path / "cp.txt", _run=fake_run
        )


def test_resolve_rejects_gradle_failure(tmp_path):
    def fake_run(cmd, **kwargs):
        return _FakeProc(b"boom", returncode=1)

    with pytest.raises(ClasspathResolutionError):
        resolve_headless_runtime(
            engine=ENGINE, cache_path=tmp_path / "cp.txt", _run=fake_run
        )


def test_build_cmd_replicates_headless_props(tmp_path):
    jar = tmp_path / "engine.jar"
    jar.write_bytes(b"jar")
    rt = HeadlessRuntime(
        classpath=str(jar), default_class_location=str(tmp_path), engine_commit="c"
    )
    runner = DirectRunner(engine=ENGINE, cache_path=tmp_path / "cp.txt")
    cmd = runner._build_cmd(
        rt, "candidate", "lectureplayer", "DefaultSmall",
        Path("/tmp/r.bc26"), "/cls/cand", None,
    )
    joined = " ".join(cmd)
    assert cmd[0] == "java"
    assert cmd[-1] == "-c=-"
    assert cmd[-2] == "battlecode.server.Main"
    for prop in (
        "-Dbc.server.mode=headless",
        "-Dbc.server.map-path=maps",
        "-Dbc.engine.show-indicators=false",
        "-Dbc.server.validate-maps=false",
        "-Dbc.server.alternate-order=false",
        "-Dbc.game.team-a=candidate",
        "-Dbc.game.team-b=lectureplayer",
        "-Dbc.game.team-a.package=candidate",
        "-Dbc.game.team-b.package=lectureplayer",
        "-Dbc.game.maps=DefaultSmall",
        "-Dbc.server.save-file=/tmp/r.bc26",
        "-Dbc.game.team-a.url=/cls/cand",
        f"-Dbc.game.team-b.url={tmp_path}",  # default class location
    ):
        assert prop in joined, prop
    assert "--add-opens=java.base/jdk.internal.misc=ALL-UNNAMED" in cmd


def test_single_map_rule_enforced(tmp_path):
    runner = DirectRunner(engine=ENGINE, cache_path=tmp_path / "cp.txt")
    with pytest.raises(ValueError):
        runner.run_match("a", "b", "MapOne,MapTwo", tmp_path / "r.bc26")


def test_use_gradle_flag_routes_to_harness(tmp_path, monkeypatch):
    seen = {}

    def fake_gradle(**kwargs):
        seen.update(kwargs)
        return "GRADLE_RESULT"

    monkeypatch.setattr("experiment.direct_runner.gradle_run_match", fake_gradle)
    runner = DirectRunner(engine=ENGINE, cache_path=tmp_path / "cp.txt", use_gradle=True)
    result = runner.run_match("a", "b", "DefaultSmall", tmp_path / "r.bc26")
    assert result == "GRADLE_RESULT"
    assert seen["map_name"] == "DefaultSmall"


def test_fallback_pins_gradle_after_direct_breakage(tmp_path, monkeypatch):
    runner = DirectRunner(engine=ENGINE, cache_path=tmp_path / "cp.txt")

    def broken_direct(*args, **kwargs):
        raise MatchRunError("direct exec broke")

    gradle_calls = []

    def fake_gradle(**kwargs):
        gradle_calls.append(kwargs)
        return "GRADLE_RESULT"

    monkeypatch.setattr(runner, "_run_direct", broken_direct)
    monkeypatch.setattr("experiment.direct_runner.gradle_run_match", fake_gradle)

    assert runner.run_match("a", "b", "DefaultSmall", tmp_path / "r.bc26") == "GRADLE_RESULT"
    # Direct failed but gradle succeeded: the runner pins itself to gradle.
    assert runner.use_gradle is True
    assert runner.run_match("a", "b", "DefaultSmall", tmp_path / "r.bc26") == "GRADLE_RESULT"
    assert len(gradle_calls) == 2


@pytest.mark.slow
def test_direct_vs_gradle_equivalence(tmp_path):
    """The direct java invocation must reproduce the gradle match exactly.

    Uses a decisively-won cell (lectureplayer beats examplefuncsplayer):
    an examplefuncsplayer mirror match ends in COIN_FLIP, whose winner byte
    is the engine's ONE nondeterministic bit (PLAN.md section 5 rule 3) and
    would flap between otherwise byte-identical runs.
    """
    from harness import run_match as harness_run_match
    from replay import decode_footer

    lecture_classes = json.loads(
        (Path(__file__).parent.parent / "configs" / "opponents.lock.json").read_text()
    )
    classes_dir = next(
        o["classes_dir"] for o in lecture_classes["opponents"]
        if o["id"] == "lectureplayer"
    )

    runner = DirectRunner(engine=ENGINE)  # real classpath cache in runs/
    direct_replay = tmp_path / "d.bc26"
    gradle_replay = tmp_path / "g.bc26"

    runner.run_match(
        "lectureplayer", "examplefuncsplayer", "DefaultSmall", direct_replay,
        class_location_a=classes_dir,
    )
    assert runner.use_gradle is False, "direct path silently fell back to gradle"
    harness_run_match(
        "lectureplayer", "examplefuncsplayer", "DefaultSmall", gradle_replay,
        class_location_a=classes_dir,
    )

    d = decode_footer(direct_replay)
    g = decode_footer(gradle_replay)
    assert d["win_type"] not in ("TIE", "COIN_FLIP"), (
        "test cell must be decisively won for a byte-level comparison"
    )
    assert d["total_rounds"] == g["total_rounds"]
    assert d["win_type"] == g["win_type"]
    assert d["winner"] == g["winner"]
    assert d["final_team_stats"] == g["final_team_stats"]

    # Determinism is byte-level: identical gunzipped payloads.
    dh = hashlib.sha256(gzip.open(direct_replay, "rb").read()).hexdigest()
    gh = hashlib.sha256(gzip.open(gradle_replay, "rb").read()).hexdigest()
    assert dh == gh
