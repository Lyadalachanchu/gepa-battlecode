"""Prompt builders for the reflect-and-patch loop.

Pure, deterministic string assembly — no I/O except :func:`load_rules_digest`,
no timestamps, no randomness.  Identical inputs always produce identical
prompts, which keeps the stable prefix (rules digest + interfaces) usable for
prompt caching.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "load_rules_digest",
    "build_reflection_prompt",
    "build_score_only_prompt",
    "build_repair_prompt",
]

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES_PATH = _REPO_ROOT / "configs" / "game_rules.md"


def load_rules_digest(path: str | Path | None = None) -> str:
    """Load the frozen, model-facing rules digest (configs/game_rules.md)."""
    p = Path(path) if path is not None else DEFAULT_RULES_PATH
    return p.read_text(encoding="utf-8")


_SYSTEM_COMMON = """\
You are improving a Battlecode 2026 bot written in Java. You are the only \
optimizer: you must study the evidence yourself, form your own diagnosis, and \
then decide whether to rewrite ONE component of the bot.

Hard rules:
- Study the provided game evidence yourself. Nobody has pre-analyzed it for \
you and no diagnosis is supplied.
- Every claim in your reflection must cite concrete rounds from the provided \
evidence (replay_id + round numbers). Cited rounds are checked against the \
raw traces; ungrounded citations are treated as failures.
- You may change ONLY the selected component named below. Do not modify, \
rename, or restructure any other component, and do not change the selected \
component's public interface that other components depend on.
- If you patch, output the COMPLETE rewritten source file for the selected \
component — the entire file from first line to last, not a diff, not an \
excerpt. It replaces the old file verbatim.
- Patches changing more than 250 lines, touching other files, or using \
indicator strings, reflection, threads, or I/O are rejected.
- Respond only with a JSON object matching the provided schema. Set action to \
"no_change" (with mutation null) only if you conclude no safe improvement to \
the selected component exists.
"""

_SYSTEM_REFLECTION = _SYSTEM_COMMON + """\
- Your evidence is a set of complete decoded game traces (deterministic \
projections of official replays). Read them round by round; ground your \
causal hypothesis in specific events you can point to.
"""

_SYSTEM_SCORE_ONLY = _SYSTEM_COMMON + """\
- Your evidence is limited to match outcome summaries (scores only, no \
traces). Cite the summarized matches by replay_id; use round citations only \
where the summary provides round-level facts.
"""


def _numbered(items: list[str]) -> str:
    if not items:
        return "(none)"
    return "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))


def _common_user_sections(
    rules_digest: str,
    component_name: str,
    component_source: str,
    other_interfaces: str,
    mutation_history: list[str],
) -> str:
    return (
        "## Game rules digest (frozen)\n\n"
        f"{rules_digest.rstrip()}\n\n"
        "## Selected component (the ONLY file you may rewrite)\n\n"
        f"Component name: {component_name}\n\n"
        "```java\n"
        f"{component_source.rstrip()}\n"
        "```\n\n"
        "## Other components (interfaces only — read-only context)\n\n"
        f"{other_interfaces.rstrip() or '(none)'}\n\n"
        "## Recent accepted mutations (most recent last)\n\n"
        f"{_numbered(mutation_history)}\n\n"
    )


def build_reflection_prompt(
    rules_digest: str,
    component_name: str,
    component_source: str,
    other_interfaces: str,
    mutation_history: list[str],
    packed_traces: str,
) -> tuple[str, str]:
    """Build (system, user) for a trajectory-reflection call (arms B/C/D)."""
    user = (
        _common_user_sections(
            rules_digest, component_name, component_source, other_interfaces, mutation_history
        )
        + "## Decoded game traces\n\n"
        + f"{packed_traces.rstrip()}\n\n"
        + "## Task\n\n"
        + "Study the traces above round by round. Produce your reflection "
        + "(observations, causal hypothesis, general lesson, round-cited "
        + f"evidence), then either rewrite the complete `{component_name}` "
        + "component to address the diagnosed weakness, or answer no_change. "
        + "Respond with a single JSON object matching the schema."
    )
    return _SYSTEM_REFLECTION, user


def build_score_only_prompt(
    rules_digest: str,
    component_name: str,
    component_source: str,
    other_interfaces: str,
    mutation_history: list[str],
    outcomes_summary: str,
) -> tuple[str, str]:
    """Build (system, user) for an outcomes-only call (arm A)."""
    user = (
        _common_user_sections(
            rules_digest, component_name, component_source, other_interfaces, mutation_history
        )
        + "## Match outcome summaries (no traces available)\n\n"
        + f"{outcomes_summary.rstrip()}\n\n"
        + "## Task\n\n"
        + "Using only the outcome summaries above, produce your reflection "
        + "(observations, causal hypothesis, general lesson, evidence citing "
        + f"the summarized matches), then either rewrite the complete "
        + f"`{component_name}` component to address the diagnosed weakness, "
        + "or answer no_change. Respond with a single JSON object matching "
        + "the schema."
    )
    return _SYSTEM_SCORE_ONLY, user


_SYSTEM_REPAIR = """\
You are repairing your previous Battlecode 2026 component rewrite, which \
failed to compile or was rejected by the static validator. This is your one \
repair attempt: fix the reported problems and nothing else.

Hard rules:
- Change ONLY the same selected component as before; keep the same \
target_component.
- Output the COMPLETE corrected source file (entire file, not a diff).
- Keep your original intent and reflection; do not introduce new features, \
and do not use indicator strings, reflection, threads, or I/O.
- Respond only with a JSON object matching the provided schema, with action \
"patch".
"""


def build_repair_prompt(
    original_output: dict[str, Any],
    compiler_output: str,
) -> tuple[str, str]:
    """Build (system, user) for the one-attempt compile-repair call.

    ``original_output`` is the parsed (or best-effort) dict from the failed
    attempt; ``compiler_output`` is the raw validator/javac output.
    """
    original_json = json.dumps(original_output, indent=2, sort_keys=True)
    user = (
        "## Your previous output (failed to compile / rejected)\n\n"
        "```json\n"
        f"{original_json}\n"
        "```\n\n"
        "## Compiler / validator output\n\n"
        "```\n"
        f"{compiler_output.rstrip()}\n"
        "```\n\n"
        "## Task\n\n"
        "Fix the problems reported above in your component_source and return "
        "the corrected complete file as a JSON object matching the schema "
        '(action "patch", same target_component). Do not make any other '
        "changes."
    )
    return _SYSTEM_REPAIR, user
