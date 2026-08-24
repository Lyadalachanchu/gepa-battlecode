"""Structured-output JSON schema for the reflect-and-patch model call.

PLAN.md section 8 defines the exact output shape. The schema below is written
for the OpenAI Responses API structured-outputs strict mode:

- every object lists all of its properties in ``required`` (strict mode
  demands this), so optionality is encoded via nullability;
- ``additionalProperties: false`` everywhere;
- ``mutation`` is ``type: ["object", "null"]`` — it must be ``null`` when
  ``action == "no_change"`` and a fully-populated object when
  ``action == "patch"``.  That cross-field constraint cannot be expressed in
  the strict-mode schema subset, so :func:`parse_and_validate` enforces it.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "REFLECT_AND_PATCH_SCHEMA",
    "SCHEMA_NAME",
    "SchemaError",
    "parse_and_validate",
]

SCHEMA_NAME = "reflect_and_patch"

_EVIDENCE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["replay_id", "rounds", "explanation"],
    "properties": {
        "replay_id": {
            "type": "string",
            "description": "Identifier of the replay/trace the evidence comes from.",
        },
        "rounds": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Round numbers in that replay supporting the claim.",
        },
        "explanation": {
            "type": "string",
            "description": "What those rounds show and why it matters.",
        },
    },
}

_REFLECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["observations", "causal_hypothesis", "general_lesson", "evidence"],
    "properties": {
        "observations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete things noticed in the provided evidence.",
        },
        "causal_hypothesis": {
            "type": "string",
            "description": "The single most likely cause of the observed weakness.",
        },
        "general_lesson": {
            "type": "string",
            "description": "A transferable lesson, not tied to one match.",
        },
        "evidence": {
            "type": "array",
            "items": _EVIDENCE_ITEM_SCHEMA,
            "description": "Round-level citations backing the observations.",
        },
    },
}

_MUTATION_SCHEMA: dict[str, Any] = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": [
        "target_component",
        "hypothesis",
        "expected_improvement",
        "regression_risks",
        "component_source",
    ],
    "properties": {
        "target_component": {
            "type": "string",
            "description": "Name of the ONE component being rewritten (must be the selected component).",
        },
        "hypothesis": {
            "type": "string",
            "description": "Why this change should help, tied to the reflection.",
        },
        "expected_improvement": {
            "type": "string",
            "description": "What should measurably improve if the hypothesis is right.",
        },
        "regression_risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ways this change could make the bot worse.",
        },
        "component_source": {
            "type": "string",
            "description": "The COMPLETE new source of the selected component file.",
        },
    },
}

REFLECT_AND_PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "reflection", "mutation"],
    "properties": {
        "action": {
            "type": "string",
            "enum": ["patch", "no_change"],
            "description": "'patch' to rewrite the selected component, 'no_change' to keep it.",
        },
        "reflection": _REFLECTION_SCHEMA,
        "mutation": _MUTATION_SCHEMA,
    },
}


class SchemaError(ValueError):
    """Raised when model output does not conform to the reflect-and-patch schema.

    The message is written to be pasted directly into a repair prompt: it names
    the offending JSON path and states exactly what is required there.
    """


def _fail(path: str, problem: str) -> None:
    raise SchemaError(f"At {path}: {problem}")


def _require_str(value: Any, path: str) -> None:
    if not isinstance(value, str):
        _fail(path, f"expected a JSON string, got {type(value).__name__}")


def _require_str_list(value: Any, path: str) -> None:
    if not isinstance(value, list):
        _fail(path, f"expected a JSON array of strings, got {type(value).__name__}")
    for i, item in enumerate(value):
        if not isinstance(item, str):
            _fail(f"{path}[{i}]", f"expected a string, got {type(item).__name__}")


def _check_keys(obj: dict[str, Any], required: list[str], path: str) -> None:
    missing = [k for k in required if k not in obj]
    if missing:
        _fail(path, f"missing required key(s): {', '.join(missing)}")
    extra = [k for k in obj if k not in required]
    if extra:
        _fail(
            path,
            f"unexpected key(s): {', '.join(extra)} "
            f"(additionalProperties is false; allowed keys are {', '.join(required)})",
        )


def parse_and_validate(raw_json_str: str) -> dict[str, Any]:
    """Parse ``raw_json_str`` and validate it against the reflect-and-patch schema.

    Returns the parsed dict on success.  Raises :class:`SchemaError` with a
    repair-prompt-ready message on any violation, including the cross-field
    rule that ``mutation`` must be present (non-null) iff ``action == "patch"``.
    """
    try:
        obj = json.loads(raw_json_str)
    except json.JSONDecodeError as exc:
        raise SchemaError(
            f"Output is not valid JSON: {exc}. "
            "Emit a single JSON object matching the reflect_and_patch schema."
        ) from None

    if not isinstance(obj, dict):
        _fail("$", f"expected a JSON object, got {type(obj).__name__}")

    _check_keys(obj, ["action", "reflection", "mutation"], "$")

    action = obj["action"]
    if action not in ("patch", "no_change"):
        _fail("$.action", f"must be 'patch' or 'no_change', got {action!r}")

    reflection = obj["reflection"]
    if not isinstance(reflection, dict):
        _fail("$.reflection", f"expected an object, got {type(reflection).__name__}")
    _check_keys(
        reflection,
        ["observations", "causal_hypothesis", "general_lesson", "evidence"],
        "$.reflection",
    )
    _require_str_list(reflection["observations"], "$.reflection.observations")
    _require_str(reflection["causal_hypothesis"], "$.reflection.causal_hypothesis")
    _require_str(reflection["general_lesson"], "$.reflection.general_lesson")

    evidence = reflection["evidence"]
    if not isinstance(evidence, list):
        _fail("$.reflection.evidence", f"expected an array, got {type(evidence).__name__}")
    for i, item in enumerate(evidence):
        path = f"$.reflection.evidence[{i}]"
        if not isinstance(item, dict):
            _fail(path, f"expected an object, got {type(item).__name__}")
        _check_keys(item, ["replay_id", "rounds", "explanation"], path)
        _require_str(item["replay_id"], f"{path}.replay_id")
        rounds = item["rounds"]
        if not isinstance(rounds, list):
            _fail(f"{path}.rounds", f"expected an array of integers, got {type(rounds).__name__}")
        for j, r in enumerate(rounds):
            if not isinstance(r, int) or isinstance(r, bool):
                _fail(f"{path}.rounds[{j}]", f"expected an integer, got {type(r).__name__}")
        _require_str(item["explanation"], f"{path}.explanation")

    mutation = obj["mutation"]
    if action == "no_change":
        if mutation is not None:
            _fail(
                "$.mutation",
                "must be null when action is 'no_change' (no mutation may accompany no_change)",
            )
    else:  # action == "patch"
        if not isinstance(mutation, dict):
            _fail(
                "$.mutation",
                "must be a fully-populated object when action is 'patch' "
                f"(got {'null' if mutation is None else type(mutation).__name__})",
            )
        _check_keys(
            mutation,
            [
                "target_component",
                "hypothesis",
                "expected_improvement",
                "regression_risks",
                "component_source",
            ],
            "$.mutation",
        )
        _require_str(mutation["target_component"], "$.mutation.target_component")
        _require_str(mutation["hypothesis"], "$.mutation.hypothesis")
        _require_str(mutation["expected_improvement"], "$.mutation.expected_improvement")
        _require_str_list(mutation["regression_risks"], "$.mutation.regression_risks")
        _require_str(mutation["component_source"], "$.mutation.component_source")
        if not mutation["component_source"].strip():
            _fail(
                "$.mutation.component_source",
                "must contain the complete rewritten component source, not an empty string",
            )

    return obj
