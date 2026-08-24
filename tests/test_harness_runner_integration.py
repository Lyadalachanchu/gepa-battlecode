"""Integration test: run ONE real match through the pinned engine.

Marked slow (a warm-cache match takes ~5-45s of gradle + JVM time). Run with:

    python -m pytest tests/test_harness_runner_integration.py -q

Deselect with ``-m "not slow"``.
"""

import gzip
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness.runner import EngineConfig, run_match

pytestmark = pytest.mark.slow

_LOCK = REPO_ROOT / "configs" / "engine.lock.json"


@pytest.mark.skipif(not _LOCK.exists(), reason="engine.lock.json missing")
def test_run_match_produces_valid_gamewrapper(tmp_path):
    engine = EngineConfig.from_lock(_LOCK)
    if not Path(engine.engine_path).is_dir():
        pytest.skip(f"pinned engine not present at {engine.engine_path}")

    replay_out = tmp_path / "integration.bc26"
    result = run_match(
        team_a="examplefuncsplayer",
        team_b="examplefuncsplayer",
        map_name="DefaultSmall",
        replay_out=replay_out,
        engine=engine,
        timeout_s=1200,
    )

    # The replay file exists, is where we asked, and is non-trivial.
    assert result.replay_path == str(replay_out)
    assert result.returncode == 0
    assert result.duration_s > 0
    p = Path(result.replay_path)
    assert p.exists()
    assert p.stat().st_size > 0

    # It gunzips (a .bc26 is a gzip-compressed GameWrapper flatbuffer).
    raw = gzip.open(p, "rb").read()
    assert len(raw) > 0

    # Its first bytes parse as a flatbuffer GameWrapper via the engine's own
    # bundled Python schema bindings (deliberately NOT the replay package,
    # which is owned by another workstream).
    with open(_LOCK, "r", encoding="utf-8") as f:
        lock = json.load(f)
    schema_dir = str(Path(engine.engine_path) / lock["schema_python_path"])
    if schema_dir not in sys.path:
        sys.path.insert(0, schema_dir)
    from battlecode.schema.GameWrapper import GameWrapper

    gw = GameWrapper.GetRootAs(raw, 0)
    assert gw.EventsLength() > 0
    # A complete single-match game has exactly one match header/footer pair.
    assert gw.MatchHeadersLength() == 1
    assert gw.MatchFootersLength() == 1
    # Header/footer indices point inside the event stream.
    assert 0 <= gw.MatchHeaders(0) < gw.EventsLength()
    assert 0 <= gw.MatchFooters(0) < gw.EventsLength()
