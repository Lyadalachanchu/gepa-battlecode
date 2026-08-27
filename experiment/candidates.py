"""Candidate materialization: components dict -> compiled Java bot.

A :class:`~optimizer.candidate.Candidate` is a mapping of component name ->
full Java source (the modular seed: ``robotplayer`` glue + ``economy`` /
``combat`` / ``defense`` / ``navigation`` / ``strategy``; ``robotplayer`` is
glue and never appears in the mutable components list).  Materialization
writes those sources into a package directory with the package declaration
rewritten deterministically to ``candidate``, then javac-compiles against the
pinned engine's classes (reusing :mod:`opponents.build`).

The compiled classes dir is passed to the runner as a class location with
team name == package name == ``candidate``.
"""

from __future__ import annotations

import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Union

from harness.runner import EngineConfig
from opponents.build import compile_bot, ensure_engine_classes
from optimizer.candidate import Candidate, candidate_id_for

__all__ = [
    "DEFAULT_PACKAGE",
    "GLUE_COMPONENT",
    "CompiledCandidate",
    "CandidateCompileError",
    "component_class_name",
    "rewrite_package_decl",
    "materialize_candidate",
    "CandidateCompiler",
]

DEFAULT_PACKAGE = "candidate"
GLUE_COMPONENT = "robotplayer"  # never in the mutable components list

_PACKAGE_DECL_RE = re.compile(r"^\s*package\s+[A-Za-z_$][\w$.]*\s*;", re.MULTILINE)
_TYPE_DECL_RE = re.compile(
    r"^\s*(?:public\s+)?(?:final\s+|abstract\s+|strictfp\s+)*"
    r"(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


class CandidateCompileError(RuntimeError):
    """Materialization or javac compilation of a candidate failed."""

    def __init__(self, message: str, javac_output: str = ""):
        super().__init__(message)
        self.javac_output = javac_output


def component_class_name(name: str, source: str) -> str:
    """The top-level Java type name declared in a component source.

    The filename must match the public type name, so this is parsed from the
    source, never guessed from the component key.
    """
    m = _TYPE_DECL_RE.search(source)
    if m is None:
        raise CandidateCompileError(
            f"component {name!r}: no top-level class/interface/enum declaration found"
        )
    return m.group(1)


def rewrite_package_decl(source: str, package: str) -> str:
    """Deterministically force the file's package declaration to ``package``.

    Replaces the first ``package X;`` declaration, or prepends one if the
    file has none.
    """
    if _PACKAGE_DECL_RE.search(source):
        return _PACKAGE_DECL_RE.sub(f"package {package};", source, count=1)
    return f"package {package};\n\n" + source


@dataclass(frozen=True)
class CompiledCandidate:
    """A materialized, successfully compiled candidate."""

    candidate_id: str
    package: str
    source_dir: str  # directory CONTAINING the package dir (javac sourcepath root)
    classes_dir: str  # pass as the runner's class_location


def materialize_candidate(
    candidate: Union[Candidate, Mapping[str, str]],
    work_dir: Union[str, Path],
    engine: Optional[EngineConfig] = None,
    package: str = DEFAULT_PACKAGE,
) -> CompiledCandidate:
    """Write component sources under ``work_dir`` and compile them.

    Layout: ``work_dir/src/<package>/<ClassName>.java`` and
    ``work_dir/classes/<package>/*.class``.  Raises
    :class:`CandidateCompileError` (with javac output) on failure.
    """
    components = (
        candidate.components if isinstance(candidate, Candidate) else candidate
    )
    if not components:
        raise CandidateCompileError("candidate has no components")
    cid = (
        candidate.candidate_id
        if isinstance(candidate, Candidate)
        else candidate_id_for(components)
    )
    if engine is None:
        engine = EngineConfig.from_lock()

    work_dir = Path(work_dir)
    src_root = work_dir / "src"
    pkg_dir = src_root / package
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    seen_files: dict[str, str] = {}
    for name in sorted(components):
        source = components[name]
        cls = component_class_name(name, source)
        fname = f"{cls}.java"
        if fname in seen_files:
            raise CandidateCompileError(
                f"components {seen_files[fname]!r} and {name!r} both declare "
                f"top-level type {cls!r}"
            )
        seen_files[fname] = name
        (pkg_dir / fname).write_text(
            rewrite_package_decl(source, package), encoding="utf-8"
        )

    engine_cp = ensure_engine_classes(engine.engine_path)
    result = compile_bot(
        source_dir=src_root,
        package=package,
        out_classes_dir=work_dir / "classes",
        engine_classes_cp=engine_cp,
    )
    if not result.ok:
        raise CandidateCompileError(
            f"javac failed for candidate {cid[:12]}", javac_output=result.javac_output
        )
    return CompiledCandidate(
        candidate_id=cid,
        package=package,
        source_dir=str(src_root),
        classes_dir=result.out_classes_dir,
    )


class CandidateCompiler:
    """Materialize + compile candidates with per-content caching.

    Work trees live under ``root_dir/<candidate_id[:16]>``; a candidate id
    that already compiled in this process (or whose classes dir survives on
    disk with its OK marker) is reused for free.  ``compile_check`` matches
    the OptimizerLoop injected-callable contract.
    """

    _OK_MARKER = "compile_ok"

    def __init__(
        self,
        root_dir: Union[str, Path],
        engine: Optional[EngineConfig] = None,
        package: str = DEFAULT_PACKAGE,
    ):
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.engine = engine if engine is not None else EngineConfig.from_lock()
        self.package = package
        self._compiled: dict[str, CompiledCandidate] = {}
        self._failed: dict[str, str] = {}  # cid -> javac output
        # Concurrent compiles of the SAME candidate share a staging dir and
        # clobber each other (seen with the held-out evaluator's thread pool);
        # serialize compilation — matches themselves still run in parallel.
        self._lock = threading.Lock()

    def _work_dir(self, cid: str) -> Path:
        return self.root / cid[:16]

    def compile_components(
        self, components: Mapping[str, str]
    ) -> tuple[bool, str, Optional[CompiledCandidate]]:
        """Compile a components dict; returns (ok, javac_output, compiled)."""
        with self._lock:
            return self._compile_components_locked(components)

    def _compile_components_locked(
        self, components: Mapping[str, str]
    ) -> tuple[bool, str, Optional[CompiledCandidate]]:
        cid = candidate_id_for(components)
        if cid in self._compiled:
            return True, "", self._compiled[cid]
        if cid in self._failed:
            return False, self._failed[cid], None

        work = self._work_dir(cid)
        marker = work / self._OK_MARKER
        classes = work / "classes"
        if marker.exists() and (classes / self.package).is_dir():
            compiled = CompiledCandidate(
                candidate_id=cid,
                package=self.package,
                source_dir=str(work / "src"),
                classes_dir=str(classes),
            )
            self._compiled[cid] = compiled
            return True, "", compiled

        try:
            compiled = materialize_candidate(
                dict(components), work, engine=self.engine, package=self.package
            )
        except CandidateCompileError as exc:
            output = exc.javac_output or str(exc)
            self._failed[cid] = output
            return False, output, None
        marker.write_text(cid + "\n", encoding="utf-8")
        self._compiled[cid] = compiled
        return True, "", compiled

    # -- OptimizerLoop contract: compile_check(components) -> (ok, errors) --
    def compile_check(self, components: Mapping[str, str]) -> tuple[bool, str]:
        ok, output, _ = self.compile_components(components)
        return ok, output

    def compiled_for(self, candidate: Union[Candidate, Mapping[str, str]]) -> CompiledCandidate:
        """CompiledCandidate for a candidate; raises if it does not compile."""
        components = (
            candidate.components if isinstance(candidate, Candidate) else candidate
        )
        ok, output, compiled = self.compile_components(components)
        if not ok or compiled is None:
            raise CandidateCompileError(
                f"candidate {candidate_id_for(components)[:12]} does not compile",
                javac_output=output,
            )
        return compiled
