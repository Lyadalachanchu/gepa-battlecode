"""Experiment driver: wires the optimizer loop to the real engine, replay
pipeline, and model client (PLAN.md section 20, build-order step 8)."""

from .direct_runner import DirectRunner, HeadlessRuntime, resolve_headless_runtime
from .candidates import (
    CandidateCompileError,
    CandidateCompiler,
    CompiledCandidate,
    materialize_candidate,
)
from .scenarios import ScenarioError, ScenarioExecutor, load_opponents
from .reflection import ReflectionAdapter, ensure_api_key, make_decode_traces

__all__ = [
    "DirectRunner",
    "HeadlessRuntime",
    "resolve_headless_runtime",
    "CandidateCompiler",
    "CandidateCompileError",
    "CompiledCandidate",
    "materialize_candidate",
    "ScenarioExecutor",
    "ScenarioError",
    "load_opponents",
    "ReflectionAdapter",
    "ensure_api_key",
    "make_decode_traces",
]
