"""Match runner: invoke the pinned Battlecode 2026 engine headlessly.

Enforces the PLAN.md section 5 harness rules:

* Exactly one map per JVM invocation (cat-AI static ``Random(1092)`` is seeded
  once per JVM, so multi-map runs are only reproducible as a whole sequence).
* Always passes ``-PvalidateMaps=false -PalternateOrder=false``.
* Indicator strings disabled (``-PshowIndicators=false``).
* Winners are NEVER parsed from stdout -- the replay footer is the authority
  (the ``replay`` package owns decoding; this module deliberately does not
  import it).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOCK = _REPO_ROOT / "configs" / "engine.lock.json"

# The engine's build.gradle mangles the replay property:
#   saveFileName = replay[:-5];  saveFileName = saveFileName[:100] + '.bc26'
# so a path longer than this is silently truncated. We route through a short
# temporary path whenever the requested path would be mangled.
_GRADLE_REPLAY_MAX_LEN = 105  # 100 chars + '.bc26'

_STDOUT_TAIL_CHARS = 4000


class MatchRunError(RuntimeError):
    """A match invocation failed (nonzero exit, timeout, or missing replay)."""

    def __init__(self, message: str, stdout_tail: str = "", returncode: Optional[int] = None):
        super().__init__(message if not stdout_tail else f"{message}\n--- stdout tail ---\n{stdout_tail}")
        self.stdout_tail = stdout_tail
        self.returncode = returncode


@dataclass(frozen=True)
class EngineConfig:
    """Pinned engine description, normally loaded from configs/engine.lock.json."""

    engine_path: str
    commit: str = ""
    headless_task: str = "headless"
    required_gradle_props: tuple = ("teamA", "teamB", "maps", "validateMaps", "alternateOrder")
    show_indicators: bool = False
    maps_dir: str = "maps"
    schema_python_path: str = "schema/python"

    @classmethod
    def from_lock(cls, lock_path: Union[str, Path] = _DEFAULT_LOCK) -> "EngineConfig":
        with open(lock_path, "r", encoding="utf-8") as f:
            lock = json.load(f)
        return cls(
            engine_path=lock["local_path"],
            commit=lock.get("commit", ""),
            headless_task=lock.get("headless_task", "headless"),
            required_gradle_props=tuple(lock.get("required_gradle_props", ())),
            show_indicators=bool(lock.get("show_indicators", False)),
            maps_dir=lock.get("maps_dir", "maps"),
            schema_python_path=lock.get("schema_python_path", "schema/python"),
        )


@dataclass(frozen=True)
class MatchRunResult:
    replay_path: str
    returncode: int
    duration_s: float
    stdout_tail: str


def _validate_single_map(map_name: str) -> None:
    if not map_name or any(sep in map_name for sep in (",", ";", "+", " ", "\t", "\n")):
        raise ValueError(
            f"run_match requires EXACTLY ONE map per invocation (cat-AI static RNG rule); "
            f"got map_name={map_name!r}"
        )


def run_match(
    team_a: str,
    team_b: str,
    map_name: str,
    replay_out: Union[str, Path],
    engine: Optional[EngineConfig] = None,
    class_location_a: Optional[str] = None,
    class_location_b: Optional[str] = None,
    timeout_s: int = 2700,
) -> MatchRunResult:
    """Run one match (one map, one JVM) via ``./gradlew headless``.

    Returns a :class:`MatchRunResult` on success; raises :class:`MatchRunError`
    with the stdout tail on nonzero exit, timeout, or a missing/empty replay.
    Winners are not parsed here -- decode the replay footer for that.
    """
    _validate_single_map(map_name)

    if engine is None:
        engine = EngineConfig.from_lock()

    engine_dir = Path(engine.engine_path)
    gradlew = engine_dir / "gradlew"
    if not gradlew.exists():
        raise MatchRunError(f"gradlew not found at {gradlew}")

    replay_out = Path(replay_out).absolute()
    if replay_out.suffix != ".bc26":
        raise ValueError(f"replay_out must end with .bc26, got {replay_out}")
    replay_out.parent.mkdir(parents=True, exist_ok=True)

    # Work around the build.gradle filename mangling (see _GRADLE_REPLAY_MAX_LEN).
    tmp_holder: Optional[str] = None
    if len(str(replay_out)) <= _GRADLE_REPLAY_MAX_LEN:
        gradle_replay = replay_out
    else:
        tmp_holder = tempfile.mkdtemp(prefix="bc26_")
        gradle_replay = Path(tmp_holder) / "m.bc26"

    cmd = [
        str(gradlew),
        engine.headless_task,
        f"-PteamA={team_a}",
        f"-PteamB={team_b}",
        f"-Pmaps={map_name}",
        "-PvalidateMaps=false",
        "-PalternateOrder=false",
        "-PshowIndicators=false",
        f"-Preplay={gradle_replay}",
    ]
    if class_location_a is not None:
        cmd.append(f"-PclassLocationA={Path(class_location_a).absolute()}")
    if class_location_b is not None:
        cmd.append(f"-PclassLocationB={Path(class_location_b).absolute()}")

    env = dict(os.environ)  # preserves JAVA_TOOL_OPTIONS and proxy settings

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(engine_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        out = exc.stdout or b""
        tail = out.decode("utf-8", errors="replace")[-_STDOUT_TAIL_CHARS:]
        if tmp_holder:
            shutil.rmtree(tmp_holder, ignore_errors=True)
        raise MatchRunError(
            f"match timed out after {duration:.1f}s (timeout_s={timeout_s}): "
            f"{team_a} vs {team_b} on {map_name}",
            stdout_tail=tail,
        ) from exc

    duration = time.monotonic() - start
    stdout_text = (proc.stdout or b"").decode("utf-8", errors="replace")
    tail = stdout_text[-_STDOUT_TAIL_CHARS:]

    try:
        if proc.returncode != 0:
            raise MatchRunError(
                f"gradlew {engine.headless_task} exited with code {proc.returncode}: "
                f"{team_a} vs {team_b} on {map_name}",
                stdout_tail=tail,
                returncode=proc.returncode,
            )
        if not gradle_replay.exists() or gradle_replay.stat().st_size == 0:
            raise MatchRunError(
                f"engine exited 0 but replay file is missing or empty: {gradle_replay}",
                stdout_tail=tail,
                returncode=proc.returncode,
            )
        if gradle_replay != replay_out:
            shutil.move(str(gradle_replay), str(replay_out))
    finally:
        if tmp_holder:
            shutil.rmtree(tmp_holder, ignore_errors=True)

    return MatchRunResult(
        replay_path=str(replay_out),
        returncode=proc.returncode,
        duration_s=duration,
        stdout_tail=tail,
    )
