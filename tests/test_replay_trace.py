"""Tests for replay.trace / replay.tokens against the smoke fixture."""
from pathlib import Path

import pytest

from replay import (
    TraceConfig,
    build_trace,
    count_tokens,
    decode_match,
    degrade,
    pack_traces,
)

FIXTURE = Path(__file__).parent / "fixtures" / "smoke.bc26"
REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def decoded():
    return decode_match(FIXTURE)


@pytest.fixture(scope="module")
def cfg():
    return TraceConfig.from_experiment_yaml(
        REPO_ROOT / "configs" / "experiment.yaml"
    )


def test_config_from_experiment_yaml(cfg):
    assert cfg.snapshot_stride_rounds == 20
    assert cfg.event_window_rounds == 10
    assert cfg.final_window_rounds == 50
    assert cfg.replay_token_budget == 250_000
    assert cfg.max_games_per_call == 4


def test_build_trace_nonempty_and_structured(decoded, cfg):
    t = build_trace(decoded, cfg)
    assert isinstance(t, str) and len(t) > 0
    lines = t.split("\n")
    assert lines[0].startswith("GAME map=")
    assert any(l.startswith("TEAM A=") for l in lines)
    assert any(l.startswith("RESULT winner=") for l in lines)
    assert "T1 AGGREGATES" in lines
    assert "T2 EVENTS" in lines
    assert "T3 SNAPSHOTS" in lines
    # T3 exists: the final window alone guarantees snapshots
    assert any(" SNAP " in l or " DSNP " in l for l in lines)


def test_build_trace_deterministic(decoded, cfg):
    assert build_trace(decoded, cfg) == build_trace(decoded, cfg)


def test_token_counts_positive_and_ladder_monotone(decoded, cfg):
    tokens = []
    for level in range(0, 4):
        t = build_trace(decoded, degrade(cfg, level))
        n = count_tokens(t)
        assert n > 0
        tokens.append(n)
    # degrade ladder never increases token counts
    for a, b in zip(tokens, tokens[1:]):
        assert b <= a
    # and actually shrinks somewhere along the ladder
    assert tokens[-1] < tokens[0]


def test_degrade_levels(cfg):
    d0 = degrade(cfg, 0)
    assert d0 == cfg
    d1 = degrade(cfg, 1)  # stride40
    assert d1.snapshot_stride_rounds == 40
    d2 = degrade(cfg, 2)  # windows_only
    assert d2.snapshot_stride_rounds == 0
    assert d2.event_window_rounds == cfg.event_window_rounds
    d3 = degrade(cfg, 3)  # shrink_window
    assert d3.snapshot_stride_rounds == 0
    assert d3.event_window_rounds < cfg.event_window_rounds
    assert d3.final_window_rounds < cfg.final_window_rounds


def test_pack_respects_budget(decoded, cfg):
    trace = build_trace(decoded, cfg)
    one = count_tokens(trace)
    budget = int(one * 2.5)  # fits 2 whole traces, not 3
    packed, manifest = pack_traces(
        [trace, trace, trace], budget_tokens=budget, max_games=4
    )
    assert count_tokens(packed) <= budget
    included = [m for m in manifest if m["included"]]
    assert len(included) == 2
    # complete traces only -- packed is whole traces joined
    assert packed.count("GAME map=") == 2


def test_pack_respects_max_games(decoded, cfg):
    trace = build_trace(decoded, cfg)
    packed, manifest = pack_traces(
        [trace, trace, trace], budget_tokens=10_000_000, max_games=2
    )
    assert packed.count("GAME map=") == 2
    assert [m["included"] for m in manifest] == [True, True, False]
    assert manifest[2]["reason"] == "max_games"


def test_pack_degrades_oversized_trace(decoded, cfg):
    full = build_trace(decoded, cfg)
    full_tokens = count_tokens(full)
    # pick a budget below the full trace but above the fully-degraded one
    floor = count_tokens(build_trace(decoded, degrade(cfg, 3)))
    assert floor < full_tokens
    budget = (floor + full_tokens) // 2
    packed, manifest = pack_traces(
        [full],
        budget_tokens=budget,
        max_games=1,
        decoded_games=[decoded],
        base_cfg=cfg,
    )
    assert manifest[0]["included"]
    assert manifest[0]["degrade_level"] >= 1
    assert count_tokens(packed) <= budget


def test_pack_gives_up_without_decoded_game(decoded, cfg):
    full = build_trace(decoded, cfg)
    budget = count_tokens(full) // 2
    packed, manifest = pack_traces([full], budget_tokens=budget, max_games=1)
    assert packed == ""
    assert manifest[0]["included"] is False
    assert manifest[0]["reason"] == "over_budget"


def test_count_tokens_basic():
    assert count_tokens("") == 0
    n = count_tokens("hello world, this is a trace line\n" * 10)
    assert n > 0
    # deterministic
    assert n == count_tokens("hello world, this is a trace line\n" * 10)
