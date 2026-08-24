"""Tests for model/schemas.py — schema shape and parse_and_validate."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.schemas import REFLECT_AND_PATCH_SCHEMA, SchemaError, parse_and_validate


def good_patch_payload() -> dict:
    return {
        "action": "patch",
        "reflection": {
            "observations": [
                "Team A kings starved after round 800",
                "No cheese transfers between rounds 750 and 900",
            ],
            "causal_hypothesis": "Economy stops routing carriers once the cat blocks the mine lane.",
            "general_lesson": "Carriers need a fallback route when the primary lane is contested.",
            "evidence": [
                {
                    "replay_id": "smoke-001",
                    "rounds": [752, 803, 890],
                    "explanation": "King HP drops 10/round with zero transfer events.",
                }
            ],
        },
        "mutation": {
            "target_component": "economy",
            "hypothesis": "Adding a detour path around cat waypoints restores transfers.",
            "expected_improvement": "Cheese transferred aggregate stays positive after round 800.",
            "regression_risks": ["Longer routes may starve early spawning"],
            "component_source": "package bot;\npublic class Economy {\n  // full file\n}\n",
        },
    }


def good_no_change_payload() -> dict:
    return {
        "action": "no_change",
        "reflection": {
            "observations": ["Component behaved as intended in all cited games"],
            "causal_hypothesis": "Losses trace to combat, not the selected component.",
            "general_lesson": "Do not churn components that are not implicated.",
            "evidence": [
                {"replay_id": "smoke-002", "rounds": [12], "explanation": "Clean early economy."}
            ],
        },
        "mutation": None,
    }


# ------------------------------------------------------------------ acceptance


def test_valid_patch_payload_roundtrips():
    payload = good_patch_payload()
    assert parse_and_validate(json.dumps(payload)) == payload


def test_valid_no_change_payload_roundtrips():
    payload = good_no_change_payload()
    assert parse_and_validate(json.dumps(payload)) == payload


# ------------------------------------------------------------------- rejection


def test_rejects_non_json():
    with pytest.raises(SchemaError, match="not valid JSON"):
        parse_and_validate("this is not json {")


def test_rejects_missing_top_level_key():
    payload = good_patch_payload()
    del payload["reflection"]
    with pytest.raises(SchemaError, match=r"missing required key.*reflection"):
        parse_and_validate(json.dumps(payload))


def test_rejects_missing_reflection_field():
    payload = good_patch_payload()
    del payload["reflection"]["causal_hypothesis"]
    with pytest.raises(SchemaError, match="causal_hypothesis"):
        parse_and_validate(json.dumps(payload))


def test_rejects_missing_mutation_field():
    payload = good_patch_payload()
    del payload["mutation"]["component_source"]
    with pytest.raises(SchemaError, match="component_source"):
        parse_and_validate(json.dumps(payload))


def test_rejects_bad_action_value():
    payload = good_patch_payload()
    payload["action"] = "rewrite"
    with pytest.raises(SchemaError, match=r"\$\.action"):
        parse_and_validate(json.dumps(payload))


def test_rejects_patch_with_null_mutation():
    payload = good_patch_payload()
    payload["mutation"] = None
    with pytest.raises(SchemaError, match="action is 'patch'"):
        parse_and_validate(json.dumps(payload))


def test_rejects_no_change_with_mutation():
    payload = good_no_change_payload()
    payload["mutation"] = good_patch_payload()["mutation"]
    with pytest.raises(SchemaError, match="no_change"):
        parse_and_validate(json.dumps(payload))


def test_rejects_extra_keys():
    payload = good_patch_payload()
    payload["diagnosis"] = "sneaky extra"
    with pytest.raises(SchemaError, match="unexpected key"):
        parse_and_validate(json.dumps(payload))


def test_rejects_non_integer_rounds():
    payload = good_patch_payload()
    payload["reflection"]["evidence"][0]["rounds"] = ["12"]
    with pytest.raises(SchemaError, match=r"rounds\[0\]"):
        parse_and_validate(json.dumps(payload))


def test_rejects_empty_component_source():
    payload = good_patch_payload()
    payload["mutation"]["component_source"] = "   "
    with pytest.raises(SchemaError, match="component_source"):
        parse_and_validate(json.dumps(payload))


def test_error_message_names_json_path_for_repair_prompt():
    payload = good_patch_payload()
    del payload["mutation"]["hypothesis"]
    with pytest.raises(SchemaError) as exc_info:
        parse_and_validate(json.dumps(payload))
    assert "$.mutation" in str(exc_info.value)


# ---------------------------------------------------- strict-mode schema shape


def _walk_objects(node):
    if isinstance(node, dict):
        node_type = node.get("type")
        is_object = node_type == "object" or (
            isinstance(node_type, list) and "object" in node_type
        )
        if is_object:
            yield node
        for value in node.values():
            yield from _walk_objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_objects(item)


def test_schema_strict_mode_invariants():
    objects = list(_walk_objects(REFLECT_AND_PATCH_SCHEMA))
    assert objects, "schema should contain object nodes"
    for obj in objects:
        assert obj.get("additionalProperties") is False
        # strict mode: every property must be listed in required
        assert sorted(obj["required"]) == sorted(obj["properties"].keys())


def test_schema_mutation_is_nullable():
    mutation = REFLECT_AND_PATCH_SCHEMA["properties"]["mutation"]
    assert mutation["type"] == ["object", "null"]


def test_schema_action_enum():
    action = REFLECT_AND_PATCH_SCHEMA["properties"]["action"]
    assert action["enum"] == ["patch", "no_change"]
