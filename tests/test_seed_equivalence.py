"""Tests for the modular seed refactor (PLAN.md section 9).

* Pure tests: bots/modular_seed has exactly the expected files with the right
  package declarations (glue + the five experiment.yaml components).
* A slow test that both seed source trees actually compile against the pinned
  engine classes (skipped when javac or the engine build output is missing).

The full behavioral equivalence check (identical decoded action streams +
bytecode headroom) is scripts/check_equivalence.py -- it runs real engine
matches and is not a pytest concern.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ORIGINAL_DIR = REPO_ROOT / "bots" / "original_seed" / "lectureplayer"
MODULAR_DIR = REPO_ROOT / "bots" / "modular_seed"

EXPECTED_MODULAR_FILES = {
    "RobotPlayer.java",  # glue: entry point + shared state + dispatch
    "Economy.java",
    "Combat.java",
    "Defense.java",
    "Navigation.java",
    "Strategy.java",
}


def _engine_classes_dir() -> Path | None:
    lock = REPO_ROOT / "configs" / "engine.lock.json"
    if not lock.exists():
        return None
    engine_path = Path(json.loads(lock.read_text())["local_path"])
    classes = engine_path / "engine" / "build" / "classes"
    return classes if (classes / "battlecode" / "common").is_dir() else None


def test_modular_seed_has_exactly_expected_files():
    java_files = {p.name for p in MODULAR_DIR.glob("*.java")}
    assert java_files == EXPECTED_MODULAR_FILES
    # nothing but .java sources in the bot package directory
    extras = [p.name for p in MODULAR_DIR.iterdir() if p.suffix != ".java"]
    assert extras == []


def test_modular_seed_package_declarations():
    for name in EXPECTED_MODULAR_FILES:
        src = (MODULAR_DIR / name).read_text(encoding="utf-8")
        first_code_line = next(
            line for line in src.splitlines() if line.strip() and not line.strip().startswith("//")
        )
        assert first_code_line.strip() == "package modular_seed;", name


def test_component_files_match_experiment_yaml():
    cfg = yaml.safe_load((REPO_ROOT / "configs" / "experiment.yaml").read_text())
    components = cfg["components"]
    assert components == ["economy", "combat", "defense", "navigation", "strategy"]
    for comp in components:
        assert (MODULAR_DIR / f"{comp.capitalize()}.java").exists(), comp


def test_original_seed_untouched_package():
    src = (ORIGINAL_DIR / "RobotPlayer.java").read_text(encoding="utf-8")
    assert re.match(r"\s*package lectureplayer;", src)


@pytest.mark.slow
def test_both_seed_trees_compile(tmp_path):
    if shutil.which("javac") is None:
        pytest.skip("javac not available")
    engine_classes = _engine_classes_dir()
    if engine_classes is None:
        pytest.skip("pinned engine classes not built (battlecode/common missing)")

    for label, sources in (
        ("original", sorted(ORIGINAL_DIR.glob("*.java"))),
        ("modular", sorted(MODULAR_DIR.glob("*.java"))),
    ):
        out = tmp_path / f"{label}_classes"
        out.mkdir()
        proc = subprocess.run(
            ["javac", "-cp", str(engine_classes), "-d", str(out)]
            + [str(p) for p in sources],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, f"{label} failed to compile:\n{proc.stderr}"
        assert list(out.rglob("*.class")), label
