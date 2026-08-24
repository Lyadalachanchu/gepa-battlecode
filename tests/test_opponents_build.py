"""Tests for the opponent pipeline: lockfile schema round-trip and
compile_bot against the pinned engine classpath (javac tests are slow)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opponents.build import compile_bot, ensure_engine_classes
from opponents.lockfile import OpponentEntry, load_lockfile, save_lockfile

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sample_entries() -> list[OpponentEntry]:
    return [
        OpponentEntry(
            id="examplefuncsplayer",
            repo="https://github.com/battlecode/battlecode26",
            commit="103abf6b67a2cf544e6344dddef9318af9ae9193",
            package="examplefuncsplayer",
            lineage="official",
            split="dev",
            license="AGPL-3.0 (engine COPYING)",
            source_dir=None,
            classes_dir=None,
            compiled=True,
            javac_error=None,
            smoke_ok=True,
            strength_tier="floor",
        ),
        OpponentEntry(
            id="somebot",
            repo="https://github.com/example/bot",
            commit="deadbeef",
            package="somebot",
            lineage="example",
            split="test",
            license="MIT",
            source_dir="/tmp/x/src",
            classes_dir=None,
            compiled=False,
            javac_error="Foo.java:3: error: cannot find symbol",
            smoke_ok=False,
            strength_tier=None,
        ),
    ]


class TestLockfile:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "opponents.lock.json"
        entries = _sample_entries()
        save_lockfile(path, entries)
        assert load_lockfile(path) == entries

    def test_schema_shape(self, tmp_path):
        path = tmp_path / "opponents.lock.json"
        save_lockfile(path, _sample_entries())
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert set(raw.keys()) == {"opponents"}
        required = {
            "id", "repo", "commit", "package", "lineage", "split", "license",
            "source_dir", "classes_dir", "compiled", "javac_error",
            "smoke_ok", "strength_tier",
        }
        for entry in raw["opponents"]:
            assert required <= set(entry.keys())

    def test_rejects_bad_split(self):
        with pytest.raises(ValueError, match="split"):
            OpponentEntry(
                id="x", repo="r", commit="c", package="p", lineage="l",
                split="validation", license="MIT",
                source_dir=None, classes_dir=None,
            )

    def test_rejects_bad_tier(self):
        with pytest.raises(ValueError, match="strength_tier"):
            OpponentEntry(
                id="x", repo="r", commit="c", package="p", lineage="l",
                split="dev", license="MIT",
                source_dir=None, classes_dir=None,
                strength_tier="godlike",
            )


VALID_BOT = """\
package tinybot;

import battlecode.common.RobotController;
import battlecode.common.GameActionException;

public class RobotPlayer {
    public static void run(RobotController rc) throws GameActionException {
        while (true) {
            rc.getRoundNum();
        }
    }
}
"""

INVALID_BOT = """\
package tinybot;

public class RobotPlayer {
    public static void run( {  // syntax error
}
"""


@pytest.fixture(scope="module")
def engine_cp() -> Path:
    lock = json.loads(
        (REPO_ROOT / "configs" / "engine.lock.json").read_text(encoding="utf-8")
    )
    engine_path = Path(lock["local_path"])
    if not engine_path.exists():
        pytest.skip("pinned engine checkout not present")
    return ensure_engine_classes(engine_path)


@pytest.mark.slow
class TestCompileBot:
    def _write_pkg(self, tmp_path: Path, source: str) -> Path:
        src = tmp_path / "src"
        pkg = src / "tinybot"
        pkg.mkdir(parents=True)
        (pkg / "RobotPlayer.java").write_text(source, encoding="utf-8")
        return src

    def test_valid_bot_compiles(self, tmp_path, engine_cp):
        src = self._write_pkg(tmp_path, VALID_BOT)
        # A dev scrap next to the sources must be ignored, not compiled/copied.
        (src / "tinybot" / "RobotPlayer.java.jinja2").write_text("{{ x }}")
        out = tmp_path / "classes"
        result = compile_bot(src, "tinybot", out, engine_cp)
        assert result.ok, result.javac_output
        assert result.num_sources == 1
        assert (out / "tinybot" / "RobotPlayer.class").is_file()
        assert result.out_classes_dir == str(out)

    def test_invalid_bot_reports_error_and_cleans_up(self, tmp_path, engine_cp):
        src = self._write_pkg(tmp_path, INVALID_BOT)
        out = tmp_path / "classes"
        result = compile_bot(src, "tinybot", out, engine_cp)
        assert not result.ok
        assert "error" in result.javac_output
        assert result.out_classes_dir == ""
        assert not out.exists()  # no half-built tree left behind

    def test_engine_api_actually_on_classpath(self, tmp_path, engine_cp):
        # Same bot minus the engine import fails; with it (above) it passes --
        # so the classpath really is the engine, not a no-op.
        bad = VALID_BOT.replace("import battlecode.common.RobotController;\n", "")
        src = self._write_pkg(tmp_path, bad)
        result = compile_bot(src, "tinybot", tmp_path / "classes", engine_cp)
        assert not result.ok
        assert "RobotController" in result.javac_output

    def test_missing_package_raises(self, tmp_path, engine_cp):
        (tmp_path / "src").mkdir()
        with pytest.raises(ValueError, match="package dir not found"):
            compile_bot(tmp_path / "src", "nosuchbot", tmp_path / "classes", engine_cp)
