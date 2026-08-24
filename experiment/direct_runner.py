"""Gradle-free headless match execution.

``./gradlew headless`` is a thin JavaExec wrapper (engine build.gradle lines
~60-110): ``java -cp <engine.jar + example-bots classes + scala jars>
battlecode.server.Main -c=-`` plus a fixed set of ``-Dbc.*`` system
properties.  This module replicates that invocation directly so matches
skip the gradle daemon (faster, and free of gradle's project lock, which
serializes concurrent matches).

The classpath is resolved ONCE, deterministically, by running gradle with an
external ``--init-script`` (``print_headless_classpath.init.gradle``, shipped
next to this file -- the engine repo is never modified) under ``--dry-run``:
the ``taskGraph.whenReady`` hook prints the resolved ``headless`` task
classpath and the example-bots default class location between markers.  The
result is cached in ``runs/engine_classpath.txt`` and revalidated (engine
commit + files exist) on every load.

Equivalence is verified (see tests + task report): a direct match and a
``harness.run_match`` gradle match on the same cell produce byte-identical
gunzipped replays.  ``DirectRunner`` therefore shares the exact match cache
with the gradle path, and transparently falls back to ``harness.run_match``
if the direct invocation cannot be used (``use_gradle=True`` forces it).
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from harness.runner import (
    EngineConfig,
    MatchRunError,
    MatchRunResult,
    _validate_single_map,
    run_match as gradle_run_match,
)

__all__ = [
    "HeadlessRuntime",
    "resolve_headless_runtime",
    "DirectRunner",
    "DEFAULT_CACHE_PATH",
    "INIT_SCRIPT_PATH",
]

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = _REPO_ROOT / "runs" / "engine_classpath.txt"
INIT_SCRIPT_PATH = Path(__file__).resolve().parent / "print_headless_classpath.init.gradle"

_CLASSPATH_BEGIN = "GEPA_CLASSPATH_BEGIN"
_CLASSPATH_END = "GEPA_CLASSPATH_END"
_CLASSLOC_BEGIN = "GEPA_DEFAULT_CLASSLOC_BEGIN"
_CLASSLOC_END = "GEPA_DEFAULT_CLASSLOC_END"

_STDOUT_TAIL_CHARS = 4000

# Verbatim from the engine build.gradle 'headless' JavaExec block.
_JVM_ADD_OPENS = (
    "--add-opens=java.base/jdk.internal.misc=ALL-UNNAMED",
    "--add-opens=java.base/jdk.internal.math=ALL-UNNAMED",
    "--add-opens=java.base/jdk.internal.util=ALL-UNNAMED",
    "--add-opens=java.base/jdk.internal.access=ALL-UNNAMED",
    "--add-opens=java.base/sun.security.action=ALL-UNNAMED",
)


@dataclass(frozen=True)
class HeadlessRuntime:
    """Resolved, validated ingredients of a direct headless invocation."""

    classpath: str
    default_class_location: str
    engine_commit: str

    def to_json_dict(self) -> dict:
        return {
            "classpath": self.classpath,
            "default_class_location": self.default_class_location,
            "engine_commit": self.engine_commit,
        }


class ClasspathResolutionError(RuntimeError):
    """The gradle dry-run classpath probe failed or produced garbage."""


def _extract_marked(text: str, begin: str, end: str) -> str:
    """Return the stripped text between the first begin/end marker lines."""
    lines = text.splitlines()
    try:
        i = lines.index(begin)
        j = lines.index(end, i + 1)
    except ValueError:
        raise ClasspathResolutionError(
            f"markers {begin}/{end} not found in gradle output "
            f"(tail: {text[-800:]!r})"
        ) from None
    return "\n".join(lines[i + 1 : j]).strip()


def _classpath_ok(runtime: HeadlessRuntime) -> bool:
    """The load-bearing classpath entries must exist.

    The engine server jar (first entry), every ``.jar`` entry, and the
    default class location are required.  Bare directory entries (e.g. an
    example-bots ``resources/main`` that gradle lists but never created) are
    allowed to be absent -- gradle and ``java -cp`` both tolerate them.
    """
    entries = [e for e in runtime.classpath.split(":") if e]
    if not entries or not Path(entries[0]).exists():
        return False
    if not all(Path(e).exists() for e in entries if e.endswith(".jar")):
        return False
    return Path(runtime.default_class_location.split(":")[0]).exists()


def resolve_headless_runtime(
    engine: Optional[EngineConfig] = None,
    cache_path: Union[str, Path] = DEFAULT_CACHE_PATH,
    force: bool = False,
    timeout_s: int = 600,
    _run=subprocess.run,
) -> HeadlessRuntime:
    """Resolve the headless classpath once; cache in ``cache_path``.

    A cached result is reused only if its engine commit matches the lock and
    every classpath entry still exists; otherwise the gradle dry-run probe is
    rerun and the cache rewritten.
    """
    if engine is None:
        engine = EngineConfig.from_lock()
    cache_path = Path(cache_path)

    if not force and cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            runtime = HeadlessRuntime(
                classpath=str(data["classpath"]),
                default_class_location=str(data["default_class_location"]),
                engine_commit=str(data["engine_commit"]),
            )
            if runtime.engine_commit == engine.commit and _classpath_ok(runtime):
                return runtime
        except (json.JSONDecodeError, KeyError, TypeError):
            pass  # corrupt cache: fall through and re-resolve

    engine_dir = Path(engine.engine_path)
    gradlew = engine_dir / "gradlew"
    if not gradlew.exists():
        raise ClasspathResolutionError(f"gradlew not found at {gradlew}")
    if not INIT_SCRIPT_PATH.exists():
        raise ClasspathResolutionError(f"init script missing: {INIT_SCRIPT_PATH}")

    cmd = [
        str(gradlew),
        engine.headless_task,
        "--dry-run",
        "-q",
        "--init-script",
        str(INIT_SCRIPT_PATH),
        "-PteamA=examplefuncsplayer",
        "-PteamB=examplefuncsplayer",
        "-Pmaps=DefaultSmall",
        "-PvalidateMaps=false",
        "-PalternateOrder=false",
        "-PshowIndicators=false",
        "-Preplay=/tmp/gepa_classpath_probe.bc26",  # never written: --dry-run
    ]
    proc = _run(
        cmd,
        cwd=str(engine_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_s,
    )
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise ClasspathResolutionError(
            f"gradle classpath probe exited {proc.returncode}\n"
            f"--- tail ---\n{out[-2000:]}"
        )
    runtime = HeadlessRuntime(
        classpath=_extract_marked(out, _CLASSPATH_BEGIN, _CLASSPATH_END),
        default_class_location=_extract_marked(out, _CLASSLOC_BEGIN, _CLASSLOC_END),
        engine_commit=engine.commit,
    )
    if not _classpath_ok(runtime):
        raise ClasspathResolutionError(
            f"resolved classpath has missing entries: {runtime.classpath}"
        )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(runtime.to_json_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(cache_path)
    return runtime


class DirectRunner:
    """Run one-map headless matches directly via ``java -cp ...``.

    Same call surface as :func:`harness.run_match` (as a method), same
    MatchRunResult/MatchRunError semantics.  ``use_gradle=True`` routes every
    match through :func:`harness.run_match` instead; on a direct-path failure
    the runner retries the same match through gradle transparently, and
    permanently switches to gradle if gradle succeeds where direct failed
    (i.e. the direct path itself is broken, not the match).
    """

    def __init__(
        self,
        engine: Optional[EngineConfig] = None,
        cache_path: Union[str, Path] = DEFAULT_CACHE_PATH,
        use_gradle: bool = False,
    ):
        self.engine = engine if engine is not None else EngineConfig.from_lock()
        self.cache_path = Path(cache_path)
        self.use_gradle = use_gradle
        self._runtime: Optional[HeadlessRuntime] = None

    # ------------------------------------------------------------------

    def _get_runtime(self) -> HeadlessRuntime:
        if self._runtime is None:
            self._runtime = resolve_headless_runtime(
                engine=self.engine, cache_path=self.cache_path
            )
        return self._runtime

    def _build_cmd(
        self,
        runtime: HeadlessRuntime,
        team_a: str,
        team_b: str,
        map_name: str,
        replay_out: Path,
        class_location_a: Optional[str],
        class_location_b: Optional[str],
    ) -> list[str]:
        loc_a = (
            str(Path(class_location_a).absolute())
            if class_location_a is not None
            else runtime.default_class_location
        )
        loc_b = (
            str(Path(class_location_b).absolute())
            if class_location_b is not None
            else runtime.default_class_location
        )
        # Property list mirrors the engine build.gradle 'headless' task with
        # the harness-pinned flags (PLAN.md section 5): indicators off,
        # validateMaps off, alternateOrder off, one map.
        return [
            "java",
            *_JVM_ADD_OPENS,
            "-Dbc.server.wait-for-client=false",
            "-Dbc.server.mode=headless",
            "-Dbc.server.map-path=maps",
            "-Dbc.server.robot-player-to-system-out=true",
            "-Dbc.server.debug=false",
            "-Dbc.engine.debug-methods=false",
            "-Dbc.engine.enable-profiler=false",
            "-Dbc.engine.show-indicators=false",
            f"-Dbc.game.team-a={team_a}",
            f"-Dbc.game.team-b={team_b}",
            "-Dbc.game.team-a.language=java",
            "-Dbc.game.team-b.language=java",
            f"-Dbc.game.team-a.url={loc_a}",
            f"-Dbc.game.team-b.url={loc_b}",
            f"-Dbc.game.team-a.package={team_a}",
            f"-Dbc.game.team-b.package={team_b}",
            f"-Dbc.game.maps={map_name}",
            "-Dbc.server.validate-maps=false",
            "-Dbc.server.alternate-order=false",
            f"-Dbc.server.save-file={replay_out}",
            "-cp",
            runtime.classpath,
            "battlecode.server.Main",
            "-c=-",
        ]

    def _run_direct(
        self,
        team_a: str,
        team_b: str,
        map_name: str,
        replay_out: Path,
        class_location_a: Optional[str],
        class_location_b: Optional[str],
        timeout_s: int,
    ) -> MatchRunResult:
        runtime = self._get_runtime()
        cmd = self._build_cmd(
            runtime, team_a, team_b, map_name, replay_out,
            class_location_a, class_location_b,
        )
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.engine.engine_path),  # bc.server.map-path=maps is relative
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start
            out = exc.stdout or b""
            raise MatchRunError(
                f"direct match timed out after {duration:.1f}s "
                f"(timeout_s={timeout_s}): {team_a} vs {team_b} on {map_name}",
                stdout_tail=out.decode("utf-8", errors="replace")[-_STDOUT_TAIL_CHARS:],
            ) from exc
        duration = time.monotonic() - start
        tail = (proc.stdout or b"").decode("utf-8", errors="replace")[-_STDOUT_TAIL_CHARS:]
        if proc.returncode != 0:
            raise MatchRunError(
                f"direct battlecode.server.Main exited {proc.returncode}: "
                f"{team_a} vs {team_b} on {map_name}",
                stdout_tail=tail,
                returncode=proc.returncode,
            )
        if not replay_out.exists() or replay_out.stat().st_size == 0:
            raise MatchRunError(
                f"direct run exited 0 but replay missing or empty: {replay_out}",
                stdout_tail=tail,
                returncode=proc.returncode,
            )
        return MatchRunResult(
            replay_path=str(replay_out),
            returncode=proc.returncode,
            duration_s=duration,
            stdout_tail=tail,
        )

    # ------------------------------------------------------------------

    def run_match(
        self,
        team_a: str,
        team_b: str,
        map_name: str,
        replay_out: Union[str, Path],
        class_location_a: Optional[str] = None,
        class_location_b: Optional[str] = None,
        timeout_s: int = 2700,
    ) -> MatchRunResult:
        """Run one match; direct java first, gradle fallback (or forced)."""
        _validate_single_map(map_name)
        replay_out = Path(replay_out).absolute()
        if replay_out.suffix != ".bc26":
            raise ValueError(f"replay_out must end with .bc26, got {replay_out}")
        replay_out.parent.mkdir(parents=True, exist_ok=True)

        def _via_gradle() -> MatchRunResult:
            return gradle_run_match(
                team_a=team_a,
                team_b=team_b,
                map_name=map_name,
                replay_out=replay_out,
                engine=self.engine,
                class_location_a=class_location_a,
                class_location_b=class_location_b,
                timeout_s=timeout_s,
            )

        if self.use_gradle:
            return _via_gradle()
        try:
            return self._run_direct(
                team_a, team_b, map_name, replay_out,
                class_location_a, class_location_b, timeout_s,
            )
        except (MatchRunError, ClasspathResolutionError, OSError) as direct_exc:
            # Transparent fallback.  If gradle succeeds where direct failed,
            # the direct path (not the match) is broken: pin to gradle.
            result = _via_gradle()
            self.use_gradle = True
            self._last_fallback_reason = str(direct_exc)  # for run.log diagnostics
            return result
