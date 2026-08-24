"""Tests for experiment.reflection with a fake transport (no network ever)."""

import json
from pathlib import Path

import pytest

from experiment.reflection import (
    ReflectionAdapter,
    ensure_api_key,
    extract_interfaces,
    load_env_file,
    make_decode_traces,
    outcomes_summary,
)
from model.client import LunaClient
from optimizer.loop import ARMS

FIXTURE_REPLAY = Path(__file__).parent / "fixtures" / "smoke.bc26"

RULES = "## rules digest (test)"

COMPONENTS = {
    "robotplayer": "public class RobotPlayer { public static void run() {} }",
    "economy": "public class Economy {\n    public static int target() { return 1; }\n}",
    "combat": "public class Combat {\n    public static void fight() {}\n}",
}


def _patch_response(target="economy", source="public class Economy {}"):
    return json.dumps(
        {
            "action": "patch",
            "reflection": {
                "observations": ["obs"],
                "causal_hypothesis": "cause",
                "general_lesson": "lesson",
                "evidence": [{"replay_id": "g0", "rounds": [1], "explanation": "e"}],
            },
            "mutation": {
                "target_component": target,
                "hypothesis": "hyp",
                "expected_improvement": "better",
                "regression_risks": ["risk"],
                "component_source": source,
            },
        }
    )


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        text = self.responses.pop(0)
        return {
            "output_text": text,
            "usage": {"input_tokens": 10, "output_tokens": 5, "reasoning_tokens": 0},
            "model": "fake-model",
        }


def _client(tmp_path, transport):
    return LunaClient(
        model_id="fake-model",
        reasoning_effort="high",
        call_log_path=tmp_path / "calls.jsonl",
        transport=transport,
    )


def _payload(traces=None, scores=(0.1, 0.9)):
    p = {
        "iteration": 0,
        "target_component": "economy",
        "parent_id": "p0",
        "components": dict(COMPONENTS),
        "scores": list(scores),
    }
    if traces is not None:
        p["traces"] = traces
    return p


def test_arm_b_prompt_contains_packed_traces(tmp_path):
    transport = FakeTransport([_patch_response()])
    adapter = ReflectionAdapter(
        _client(tmp_path, transport), ARMS["replay_greedy"], tmp_path, rules_digest=RULES
    )
    trace_text = "=== REPLAY g0 opponent=x map=y side=A score=0.1 ===\nT1 AGGREGATES"
    proposal = adapter.model_call(_payload(traces=trace_text))
    assert proposal["action"] == "patch"
    assert proposal["target_component"] == "economy"
    assert proposal["component_source"] == "public class Economy {}"

    request = transport.requests[0]
    assert trace_text in request["input"]
    assert "Decoded game traces" in request["input"]
    assert RULES in request["input"]
    # System prompt is the trajectory variant.
    assert "complete decoded game traces" in request["instructions"]


def test_arm_a_prompt_has_no_traces(tmp_path):
    transport = FakeTransport([_patch_response()])
    adapter = ReflectionAdapter(
        _client(tmp_path, transport), ARMS["score_greedy"], tmp_path, rules_digest=RULES
    )
    proposal = adapter.model_call(_payload(scores=(0.0, 1.0)))
    assert proposal["action"] == "patch"

    request = transport.requests[0]
    assert "Decoded game traces" not in request["input"]
    assert "T1 AGGREGATES" not in request["input"]
    assert "Match outcome summaries" in request["input"]
    assert "replay_id=g0: score=0.0000" in request["input"]
    assert "scores only, no" in request["instructions"]


def test_no_change_and_model_failure_map_to_no_change(tmp_path):
    no_change = json.dumps(
        {
            "action": "no_change",
            "reflection": {
                "observations": [],
                "causal_hypothesis": "c",
                "general_lesson": "l",
                "evidence": [],
            },
            "mutation": None,
        }
    )
    transport = FakeTransport([no_change, "this is not json"])
    adapter = ReflectionAdapter(
        _client(tmp_path, transport), ARMS["replay_greedy"], tmp_path, rules_digest=RULES
    )
    assert adapter.model_call(_payload(traces=""))["action"] == "no_change"
    bad = adapter.model_call(_payload(traces=""))
    assert bad["action"] == "no_change"
    assert "error" in bad


def test_repair_call_flow(tmp_path):
    transport = FakeTransport(
        [
            _patch_response(source="public class Economy { broken"),
            _patch_response(source="public class Economy { /* fixed */ }"),
        ]
    )
    adapter = ReflectionAdapter(
        _client(tmp_path, transport), ARMS["replay_greedy"], tmp_path, rules_digest=RULES
    )
    adapter.model_call(_payload(traces=""))
    fixed = adapter.model_call(
        {
            "repair": True,
            "target_component": "economy",
            "compile_errors": "Economy.java:1: error: reached end of file",
            "previous_source": "public class Economy { broken",
        }
    )
    assert fixed["component_source"] == "public class Economy { /* fixed */ }"
    repair_request = transport.requests[1]
    assert "reached end of file" in repair_request["input"]
    assert "repair" in repair_request["instructions"].lower()


def test_repair_failure_returns_previous_source(tmp_path):
    transport = FakeTransport([_patch_response(), "garbage"])
    adapter = ReflectionAdapter(
        _client(tmp_path, transport), ARMS["replay_greedy"], tmp_path, rules_digest=RULES
    )
    adapter.model_call(_payload(traces=""))
    out = adapter.model_call(
        {"repair": True, "compile_errors": "e", "previous_source": "PREV"}
    )
    assert out["component_source"] == "PREV"
    assert "error" in out


def test_validate_rewrite_and_compile_check_wrapper(tmp_path):
    transport = FakeTransport([_patch_response()])
    adapter = ReflectionAdapter(
        _client(tmp_path, transport), ARMS["replay_greedy"], tmp_path,
        rules_digest=RULES, max_changed_lines=4,
    )
    adapter.model_call(_payload(traces=""))

    ok_components = dict(
        COMPONENTS,
        economy=COMPONENTS["economy"].replace("return 1;", "return 2;"),
    )
    assert adapter.validate_rewrite(ok_components) == []

    big = "public class Economy {\n" + "\n".join(f"int x{i};" for i in range(10)) + "\n}"
    violations = adapter.validate_rewrite(dict(COMPONENTS, economy=big))
    assert violations and "changed-line cap" in violations[0]

    forbidden = dict(
        COMPONENTS, economy="public class Economy { java.io.File f; }"
    )
    assert any("forbidden" in v for v in adapter.validate_rewrite(forbidden))

    check = adapter.make_compile_check(lambda c: (True, ""))
    ok, _ = check(ok_components)
    assert ok is True
    ok2, errors2 = check(dict(COMPONENTS, economy=big))
    assert ok2 is False and "static validation failed" in errors2


def test_mutation_history_reads_state_jsonl(tmp_path):
    transport = FakeTransport([_patch_response(), _patch_response()])
    adapter = ReflectionAdapter(
        _client(tmp_path, transport), ARMS["replay_greedy"], tmp_path, rules_digest=RULES
    )
    adapter.model_call(_payload(traces=""))  # iteration 0 proposes "hyp"
    (tmp_path / "state.jsonl").write_text(
        json.dumps({"event": "iteration", "iteration": 0, "result": "accepted"}) + "\n"
    )
    adapter.model_call(_payload(traces=""))
    second_request = transport.requests[1]
    assert "[economy] hyp" in second_request["input"]


def test_decode_traces_builds_labeled_packed_traces():
    records = [
        {
            "replay_path": str(FIXTURE_REPLAY),
            "opponent": "examplefuncsplayer",
            "map_name": "DefaultSmall",
            "side": "A",
            "score": 0.5,
        }
    ]
    decode_traces = make_decode_traces()
    text = decode_traces(records)
    assert "=== REPLAY g0 opponent=examplefuncsplayer map=DefaultSmall side=A" in text
    assert "T1 AGGREGATES" in text
    assert "T2 EVENTS" in text


def test_outcomes_summary_empty():
    assert outcomes_summary([]) == "(no matches available)"


def test_load_env_file_and_ensure_api_key(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nexport FOO=bar\nOPENAI_API_KEY='sk-test-123'\nEMPTY\n"
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FOO", raising=False)
    parsed = load_env_file(env)
    assert parsed["OPENAI_API_KEY"] == "sk-test-123"
    assert parsed["FOO"] == "bar"
    assert os_environ_get("OPENAI_API_KEY") == "sk-test-123"
    assert ensure_api_key(env) is True

    # Existing environment wins without override.
    monkeypatch.setenv("FOO", "orig")
    load_env_file(env)
    assert os_environ_get("FOO") == "orig"

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert ensure_api_key(tmp_path / "missing.env") is False


def os_environ_get(key):
    import os

    return os.environ.get(key)


def test_extract_interfaces_excludes_target():
    text = extract_interfaces(COMPONENTS, exclude="economy")
    assert "### combat" in text
    assert "### robotplayer" in text
    assert "### economy" not in text
    assert "public static void fight()" in text
