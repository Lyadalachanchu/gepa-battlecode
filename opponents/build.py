"""Opponent bot compilation against the pinned engine (PLAN.md section 10).

Each opponent package is compiled in isolation: only the package's own
``*.java`` files are copied into a clean staging tree (dev scraps -- jinja
templates, notes, sibling packages -- are left behind), then ``javac`` runs
with the pinned engine's compiled classes on the classpath.

A bot that fails to compile against the final-API engine is *recorded* as
failed and excluded -- never patched (PLAN section 10: a failed bot is data).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Union

# Where the pinned engine's compiled classes live, relative to the engine
# checkout.  The 103abf6 engine sets ``java.destinationDirectory`` to
# ``engine/build/classes`` (no gradle ``java/main`` suffix); the suffixed
# layout is checked first in case the engine build ever moves to defaults.
_ENGINE_CLASSES_CANDIDATES = (
    "engine/build/classes/java/main",
    "engine/build/classes",
)

_JAVAC_OUTPUT_MAX_CHARS = 20000


@dataclass(frozen=True)
class CompileResult:
    """Outcome of one ``compile_bot`` invocation."""

    ok: bool
    javac_output: str
    out_classes_dir: str = ""
    num_sources: int = 0


def ensure_engine_classes(engine_path: Union[str, Path]) -> Path:
    """Return the engine's compiled-classes dir, building it once if missing.

    Checks the known output locations; if none exists, runs
    ``./gradlew :engine:classes`` in the engine checkout and re-checks.
    """
    engine_dir = Path(engine_path)
    for rel in _ENGINE_CLASSES_CANDIDATES:
        cand = engine_dir / rel
        if (cand / "battlecode" / "common").is_dir():
            return cand
    gradlew = engine_dir / "gradlew"
    if not gradlew.exists():
        raise RuntimeError(f"engine classes missing and no gradlew at {gradlew}")
    proc = subprocess.run(
        [str(gradlew), ":engine:classes"],
        cwd=str(engine_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=600,
    )
    for rel in _ENGINE_CLASSES_CANDIDATES:
        cand = engine_dir / rel
        if (cand / "battlecode" / "common").is_dir():
            return cand
    tail = (proc.stdout or b"").decode("utf-8", errors="replace")[-2000:]
    raise RuntimeError(
        f"could not produce engine classes under {engine_dir} "
        f"(gradle exit {proc.returncode})\n--- gradle tail ---\n{tail}"
    )


def _stage_package_sources(source_dir: Path, package: str, staging_root: Path) -> list[Path]:
    """Copy only the package's ``*.java`` files into ``staging_root/<package>``.

    Preserves the sub-package directory tree; everything that is not a
    ``.java`` file (templates, notes, data) stays behind.
    """
    pkg_src = source_dir / package
    if not pkg_src.is_dir():
        raise ValueError(f"package dir not found: {pkg_src}")
    staged: list[Path] = []
    for src in sorted(pkg_src.rglob("*.java")):
        if not src.is_file():
            continue
        dst = staging_root / package / src.relative_to(pkg_src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        staged.append(dst)
    return staged


def compile_bot(
    source_dir: Union[str, Path],
    package: str,
    out_classes_dir: Union[str, Path],
    engine_classes_cp: Union[str, Path],
    timeout_s: int = 300,
) -> CompileResult:
    """Compile one opponent package against the pinned engine classes.

    ``source_dir`` is the directory *containing* the package directory (e.g. a
    repo's ``src/``).  Classes land under ``out_classes_dir/<package>/...``,
    ready to be passed as a harness ``class_location``.  On failure the output
    dir is removed so a half-built tree can never be mistaken for a bot.
    """
    source_dir = Path(source_dir)
    out_classes_dir = Path(out_classes_dir)
    engine_classes_cp = Path(engine_classes_cp)
    if not engine_classes_cp.exists():
        raise ValueError(f"engine classpath does not exist: {engine_classes_cp}")

    staging_root = Path(tempfile.mkdtemp(prefix="botsrc_"))
    try:
        staged = _stage_package_sources(source_dir, package, staging_root)
        if not staged:
            return CompileResult(
                ok=False,
                javac_output=f"no .java files under {source_dir / package}",
            )

        if out_classes_dir.exists():
            shutil.rmtree(out_classes_dir)
        out_classes_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "javac",
            "-encoding", "UTF-8",
            "-proc:none",
            "-cp", str(engine_classes_cp),
            "-sourcepath", str(staging_root),
            "-d", str(out_classes_dir),
        ] + [str(p) for p in staged]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            shutil.rmtree(out_classes_dir, ignore_errors=True)
            return CompileResult(
                ok=False,
                javac_output=f"javac timed out after {timeout_s}s",
                num_sources=len(staged),
            )

        output = (proc.stdout or b"").decode("utf-8", errors="replace")
        if len(output) > _JAVAC_OUTPUT_MAX_CHARS:
            output = output[:_JAVAC_OUTPUT_MAX_CHARS] + "\n... [javac output truncated]"
        ok = proc.returncode == 0
        if not ok:
            shutil.rmtree(out_classes_dir, ignore_errors=True)
        return CompileResult(
            ok=ok,
            javac_output=output,
            out_classes_dir=str(out_classes_dir) if ok else "",
            num_sources=len(staged),
        )
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
