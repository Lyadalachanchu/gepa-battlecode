"""Tests for experiment.candidates: materialization + real javac compiles."""

from pathlib import Path

import pytest

from experiment.candidates import (
    CandidateCompileError,
    CandidateCompiler,
    component_class_name,
    materialize_candidate,
    rewrite_package_decl,
)
from optimizer.candidate import make_candidate

# A minimal, REAL bot: compiles against the engine and just yields forever.
TRIVIAL_ROBOTPLAYER = """\
package modular_seed;

import battlecode.common.*;

public class RobotPlayer {
    public static void run(RobotController rc) throws GameActionException {
        while (true) {
            Clock.yield();
        }
    }
}
"""

TRIVIAL_HELPER = """\
package modular_seed;

public class Economy {
    public static int cheeseTarget() {
        return 42;
    }
}
"""


def test_rewrite_package_decl_replaces_existing():
    out = rewrite_package_decl(TRIVIAL_ROBOTPLAYER, "candidate")
    assert out.count("package candidate;") == 1
    assert "package modular_seed;" not in out


def test_rewrite_package_decl_inserts_when_missing():
    src = "public class Foo {}\n"
    out = rewrite_package_decl(src, "candidate")
    assert out.startswith("package candidate;")
    assert "class Foo" in out


def test_component_class_name():
    assert component_class_name("robotplayer", TRIVIAL_ROBOTPLAYER) == "RobotPlayer"
    assert component_class_name("economy", TRIVIAL_HELPER) == "Economy"
    with pytest.raises(CandidateCompileError):
        component_class_name("bad", "// no type declared\n")


def test_materialize_and_compile_trivial_bot(tmp_path):
    cand = make_candidate(
        {"robotplayer": TRIVIAL_ROBOTPLAYER, "economy": TRIVIAL_HELPER}
    )
    compiled = materialize_candidate(cand, tmp_path)
    assert compiled.candidate_id == cand.candidate_id
    assert compiled.package == "candidate"
    src = Path(compiled.source_dir) / "candidate"
    assert (src / "RobotPlayer.java").exists()
    assert (src / "Economy.java").exists()
    assert "package candidate;" in (src / "RobotPlayer.java").read_text()
    classes = Path(compiled.classes_dir) / "candidate"
    assert (classes / "RobotPlayer.class").exists()
    assert (classes / "Economy.class").exists()


def test_materialize_compile_failure_raises(tmp_path):
    broken = TRIVIAL_ROBOTPLAYER.replace("Clock.yield();", "thisDoesNotCompile(;")
    with pytest.raises(CandidateCompileError) as exc:
        materialize_candidate({"robotplayer": broken}, tmp_path)
    assert exc.value.javac_output  # javac output surfaced for the repair prompt


def test_compiler_caches_and_matches_loop_contract(tmp_path):
    compiler = CandidateCompiler(tmp_path)
    components = {"robotplayer": TRIVIAL_ROBOTPLAYER, "economy": TRIVIAL_HELPER}

    ok, errors = compiler.compile_check(components)
    assert ok is True
    assert errors == ""

    # Cached: identical content compiles for free (memory + on-disk marker).
    ok2, _, compiled = compiler.compile_components(components)
    assert ok2 and compiled is not None

    fresh = CandidateCompiler(tmp_path)  # new process simulation: disk reuse
    ok3, _, compiled3 = fresh.compile_components(components)
    assert ok3 and compiled3.classes_dir == compiled.classes_dir

    broken = dict(components, economy=TRIVIAL_HELPER.replace("return 42;", "return;"))
    ok4, errors4 = compiler.compile_check(broken)
    assert ok4 is False
    assert "error" in errors4.lower()

    cand = make_candidate(components)
    assert compiler.compiled_for(cand).candidate_id == cand.candidate_id
    with pytest.raises(CandidateCompileError):
        compiler.compiled_for(make_candidate(broken))


def test_duplicate_type_names_rejected(tmp_path):
    with pytest.raises(CandidateCompileError):
        materialize_candidate(
            {"a": "public class Same {}\n", "b": "public class Same {}\n"}, tmp_path
        )
