"""Tests for model/client.py, model/prompts.py, model/repair.py.

No network: every test injects a fake transport.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.client import LunaClient, ModelCall, TransientTransportError
from model.prompts import (
    build_reflection_prompt,
    build_repair_prompt,
    build_score_only_prompt,
    load_rules_digest,
)
from model.repair import build_repair_call

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = REPO_ROOT / "configs" / "model.lock.json"


def good_payload() -> dict:
    return {
        "action": "patch",
        "reflection": {
            "observations": ["obs"],
            "causal_hypothesis": "cause",
            "general_lesson": "lesson",
            "evidence": [{"replay_id": "r1", "rounds": [3], "explanation": "why"}],
        },
        "mutation": {
            "target_component": "economy",
            "hypothesis": "h",
            "expected_improvement": "e",
            "regression_risks": ["r"],
            "component_source": "public class Economy {}\n",
        },
    }


def fake_response(payload: dict) -> dict:
    return {
        "output_text": json.dumps(payload),
        "usage": {"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 30},
        "model": "gpt-5.6-luna-2026-01-15",
    }


def make_client(tmp_path: Path, transport) -> LunaClient:
    return LunaClient.from_lock(
        call_log_path=tmp_path / "calls.jsonl",
        lock_path=LOCK_PATH,
        transport=transport,
        sleep=lambda s: None,
        clock=lambda: 1234.5,
    )


# ---------------------------------------------------------------- client basics


def test_from_lock_reads_model_and_effort(tmp_path):
    client = make_client(tmp_path, transport=lambda req: fake_response(good_payload()))
    lock = json.loads(LOCK_PATH.read_text())
    assert client.model_id == lock["model"] == "gpt-5.6-luna"
    assert client.reasoning_effort == lock["reasoning_effort"]
    assert client.max_output_tokens == lock["max_output_tokens"]


def test_reflect_and_patch_returns_parsed_dict(tmp_path):
    requests = []

    def transport(request):
        requests.append(request)
        return fake_response(good_payload())

    client = make_client(tmp_path, transport)
    call = client.reflect_and_patch("SYSTEM", "USER")

    assert isinstance(call, ModelCall)
    assert call.error is None
    assert call.parsed == good_payload()
    assert call.usage == {"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 30}
    assert call.model_id == "gpt-5.6-luna-2026-01-15"

    # request shape matches the Responses API structured-outputs contract
    (request,) = requests
    assert request["model"] == "gpt-5.6-luna"
    assert request["instructions"] == "SYSTEM"
    assert request["input"] == "USER"
    assert request["reasoning"] == {"effort": "high"}
    fmt = request["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["strict"] is True
    assert fmt["name"] == "reflect_and_patch"
    assert fmt["schema"]["properties"]["action"]["enum"] == ["patch", "no_change"]


def test_call_logged_as_jsonl_line(tmp_path):
    client = make_client(tmp_path, transport=lambda req: fake_response(good_payload()))
    client.reflect_and_patch("SYSTEM", "USER")
    client.reflect_and_patch("SYSTEM", "USER2")

    lines = (tmp_path / "calls.jsonl").read_text().splitlines()
    assert len(lines) == 2
    entry = json.loads(lines[0])
    assert entry["model_id"] == "gpt-5.6-luna-2026-01-15"
    assert entry["usage"] == {"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 30}
    assert entry["timestamp"] == 1234.5
    assert entry["parsed_ok"] is True
    assert len(entry["prompt_sha256"]) == 64
    int(entry["prompt_sha256"], 16)  # valid hex
    # different prompts hash differently
    assert json.loads(lines[1])["prompt_sha256"] != entry["prompt_sha256"]


def test_retries_transient_failure_then_succeeds(tmp_path):
    attempts = []
    sleeps = []

    def flaky_transport(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise TransientTransportError("HTTP 503")
        return fake_response(good_payload())

    client = LunaClient.from_lock(
        call_log_path=tmp_path / "calls.jsonl",
        lock_path=LOCK_PATH,
        transport=flaky_transport,
        sleep=sleeps.append,
        clock=lambda: 0.0,
    )
    call = client.reflect_and_patch("S", "U")

    assert call.error is None
    assert call.parsed == good_payload()
    assert len(attempts) == 3
    # exponential backoff: base, base*2
    assert sleeps == [client.backoff_base_seconds, client.backoff_base_seconds * 2]
    entry = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[0])
    assert entry["attempts"] == 3


def test_gives_up_after_max_attempts(tmp_path):
    def always_down(request):
        raise TransientTransportError("HTTP 503")

    client = make_client(tmp_path, always_down)
    call = client.reflect_and_patch("S", "U")

    assert call.parsed is None
    assert "transient" in call.error
    entry = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[0])
    assert entry["attempts"] == 3
    assert entry["parsed_ok"] is False


def test_non_retryable_error_fails_immediately(tmp_path):
    attempts = []

    def bad_request(request):
        attempts.append(1)
        raise ValueError("invalid schema")

    client = make_client(tmp_path, bad_request)
    call = client.reflect_and_patch("S", "U")
    assert call.parsed is None
    assert "ValueError" in call.error
    assert len(attempts) == 1


def test_schema_invalid_output_reported_not_raised(tmp_path):
    def transport(request):
        return {
            "output_text": '{"action": "patch"}',
            "usage": {"input_tokens": 1, "output_tokens": 1, "reasoning_tokens": 0},
            "model": "gpt-5.6-luna",
        }

    client = make_client(tmp_path, transport)
    call = client.reflect_and_patch("S", "U")
    assert call.parsed is None
    assert "schema validation failed" in call.error
    assert call.raw_text == '{"action": "patch"}'


# -------------------------------------------------------------------- prompts


def test_reflection_prompt_contains_rules_and_source():
    digest = load_rules_digest()
    assert "Battlecode 2026" in digest  # loaded from configs/game_rules.md

    system, user = build_reflection_prompt(
        rules_digest=digest,
        component_name="economy",
        component_source="public class Economy { int SECRET_MARKER_42; }",
        other_interfaces="interface Combat { void fight(); }",
        mutation_history=["iter1: widened trap radius"],
        packed_traces="TRACE_T0 map=DefaultSmall\nround 5: spawn",
    )
    assert "Battlecode 2026" in user  # rules digest present
    assert "SECRET_MARKER_42" in user  # component source present
    assert "interface Combat" in user
    assert "widened trap radius" in user
    assert "TRACE_T0 map=DefaultSmall" in user
    assert "economy" in user
    # system prompt states the required behaviors
    for phrase in ("ONLY", "cite", "complete", "study"):
        assert phrase.lower() in system.lower()


def test_score_only_prompt_contains_outcomes_not_traces():
    system, user = build_score_only_prompt(
        rules_digest=load_rules_digest(),
        component_name="combat",
        component_source="public class Combat {}",
        other_interfaces="",
        mutation_history=[],
        outcomes_summary="game1: LOSS vs sprint1bot on DefaultSmall margin -0.4",
    )
    assert "game1: LOSS" in user
    assert "Battlecode 2026" in user
    assert "public class Combat {}" in user
    assert "outcome" in system.lower() or "outcome" in user.lower()


def test_prompts_are_deterministic():
    args = dict(
        rules_digest="RULES",
        component_name="navigation",
        component_source="src",
        other_interfaces="ifaces",
        mutation_history=["a", "b"],
        packed_traces="traces",
    )
    assert build_reflection_prompt(**args) == build_reflection_prompt(**args)


def test_repair_prompt_contains_prior_output_and_compiler_error():
    system, user = build_repair_prompt(good_payload(), "Economy.java:12: error: ';' expected")
    assert "';' expected" in user
    assert "public class Economy {}" in user
    assert "patch" in system


def test_build_repair_call_from_prior_model_call():
    prior = ModelCall(
        parsed=good_payload(),
        raw_text=json.dumps(good_payload()),
        usage={"input_tokens": 1, "output_tokens": 1, "reasoning_tokens": 0},
        model_id="gpt-5.6-luna",
    )
    system, user = build_repair_call(prior, "javac: cannot find symbol foo")
    assert "cannot find symbol foo" in user
    assert "public class Economy {}" in user


def test_build_repair_call_with_unparsed_prior():
    prior = ModelCall(
        parsed=None,
        raw_text="not json at all",
        usage={"input_tokens": 1, "output_tokens": 1, "reasoning_tokens": 0},
        model_id="gpt-5.6-luna",
        error="schema validation failed: ...",
    )
    system, user = build_repair_call(prior, "validator: forbidden API")
    assert "not json at all" in user
    assert "forbidden API" in user
