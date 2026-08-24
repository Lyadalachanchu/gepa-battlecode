"""Match-running harness: engine invocation, exact match cache, patch validation."""

from .cache import MatchCache, MatchCacheKey, source_tree_hash
from .runner import EngineConfig, MatchRunError, MatchRunResult, run_match
from .validate import changed_lines, validate_component_rewrite

__all__ = [
    "EngineConfig",
    "MatchCache",
    "MatchCacheKey",
    "MatchRunError",
    "MatchRunResult",
    "changed_lines",
    "run_match",
    "source_tree_hash",
    "validate_component_rewrite",
]
