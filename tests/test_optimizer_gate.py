"""Tests for optimizer/gate.py: every branch of the acceptance gate."""
import pytest

from optimizer.gate import GateConfig, accept_child

CFG = GateConfig(max_consecutive_neutral_accepts=2, reject_new_exceptions=True)


def gate(parent, child, sources_differ=True, consecutive_neutral=0,
         child_new_exceptions=False, cfg=CFG):
    return accept_child(
        parent_scores=parent,
        child_scores=child,
        sources_differ=sources_differ,
        consecutive_neutral=consecutive_neutral,
        child_new_exceptions=child_new_exceptions,
        cfg=cfg,
    )


class TestExceptions:
    def test_new_exceptions_reject_even_when_better(self):
        d = gate([0.0, 0.0], [1.0, 1.0], child_new_exceptions=True)
        assert not d.accepted
        assert d.reason == "new_exceptions"
        assert not d.neutral

    def test_exception_check_can_be_disabled(self):
        cfg = GateConfig(max_consecutive_neutral_accepts=2, reject_new_exceptions=False)
        d = gate([0.0], [1.0], child_new_exceptions=True, cfg=cfg)
        assert d.accepted
        assert d.reason == "mean_improvement"


class TestMeanComparison:
    def test_strict_improvement_accepts(self):
        d = gate([0.5, 0.5, 0.5], [0.5, 0.5, 0.6])
        assert d.accepted and d.reason == "mean_improvement" and not d.neutral

    def test_tiny_margin_improvement_accepts(self):
        # The lambda-margin term makes hair-thin improvements meaningful.
        d = gate([1.0, 0.02], [1.0, 0.04])
        assert d.accepted

    def test_regression_rejects(self):
        d = gate([0.6, 0.6], [0.6, 0.5])
        assert not d.accepted and d.reason == "mean_regression"

    def test_mismatched_lengths_rejected(self):
        with pytest.raises(ValueError):
            gate([0.5], [0.5, 0.5])
        with pytest.raises(ValueError):
            gate([], [])


class TestNeutralDrift:
    def test_tie_with_changed_source_accepts_neutral(self):
        d = gate([0.5, 0.7], [0.7, 0.5], sources_differ=True, consecutive_neutral=0)
        assert d.accepted and d.neutral and d.reason == "neutral_drift"

    def test_tie_under_cap_still_accepts(self):
        d = gate([0.5], [0.5], consecutive_neutral=1)
        assert d.accepted and d.neutral

    def test_tie_at_cap_rejects(self):
        d = gate([0.5], [0.5], consecutive_neutral=2)
        assert not d.accepted and d.neutral and d.reason == "neutral_cap_reached"

    def test_tie_identical_sources_rejects(self):
        d = gate([0.5], [0.5], sources_differ=False, consecutive_neutral=0)
        assert not d.accepted and d.reason == "tie_identical_sources"

    def test_cap_zero_never_neutral_accepts(self):
        cfg = GateConfig(max_consecutive_neutral_accepts=0)
        d = gate([0.5], [0.5], consecutive_neutral=0, cfg=cfg)
        assert not d.accepted


class TestExperimentYamlCfg:
    def test_cfg_from_frozen_knobs(self):
        from pathlib import Path

        import yaml

        cfg_path = Path(__file__).resolve().parent.parent / "configs" / "experiment.yaml"
        raw = yaml.safe_load(cfg_path.read_text())["gate"]
        cfg = GateConfig(
            max_consecutive_neutral_accepts=raw["max_consecutive_neutral_accepts"],
            reject_new_exceptions=raw["reject_new_exceptions"],
        )
        assert cfg.max_consecutive_neutral_accepts == 2
        assert cfg.reject_new_exceptions is True
        # Third consecutive neutral accept must be refused.
        assert gate([0.5], [0.5], consecutive_neutral=2, cfg=cfg).accepted is False
        assert gate([0.5], [0.5], consecutive_neutral=1, cfg=cfg).accepted is True
