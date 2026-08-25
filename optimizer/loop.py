"""Optimizer iteration skeleton (PLAN.md sections 12-15).

Pure orchestration: match running, replay decoding, model calls, and compile
checks are injected callables so the whole loop is dry-runnable with fakes.

Injected callable contracts
---------------------------
run_scenario(candidate, scenario) -> mapping with at least:
    "score": float          section-11 game score for the candidate's side
    "new_exceptions": bool  candidate robots threw (DieAction EXCEPTION etc.)
    "cache_hit": bool       exact-cache hit; exempt from the match budget
    (anything else, e.g. a replay payload for the decoder, passes through)
decode_traces(records) -> str    model-facing trace text (arms B/C/D only)
model_call(payload) -> mapping   structured proposal:
    {"action": "patch"|"no_change",
     "target_component": str, "component_source": str}
    Repair calls receive {"repair": True, "compile_errors": ..., ...} and
    return {"component_source": str}.
compile_check(components) -> (ok: bool, errors: str)

Budget accounting: every model call (reflection, mutation, repair) counts
against ``model_call_budget``; every non-cache-hit run_scenario counts
against ``match_budget``.  Hard stop at the first limit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from optimizer.candidate import Candidate, CandidateStore, make_candidate
from optimizer.feedback_sampler import (
    Scenario,
    ScenarioSchedule,
    disjoint_minibatch,
    map_to_feedback_scenarios,
    select_reflection_instances,
)
from optimizer.gate import GateConfig, accept_child
from optimizer.greedy import select_parent_greedy
from optimizer.merge import attempt_merge
from optimizer.pareto import frontier_members, macro_averages, select_parent

__all__ = ["ArmConfig", "LoopConfig", "OptimizerLoop", "BudgetExhausted", "ARMS"]


@dataclass(frozen=True)
class ArmConfig:
    """Behavior switches for the four experimental arms (PLAN.md s3)."""

    name: str
    use_trajectories: bool  # model sees decoded traces vs outcomes only
    use_pareto: bool        # Pareto coverage sampling vs greedy macro-average
    use_merge: bool         # system-aware merge on the frontier


ARMS: dict[str, ArmConfig] = {
    "score_greedy": ArmConfig("score_greedy", False, False, False),
    "replay_greedy": ArmConfig("replay_greedy", True, False, False),
    "gepa_pareto": ArmConfig("gepa_pareto", True, True, False),
    "gepa_full": ArmConfig("gepa_full", True, True, True),
}


@dataclass(frozen=True)
class LoopConfig:
    iterations: int
    model_call_budget: int
    match_budget: int
    components: tuple[str, ...] = (
        "economy",
        "combat",
        "defense",
        "navigation",
        "strategy",
    )
    reflection_k: int = 2
    minibatch_n: int = 4
    gate: GateConfig = field(default_factory=GateConfig)
    seed: int = 0


class BudgetExhausted(Exception):
    def __init__(self, which: str):
        super().__init__(which)
        self.which = which


class OptimizerLoop:
    def __init__(
        self,
        arm: ArmConfig,
        cfg: LoopConfig,
        store: CandidateStore,
        seed_candidate: Candidate,
        pareto_scenarios: Sequence[Scenario],
        feedback_pool: Sequence[Scenario],
        run_scenario: Callable[[Candidate, Scenario], Mapping],
        decode_traces: Callable[[Sequence[Mapping]], str],
        model_call: Callable[[Mapping], Mapping],
        compile_check: Callable[[Mapping[str, str]], tuple[bool, str]],
        run_dir: str | Path,
    ):
        self.arm = arm
        self.cfg = cfg
        self.store = store
        self.pareto_scenarios = tuple(pareto_scenarios)
        self.feedback_pool = tuple(feedback_pool)
        self.feedback_maps = sorted({s.map_name for s in self.feedback_pool})
        self.run_scenario = run_scenario
        self.decode_traces = decode_traces
        self.model_call = model_call
        self.compile_check = compile_check
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.run_dir / "state.jsonl"

        # Paired design: streams keyed only by (seed, iteration), never arm.
        self.schedule = ScenarioSchedule(cfg.seed, pool=self.feedback_pool)

        self.model_calls_used = 0
        self.matches_run = 0
        self.cache_hits = 0
        self.scores: dict[str, tuple[float, ...]] = {}
        self.neutral_counts: dict[str, int] = {}

        seed_stored = self.store.add(seed_candidate)
        self.seed_id = seed_stored.candidate_id
        (self.run_dir / "run_meta.json").write_text(
            json.dumps(
                {
                    "arm": self.arm.name,
                    "seed": self.cfg.seed,
                    "seed_candidate": self.seed_id,
                    "components": list(self.cfg.components),
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    # -- budgets ----------------------------------------------------------
    def _charge_model_call(self) -> None:
        if self.model_calls_used >= self.cfg.model_call_budget:
            raise BudgetExhausted("model_calls")
        self.model_calls_used += 1

    def _play(self, candidate: Candidate, scenario: Scenario) -> Mapping:
        record = self.run_scenario(candidate, scenario)
        if record.get("cache_hit", False):
            self.cache_hits += 1
        else:
            if self.matches_run >= self.cfg.match_budget:
                raise BudgetExhausted("matches")
            self.matches_run += 1
        return record

    # -- state persistence ------------------------------------------------
    def _log(self, record: Mapping) -> None:
        with open(self.state_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    def _log_progress(self, next_iteration: int) -> None:
        """Small atomic checkpoint so a killed run can resume exactly.

        Everything else (pool scores, candidates, gate history) reconstructs
        from state.jsonl + the match cache + the candidate store; the budget
        counters and iteration cursor are the only state not derivable there.
        """
        tmp = self.run_dir / "progress.json.tmp"
        tmp.write_text(
            json.dumps(
                {
                    "next_iteration": next_iteration,
                    "model_calls_used": self.model_calls_used,
                    "matches_run": self.matches_run,
                    "cache_hits": self.cache_hits,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        tmp.replace(self.run_dir / "progress.json")

    def restore(
        self,
        *,
        model_calls_used: int,
        matches_run: int,
        cache_hits: int,
        scores: Mapping[str, Sequence[float]],
        neutral_counts: Mapping[str, int],
    ) -> None:
        """Adopt reconstructed state before run(start_iteration=...)."""
        self.model_calls_used = int(model_calls_used)
        self.matches_run = int(matches_run)
        self.cache_hits = int(cache_hits)
        self.scores = {k: tuple(float(x) for x in v) for k, v in scores.items()}
        self.neutral_counts = dict(neutral_counts)

    # -- evaluation -------------------------------------------------------
    def _evaluate_on_instances(self, candidate: Candidate) -> tuple[float, ...]:
        if candidate.candidate_id in self.scores:
            return self.scores[candidate.candidate_id]
        vec = tuple(
            float(self._play(candidate, s)["score"]) for s in self.pareto_scenarios
        )
        self.scores[candidate.candidate_id] = vec
        return vec

    def _pool_best(self) -> list[float]:
        n = len(self.pareto_scenarios)
        return [max(vec[i] for vec in self.scores.values()) for i in range(n)]

    # -- iteration --------------------------------------------------------
    def _select_parent(self, iteration: int) -> str:
        if self.arm.use_pareto:
            rng = self.schedule.rng_for(iteration, "parent_select")
            return select_parent(self.scores, rng)
        return select_parent_greedy(self.scores)

    def _iterate(self, iteration: int) -> dict:
        comp = self.cfg.components[iteration % len(self.cfg.components)]
        parent_id = self._select_parent(iteration)
        parent = self.store.get(parent_id)
        record: dict = {
            "event": "iteration",
            "iteration": iteration,
            "arm": self.arm.name,
            "component": comp,
            "parent": parent_id,
        }

        # 1. Reflection scenarios: weakest Pareto instances -> feedback maps.
        instances = select_reflection_instances(
            self.scores[parent_id], self._pool_best(), self.cfg.reflection_k
        )
        refl_rng = self.schedule.rng_for(iteration, "reflection")
        refl_scenarios = map_to_feedback_scenarios(
            instances, self.pareto_scenarios, self.feedback_maps, refl_rng
        )
        record["reflection_instances"] = list(instances)
        record["reflection_scenarios"] = [s.key() for s in refl_scenarios]

        parent_refl_records = [self._play(parent, s) for s in refl_scenarios]

        # 2. Model proposal (trajectories only for arms that see them).
        payload: dict = {
            "iteration": iteration,
            "target_component": comp,
            "parent_id": parent_id,
            "components": dict(parent.components),
            "scores": [float(r["score"]) for r in parent_refl_records],
        }
        if self.arm.use_trajectories:
            payload["traces"] = self.decode_traces(parent_refl_records)
        self._charge_model_call()
        proposal = self.model_call(payload)
        record["action"] = proposal.get("action", "no_change")

        if proposal.get("action") != "patch":
            record["result"] = "no_change"
            return record
        if proposal.get("target_component") != comp:
            record["result"] = "rejected_component_mismatch"
            return record

        # 3. Build + compile the child (one repair attempt on failure).
        new_components = dict(parent.components)
        new_components[comp] = str(proposal["component_source"])
        ok, errors = self.compile_check(new_components)
        record["compiled_first_try"] = ok
        if not ok:
            self._charge_model_call()
            repair = self.model_call(
                {
                    "repair": True,
                    "target_component": comp,
                    "compile_errors": errors,
                    "previous_source": new_components[comp],
                }
            )
            new_components[comp] = str(repair["component_source"])
            ok, errors = self.compile_check(new_components)
            if not ok:
                record["result"] = "rejected_compile"
                return record

        child = make_candidate(
            new_components,
            parents=(parent_id,),
            generation=parent.generation + 1,
            changed_component=comp,
            proposal_id=f"{self.arm.name}:i{iteration}",
        )
        record["child"] = child.candidate_id

        # 4. Acceptance gate on reflection + disjoint minibatch scenarios.
        gate_extra = disjoint_minibatch(
            self.schedule, iteration, exclude=refl_scenarios, n=self.cfg.minibatch_n
        )
        gate_scenarios = list(refl_scenarios) + list(gate_extra)
        parent_gate_records = list(parent_refl_records) + [
            self._play(parent, s) for s in gate_extra
        ]
        child_gate_records = [self._play(child, s) for s in gate_scenarios]

        child_new_exceptions = any(
            bool(c.get("new_exceptions", False))
            and not bool(p.get("new_exceptions", False))
            for p, c in zip(parent_gate_records, child_gate_records)
        )
        decision = accept_child(
            parent_scores=[float(r["score"]) for r in parent_gate_records],
            child_scores=[float(r["score"]) for r in child_gate_records],
            sources_differ=child.candidate_id != parent_id,
            consecutive_neutral=self.neutral_counts.get(parent_id, 0),
            child_new_exceptions=child_new_exceptions,
            cfg=self.cfg.gate,
        )
        record["gate"] = {
            "accepted": decision.accepted,
            "reason": decision.reason,
            "neutral": decision.neutral,
        }

        if not decision.accepted:
            record["result"] = f"rejected_gate:{decision.reason}"
            return record

        self.store.add(child)
        self.neutral_counts[child.candidate_id] = (
            self.neutral_counts.get(parent_id, 0) + 1 if decision.neutral else 0
        )
        self._evaluate_on_instances(child)
        record["result"] = "accepted"

        # 5. Merge (arm D): combine complementary frontier lineages.
        if self.arm.use_merge:
            record["merge"] = self._attempt_merge_step()
        return record

    def _attempt_merge_step(self) -> dict:
        frontier = frontier_members(self.scores)
        merged = attempt_merge(self.store, frontier, self.scores)
        if merged is None:
            return {"attempted": True, "merged": None}
        ok, errors = self.compile_check(dict(merged.components))
        if not ok:
            # Merged children get the same one compile-repair budget.
            try:
                self._charge_model_call()
            except BudgetExhausted:
                return {"attempted": True, "merged": None, "reason": "budget"}
            repair = self.model_call(
                {
                    "repair": True,
                    "merge": True,
                    "compile_errors": errors,
                    "components": dict(merged.components),
                }
            )
            fixed = dict(merged.components)
            comp = repair.get("target_component")
            if comp in fixed and "component_source" in repair:
                fixed[comp] = str(repair["component_source"])
            ok, errors = self.compile_check(fixed)
            if not ok:
                return {
                    "attempted": True,
                    "merged": merged.candidate_id,
                    "reason": "compile_failed",
                }
            merged = self.store.add(
                make_candidate(
                    fixed,
                    parents=merged.parents,
                    generation=merged.generation,
                    proposal_id=merged.proposal_id,
                    meta=dict(merged.meta),
                )
            )
        self._evaluate_on_instances(merged)
        return {"attempted": True, "merged": merged.candidate_id}

    # -- entry point ------------------------------------------------------
    def run(self, start_iteration: int = 0) -> dict:
        """Run up to cfg.iterations, hard-stopping at the first budget limit.

        start_iteration > 0 continues a reconstructed run (see restore());
        the seed re-evaluation is a no-op when its scores were restored.
        Returns a summary dict; per-iteration records land in state.jsonl.
        """
        stopped: Optional[str] = None
        completed = 0
        try:
            self._evaluate_on_instances(self.store.get(self.seed_id))
            for i in range(start_iteration, self.cfg.iterations):
                rec = self._iterate(i)
                self._log(rec)
                completed += 1
                self._log_progress(next_iteration=i + 1)
        except BudgetExhausted as exc:
            stopped = exc.which
            self._log({"event": "budget_exhausted", "which": exc.which})

        avgs = macro_averages(self.scores) if self.scores else {}
        best = (
            min(avgs, key=lambda cid: (-avgs[cid], cid)) if avgs else self.seed_id
        )
        summary = {
            "event": "summary",
            "arm": self.arm.name,
            "iterations_completed": completed,
            "model_calls_used": self.model_calls_used,
            "matches_run": self.matches_run,
            "cache_hits": self.cache_hits,
            "pool_size": len(self.scores),
            "best_candidate": best,
            "best_macro_average": avgs.get(best),
            "stopped": stopped,
        }
        self._log(summary)
        return summary
