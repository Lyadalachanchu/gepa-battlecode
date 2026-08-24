"""Static validation of LLM component rewrites (PLAN.md sections 8 and 18).

Mutations are full-file component rewrites; the harness diffs old vs new
itself and rejects patches over the changed-line cap or containing forbidden
API references (indicator strings, I/O, networking, threads, process control,
reflection).
"""

from __future__ import annotations

import difflib
from typing import List

DEFAULT_MAX_CHANGED_LINES = 250

# Forbidden substrings (checked against the NEW source). Presence anywhere in
# the rewrite is a violation -- these are channels for leaking notes to the
# reflector (indicators) or escaping the bytecode sandbox (I/O, threads,
# reflection, process control).
FORBIDDEN_SUBSTRINGS = (
    "setIndicator",
    "IndicatorString",
    "java.io",
    "java.net",
    "java.nio.file",
    "Thread",
    "Runtime",
    "ProcessBuilder",
    "System.exit",
    "Class.forName",
    "reflect",
)


def changed_lines(old_src: str, new_src: str) -> int:
    """Number of changed lines: added + removed lines of a unified diff
    (``+``/``-`` body lines; the ``+++``/``---`` file headers do not count)."""
    diff = difflib.unified_diff(
        old_src.splitlines(),
        new_src.splitlines(),
        lineterm="",
        n=0,
    )
    count = 0
    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            count += 1
    return count


def validate_component_rewrite(
    old_src: str,
    new_src: str,
    max_changed_lines: int = DEFAULT_MAX_CHANGED_LINES,
) -> List[str]:
    """Return a list of human-readable violations; an empty list means OK."""
    violations: List[str] = []

    n = changed_lines(old_src, new_src)
    if n > max_changed_lines:
        violations.append(
            f"changed-line cap exceeded: {n} changed lines > max {max_changed_lines}"
        )

    for needle in FORBIDDEN_SUBSTRINGS:
        if needle in new_src:
            violations.append(f"forbidden reference in rewrite: {needle!r}")

    return violations
