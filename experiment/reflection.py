"""Model-input assembly + proposal adaptation for the optimizer loop.

Bridges two fixed contracts:

* :class:`optimizer.loop.OptimizerLoop` calls ``model_call(payload)`` and
  expects ``{"action": "patch"|"no_change", "target_component": str,
  "component_source": str}`` (repair calls receive ``{"repair": True, ...}``
  and expect ``{"component_source": str}``), and ``decode_traces(records) ->
  str`` for the arms that see trajectories.
* :class:`model.client.LunaClient` takes (system, user) prompts built by
  :mod:`model.prompts` and returns a :class:`~model.client.ModelCall` whose
  ``parsed`` follows the reflect-and-patch schema.

Per arm: score-only summaries (arm A) via ``build_score_only_prompt``;
decoded, labeled, budget-packed traces (arms B/C/D) via ``decode_match`` +
``build_trace`` + ``pack_traces`` and ``build_reflection_prompt``.

The adapter also performs the static rewrite validation (changed-line cap +
forbidden APIs, PLAN.md sections 8/18) against the parent source it recorded
at proposal time; wire :meth:`ReflectionAdapter.validate_rewrite` into the
loop's ``compile_check`` so violations flow into the one repair attempt as
"compiler" output.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from harness.validate import FORBIDDEN_SUBSTRINGS, changed_lines
from model.client import LunaClient, ModelCall
from model.prompts import (
    build_reflection_prompt,
    build_score_only_prompt,
    load_rules_digest,
)
from model.repair import build_repair_call
from optimizer.loop import ArmConfig
from replay import TraceConfig, build_trace, decode_match, pack_traces
from replay.trace import degrade

__all__ = [
    "load_env_file",
    "ensure_api_key",
    "extract_interfaces",
    "make_decode_traces",
    "ReflectionAdapter",
]

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = _REPO_ROOT / ".env"


# ---------------------------------------------------------------------------
# .env loading (tiny, stdlib-only -- no python-dotenv dependency)
# ---------------------------------------------------------------------------

def load_env_file(path: Union[str, Path] = DEFAULT_ENV_PATH, override: bool = False) -> dict[str, str]:
    """Parse KEY=VALUE lines from a .env file into os.environ.

    Lines starting with '#' and blank lines are skipped; surrounding single
    or double quotes on values are stripped; existing environment variables
    win unless ``override``.  Returns the parsed mapping.
    """
    path = Path(path)
    parsed: dict[str, str] = {}
    if not path.exists():
        return parsed
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key:
            continue
        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


def ensure_api_key(env_path: Union[str, Path] = DEFAULT_ENV_PATH) -> bool:
    """Make sure OPENAI_API_KEY is in the environment, loading repo .env if
    needed.  Returns True iff the key is now set."""
    if not os.environ.get("OPENAI_API_KEY"):
        load_env_file(env_path)
    return bool(os.environ.get("OPENAI_API_KEY"))


# ---------------------------------------------------------------------------
# interface extraction (deterministic, line-based)
# ---------------------------------------------------------------------------

_SIGNATURE_MARKERS = ("public ", "protected ", "static ", "final ")


def _interface_lines(source: str) -> list[str]:
    """Declaration-level lines of a component: type declarations plus public/
    static member signatures (bodies stripped).  Deterministic and dumb on
    purpose -- context for the model, not a parser."""
    out: list[str] = []
    for raw in source.splitlines():
        line = raw.strip()
        if line.startswith(("class ", "interface ", "enum ")) or any(
            line.startswith(m) for m in _SIGNATURE_MARKERS
        ):
            sig = line.split("{", 1)[0].rstrip()
            if sig:
                out.append(sig)
    return out


def extract_interfaces(components: Mapping[str, str], exclude: str) -> str:
    """Interfaces-only view of every component except ``exclude``."""
    sections: list[str] = []
    for name in sorted(components):
        if name == exclude:
            continue
        lines = _interface_lines(components[name])
        body = "\n".join(f"  {line}" for line in lines) or "  (no public members found)"
        sections.append(f"### {name}\n{body}")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# trace assembly (arms B/C/D)
# ---------------------------------------------------------------------------

def _record_label(i: int, record: Mapping[str, Any]) -> str:
    return (
        f"=== REPLAY g{i} opponent={record.get('opponent', '?')} "
        f"map={record.get('map_name', '?')} side={record.get('side', '?')} "
        f"score={float(record.get('score', 0.0)):.4f} ==="
    )


def make_decode_traces(
    trace_cfg: Optional[TraceConfig] = None,
) -> Callable[[Sequence[Mapping]], str]:
    """Build the loop's ``decode_traces`` callable.

    Decodes each record's replay, builds the tiered trace, and packs complete
    games under the frozen token budget (degrading on the frozen ladder when
    needed).  Each included game is prefixed with a ``=== REPLAY gN ... ===``
    label so model citations (replay_id) are checkable.
    """
    cfg = trace_cfg if trace_cfg is not None else TraceConfig.from_experiment_yaml()

    def decode_traces(records: Sequence[Mapping]) -> str:
        decoded_games = [decode_match(r["replay_path"]) for r in records]
        traces = [build_trace(d, cfg) for d in decoded_games]
        _, manifest = pack_traces(
            traces,
            budget_tokens=cfg.replay_token_budget,
            max_games=cfg.max_games_per_call,
            decoded_games=decoded_games,
            base_cfg=cfg,
        )
        # Rebuild the packed text with labels, honoring the packer's
        # per-game degrade decisions (labels are ~20 tokens/game -- inside
        # the budget's noise; games stay complete, never truncated).
        parts: list[str] = []
        for entry in manifest:
            if not entry["included"]:
                continue
            i = entry["index"]
            level = entry["degrade_level"] or 0
            text = traces[i] if level == 0 else build_trace(
                decoded_games[i], degrade(cfg, level)
            )
            parts.append(_record_label(i, records[i]) + "\n" + text)
        return "\n\n".join(parts)

    return decode_traces


def outcomes_summary(scores: Sequence[float]) -> str:
    """Arm-A model evidence: per-scenario score lines, nothing else."""
    if not scores:
        return "(no matches available)"
    lines = [
        f"replay_id=g{i}: score={float(s):.4f} "
        "(1=win, 0=loss, 0.5=tie; small margin term folded in)"
        for i, s in enumerate(scores)
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the adapter
# ---------------------------------------------------------------------------

class ReflectionAdapter:
    """Stateful bridge from OptimizerLoop payloads to LunaClient calls.

    State kept between calls (all within one run process):

    * the last :class:`ModelCall`, for the one repair attempt's prompt;
    * the parent components + target of the last proposal, for
      :meth:`validate_rewrite`;
    * per-iteration mutation hypotheses, joined with the loop's
      ``state.jsonl`` acceptance records to build the "last 3 accepted
      mutations" history.
    """

    def __init__(
        self,
        client: LunaClient,
        arm: ArmConfig,
        run_dir: Union[str, Path],
        rules_digest: Optional[str] = None,
        max_changed_lines: int = 250,
        history_size: int = 3,
    ):
        self.client = client
        self.arm = arm
        self.run_dir = Path(run_dir)
        self.rules_digest = (
            rules_digest if rules_digest is not None else load_rules_digest()
        )
        self.max_changed_lines = max_changed_lines
        self.history_size = history_size
        self._last_call: Optional[ModelCall] = None
        self._parent_components: Optional[dict[str, str]] = None
        self._target_component: Optional[str] = None
        self._hypotheses: dict[int, str] = {}

    # -- mutation history -------------------------------------------------

    def _accepted_history(self) -> list[str]:
        state = self.run_dir / "state.jsonl"
        if not state.exists():
            return []
        history: list[str] = []
        for line in state.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") == "iteration" and rec.get("result") == "accepted":
                hyp = self._hypotheses.get(rec.get("iteration"))
                if hyp:
                    history.append(hyp)
        return history[-self.history_size:]

    # -- loop contract: model_call ----------------------------------------

    def model_call(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if payload.get("repair"):
            return self._repair(payload)
        return self._reflect(payload)

    def _reflect(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        comp = str(payload["target_component"])
        components = dict(payload["components"])
        self._parent_components = components
        self._target_component = comp
        history = self._accepted_history()
        other_ifaces = extract_interfaces(components, exclude=comp)

        if self.arm.use_trajectories:
            system, user = build_reflection_prompt(
                self.rules_digest,
                comp,
                components[comp],
                other_ifaces,
                history,
                str(payload.get("traces", "")),
            )
        else:
            system, user = build_score_only_prompt(
                self.rules_digest,
                comp,
                components[comp],
                other_ifaces,
                history,
                outcomes_summary(payload.get("scores", ())),
            )

        call = self.client.reflect_and_patch(system, user)
        self._last_call = call
        self._log_reflection(payload, comp, call)
        if call.parsed is None:
            return {"action": "no_change", "error": call.error or "model call failed"}
        parsed = call.parsed
        if parsed.get("action") != "patch" or not parsed.get("mutation"):
            return {"action": "no_change"}
        mutation = parsed["mutation"]
        iteration = payload.get("iteration")
        if isinstance(iteration, int):
            self._hypotheses[iteration] = (
                f"[{mutation.get('target_component', comp)}] "
                f"{mutation.get('hypothesis', '')}"
            )
        return {
            "action": "patch",
            "target_component": str(mutation["target_component"]),
            "component_source": str(mutation["component_source"]),
            "hypothesis": mutation.get("hypothesis"),
        }

    def _log_reflection(self, payload: Mapping[str, Any], comp: str, call) -> None:
        """Persist the model's parsed reflection for groundedness analysis.

        PLAN.md section 16 needs each call's claimed evidence (cited replay
        rounds) next to what was actually sent; component_source is omitted —
        accepted sources live in the candidate store.
        """
        entry: dict[str, Any] = {
            "iteration": payload.get("iteration"),
            "component": comp,
            "arm": self.arm.name,
            "used_trajectories": bool(self.arm.use_trajectories),
            "error": call.error,
        }
        if call.parsed is not None:
            entry["action"] = call.parsed.get("action")
            entry["reflection"] = call.parsed.get("reflection")
            mutation = call.parsed.get("mutation") or {}
            entry["mutation_meta"] = {
                k: mutation.get(k)
                for k in ("target_component", "hypothesis",
                          "expected_improvement", "regression_risks")
            }
        try:
            with open(self.run_dir / "reflections.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        except OSError:
            logging.getLogger(__name__).exception("failed to write reflections.jsonl")

    def _repair(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        prior = self._last_call
        if prior is None:  # merge repair or resumed state: synthesize a prior
            prior = ModelCall(
                parsed=None,
                raw_text=json.dumps(
                    {
                        "note": "no prior model output in this process",
                        "previous_source": payload.get("previous_source", ""),
                        "components": sorted(payload.get("components", ())),
                    }
                ),
                usage={"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
                model_id=self.client.model_id,
            )
        system, user = build_repair_call(prior, str(payload.get("compile_errors", "")))
        call = self.client.reflect_and_patch(system, user)
        self._last_call = call
        if call.parsed and call.parsed.get("action") == "patch" and call.parsed.get("mutation"):
            mutation = call.parsed["mutation"]
            out: dict[str, Any] = {
                "component_source": str(mutation["component_source"]),
                "target_component": str(mutation["target_component"]),
            }
            return out
        # Repair failed: return the previous source unchanged so the loop's
        # second compile_check fails and the child is rejected (fail-honest).
        return {
            "component_source": str(payload.get("previous_source", "")),
            "error": call.error or "repair call produced no patch",
        }

    # -- static validation for compile_check ------------------------------

    def validate_rewrite(self, components: Mapping[str, str]) -> list[str]:
        """Static violations of the changed component vs the recorded parent.

        Empty list when nothing is recorded (e.g. the seed compile) or the
        rewrite is clean.  Compose with the compiler's compile_check:
        violations are 'compiler' errors and feed the one repair attempt.
        """
        if self._parent_components is None:
            return []
        violations: list[str] = []
        for name in sorted(components):
            old = self._parent_components.get(name)
            new = components[name]
            if old is None or old == new:
                continue
            n = changed_lines(old, new)
            if n > self.max_changed_lines:
                violations.append(
                    f"{name}: changed-line cap exceeded: {n} changed lines > "
                    f"max {self.max_changed_lines}"
                )
            # Forbidden-API check is a RATCHET against the parent, not an
            # absolute scan: the seed bot itself calls setIndicatorString
            # (inert -- indicators are disabled at runtime), so an absolute
            # check would make those components permanently unmutatable.
            # A rewrite may keep existing references but never add new ones.
            for needle in FORBIDDEN_SUBSTRINGS:
                if new.count(needle) > old.count(needle):
                    violations.append(
                        f"{name}: forbidden reference introduced by rewrite: "
                        f"{needle!r}"
                    )
        return violations

    def make_compile_check(
        self, compiler_check: Callable[[Mapping[str, str]], tuple[bool, str]]
    ) -> Callable[[Mapping[str, str]], tuple[bool, str]]:
        """Wrap a javac compile_check with the static validator."""

        def compile_check(components: Mapping[str, str]) -> tuple[bool, str]:
            violations = self.validate_rewrite(components)
            if violations:
                return False, "static validation failed:\n" + "\n".join(violations)
            return compiler_check(components)

        return compile_check
