#!/usr/bin/env python3
"""Pilot experiment driver: wire the OptimizerLoop to the real stack.

Usage:
    python scripts/run_pilot.py --arm gepa_pareto --optimizer-seed 0
    python scripts/run_pilot.py --arm score_greedy --optimizer-seed 1 \
        --calls 20 --matches 750
    python scripts/run_pilot.py --arm replay_greedy --optimizer-seed 0 \
        --resume runs/pilot/replay_greedy-s0

Budgets default to the configs/experiment.yaml ``budgets.pilot`` section.
State lands in runs/pilot/<arm>-s<seed>/ (state.jsonl written by the loop,
run.log by this script, model_calls.jsonl by the model client).  Matches go
through the shared exact cache, so a resumed or re-paired run replays cached
cells for free; model calls are NOT resumable (the loop is restarted from
iteration 0 and re-spends its call budget -- logged loudly).

Everything is dependency-injected through :func:`build_wiring` /
:func:`build_loop` so tests can dry-wire the whole driver with fakes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from evaluation.scoring import DEFAULT_MARGIN_LAMBDA  # noqa: E402
from harness.cache import MatchCache  # noqa: E402
from harness.runner import EngineConfig  # noqa: E402
from model.client import LunaClient  # noqa: E402
from optimizer.candidate import Candidate, CandidateStore, make_candidate  # noqa: E402
from optimizer.feedback_sampler import Scenario  # noqa: E402
from optimizer.gate import GateConfig  # noqa: E402
from optimizer.loop import ARMS, LoopConfig, OptimizerLoop  # noqa: E402

from experiment.candidates import CandidateCompiler, GLUE_COMPONENT  # noqa: E402
from experiment.direct_runner import DirectRunner  # noqa: E402
from experiment.reflection import (  # noqa: E402
    ReflectionAdapter,
    ensure_api_key,
    make_decode_traces,
)
from experiment.scenarios import ScenarioExecutor, load_opponents  # noqa: E402

EXPERIMENT_YAML = REPO_ROOT / "configs" / "experiment.yaml"
MAPS_LOCK = REPO_ROOT / "configs" / "maps.lock.json"
OPPONENTS_LOCK = REPO_ROOT / "configs" / "opponents.lock.json"
MODULAR_SEED_DIR = REPO_ROOT / "bots" / "modular_seed"
ORIGINAL_SEED_FILE = REPO_ROOT / "bots" / "original_seed" / "lectureplayer" / "RobotPlayer.java"
DEFAULT_CACHE_ROOT = REPO_ROOT / "runs" / "match_cache"
PILOT_ROOT = REPO_ROOT / "runs" / "pilot"

log = logging.getLogger("run_pilot")


# ---------------------------------------------------------------------------
# config + inputs
# ---------------------------------------------------------------------------

def load_experiment_config(path: Path = EXPERIMENT_YAML) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_seed_candidate() -> tuple[Candidate, tuple[str, ...], bool]:
    """(seed candidate, mutable components tuple, is_modular).

    Prefers bots/modular_seed (component per file, keyed by lowercase stem;
    ``robotplayer`` is glue and never mutable).  Falls back to
    bots/original_seed as a single mutable component -- flagged loudly by the
    caller, scaffolding only.
    """
    java_files = sorted(MODULAR_SEED_DIR.glob("*.java")) if MODULAR_SEED_DIR.is_dir() else []
    if java_files:
        components = {
            p.stem.lower(): p.read_text(encoding="utf-8") for p in java_files
        }
        # Round-robin order is frozen by experiment.yaml (PLAN.md section 14:
        # economy -> combat -> defense -> navigation -> strategy), identical
        # across arms for the paired design.  Fall back to sorted order only
        # if the seed's components disagree with the config.
        configured = tuple(load_experiment_config().get("components", ()))
        mutable_set = {n for n in components if n != GLUE_COMPONENT}
        if set(configured) == mutable_set:
            mutable = configured
        else:
            mutable = tuple(sorted(mutable_set))
        cand = make_candidate(components, proposal_id="seed:modular")
        return cand, mutable, True
    if not ORIGINAL_SEED_FILE.exists():
        raise FileNotFoundError(
            f"no seed bot: neither {MODULAR_SEED_DIR} nor {ORIGINAL_SEED_FILE} exists"
        )
    components = {GLUE_COMPONENT: ORIGINAL_SEED_FILE.read_text(encoding="utf-8")}
    cand = make_candidate(components, proposal_id="seed:original_fallback")
    return cand, (GLUE_COMPONENT,), False


def build_scenario_pools(
    opponents: Mapping[str, Any],
    maps_lock_path: Path = MAPS_LOCK,
) -> tuple[list[Scenario], list[Scenario]]:
    """(pareto_scenarios, feedback_pool) from the frozen splits.

    Pareto instances: dev opponents x the 4 pinned Pareto maps x 2 sides
    (48 with the full dev pool).  Feedback pool: dev opponents x feedback
    maps x 2 sides.
    """
    maps_lock = json.loads(Path(maps_lock_path).read_text(encoding="utf-8"))
    pareto_maps = list(maps_lock["pareto_maps"])
    feedback_maps = list(maps_lock["splits"]["feedback"])
    dev_ids = sorted(
        oid for oid, e in opponents.items()
        if e.split == "dev" and e.compiled and e.smoke_ok
    )
    if not dev_ids:
        raise RuntimeError("no usable dev opponents in the lockfile")
    pareto = [
        Scenario(o, m, s) for o in dev_ids for m in pareto_maps for s in ("A", "B")
    ]
    feedback = [
        Scenario(o, m, s) for o in dev_ids for m in feedback_maps for s in ("A", "B")
    ]
    return pareto, feedback


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------

@dataclass
class Wiring:
    """The four injected callables the OptimizerLoop needs."""

    run_scenario: Callable[[Candidate, Scenario], Mapping]
    decode_traces: Callable[[Sequence[Mapping]], str]
    model_call: Callable[[Mapping], Mapping]
    compile_check: Callable[[Mapping[str, str]], tuple[bool, str]]


def build_wiring(
    arm_name: str,
    run_dir: Path,
    use_gradle: bool = False,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    opponents_lock: Path = OPPONENTS_LOCK,
    margin_lambda: float = DEFAULT_MARGIN_LAMBDA,
    match_timeout_s: int = 2700,
) -> Wiring:
    """Real wiring: DirectRunner + MatchCache + javac + LunaClient."""
    arm = ARMS[arm_name]
    engine = EngineConfig.from_lock()
    runner = DirectRunner(engine=engine, use_gradle=use_gradle)
    compiler = CandidateCompiler(run_dir / "candidates", engine=engine)
    executor = ScenarioExecutor(
        cache=MatchCache(cache_root),
        runner=runner,
        opponents=load_opponents(opponents_lock),
        compiler=compiler,
        engine=engine,
        margin_lambda=margin_lambda,
        match_timeout_s=match_timeout_s,
    )
    if not ensure_api_key():
        raise RuntimeError(
            "OPENAI_API_KEY is not set (env or repo .env) -- refusing to start "
            "a run that would fail at the first model call"
        )
    client = LunaClient.from_lock(call_log_path=run_dir / "model_calls.jsonl")
    adapter = ReflectionAdapter(client=client, arm=arm, run_dir=run_dir)
    return Wiring(
        run_scenario=executor.run_scenario,
        decode_traces=make_decode_traces(),
        model_call=adapter.model_call,
        compile_check=adapter.make_compile_check(compiler.compile_check),
    )


def build_loop(
    arm_name: str,
    optimizer_seed: int,
    calls: int,
    matches: int,
    run_dir: Path,
    wiring: Wiring,
    seed_candidate: Optional[Candidate] = None,
    components: Optional[tuple[str, ...]] = None,
    pareto_scenarios: Optional[Sequence[Scenario]] = None,
    feedback_pool: Optional[Sequence[Scenario]] = None,
    gate: Optional[GateConfig] = None,
    iterations: Optional[int] = None,
) -> OptimizerLoop:
    """Assemble the OptimizerLoop.  Every input is injectable for tests."""
    arm = ARMS[arm_name]
    if seed_candidate is None or components is None:
        loaded, mutable, is_modular = load_seed_candidate()
        seed_candidate = seed_candidate if seed_candidate is not None else loaded
        components = components if components is not None else mutable
        if not is_modular:
            log.warning(
                "FALLBACK SEED: bots/modular_seed is missing; using "
                "bots/original_seed as a SINGLE mutable component %r. This "
                "violates the robotplayer-is-glue convention and is "
                "scaffolding only -- do not report results from this run.",
                components,
            )
    if pareto_scenarios is None or feedback_pool is None:
        opponents = load_opponents(OPPONENTS_LOCK)
        p, f = build_scenario_pools(opponents)
        pareto_scenarios = pareto_scenarios if pareto_scenarios is not None else p
        feedback_pool = feedback_pool if feedback_pool is not None else f

    cfg = LoopConfig(
        iterations=iterations if iterations is not None else calls,
        model_call_budget=calls,
        match_budget=matches,
        components=tuple(components),
        gate=gate if gate is not None else GateConfig(),
        seed=optimizer_seed,
    )
    store = CandidateStore(run_dir / "candidates_store")
    return OptimizerLoop(
        arm=arm,
        cfg=cfg,
        store=store,
        seed_candidate=seed_candidate,
        pareto_scenarios=pareto_scenarios,
        feedback_pool=feedback_pool,
        run_scenario=wiring.run_scenario,
        decode_traces=wiring.decode_traces,
        model_call=wiring.model_call,
        compile_check=wiring.compile_check,
        run_dir=run_dir,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _setup_logging(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


# Fallback match-budget estimate for resumed runs that predate progress.json
# (pilot measured ~28 non-cached matches per model call including Pareto evals).
MATCHES_PER_CALL_ESTIMATE = 28


def reconstruct_resume_state(run_dir: Path, loop: OptimizerLoop) -> int:
    """Rebuild loop state from state.jsonl + candidate store + match cache.

    Returns the iteration to continue from.  Pool vectors replay through
    run_scenario, which hits the exact match cache (a missing game re-runs,
    uncharged).  Budgets come from progress.json when present, else from the
    model-call log plus a conservative match estimate.
    """
    state_path = run_dir / "state.jsonl"
    next_iter = 0
    accepted: list[tuple[str, str, bool]] = []
    if state_path.exists():
        with open(state_path, encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("event") == "summary":
                    raise FileExistsError(
                        f"{run_dir} already completed; refusing to resume"
                    )
                if r.get("event") == "iteration":
                    next_iter = max(next_iter, int(r["iteration"]) + 1)
                    g = r.get("gate") or {}
                    if g.get("accepted"):
                        accepted.append(
                            (r["parent"], r["child"], bool(g.get("neutral")))
                        )

    calls_used = 0
    call_log = run_dir / "model_calls.jsonl"
    if call_log.exists():
        with open(call_log, encoding="utf-8") as fh:
            calls_used = sum(1 for _ in fh)
    progress = {}
    progress_path = run_dir / "progress.json"
    if progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        calls_used = max(calls_used, int(progress.get("model_calls_used", 0)))
        next_iter = max(next_iter, int(progress.get("next_iteration", 0)))
    matches_run = int(
        progress.get("matches_run", MATCHES_PER_CALL_ESTIMATE * calls_used)
    )
    cache_hits = int(progress.get("cache_hits", 0))

    neutral_counts: dict[str, int] = {}
    for parent, child, neutral in accepted:
        neutral_counts[child] = neutral_counts.get(parent, 0) + 1 if neutral else 0

    scores: dict[str, tuple[float, ...]] = {}
    for cid in [loop.seed_id] + [child for _, child, _ in accepted]:
        cand = loop.store.get(cid, None)
        if cand is None:
            log.warning("resume: candidate %s missing from store; dropped", cid)
            continue
        scores[cid] = tuple(
            float(loop.run_scenario(cand, s)["score"])
            for s in loop.pareto_scenarios
        )

    loop.restore(
        model_calls_used=calls_used,
        matches_run=matches_run,
        cache_hits=cache_hits,
        scores=scores,
        neutral_counts=neutral_counts,
    )
    log.info(
        "resume: continuing at iteration %d (pool=%d, calls_used=%d, "
        "matches_run~=%d)", next_iter, len(scores), calls_used, matches_run,
    )
    return next_iter


def _prepare_run_dir(
    arm: str, seed: int, resume: Optional[str], run_root: Optional[str] = None
) -> Path:
    if resume:
        run_dir = Path(resume)
        if not run_dir.is_dir():
            raise FileNotFoundError(f"--resume dir does not exist: {run_dir}")
        return run_dir
    root = Path(run_root) if run_root else PILOT_ROOT
    run_dir = root / f"{arm}-s{seed}"
    if (run_dir / "state.jsonl").exists():
        raise FileExistsError(
            f"{run_dir} already holds a run (state.jsonl exists). Pass "
            f"--resume {run_dir} to restart it (cached matches are free; "
            "model calls are re-spent), or choose a different seed."
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm", required=True, choices=sorted(ARMS))
    parser.add_argument("--optimizer-seed", type=int, required=True)
    parser.add_argument("--calls", type=int, default=None,
                        help="model-call budget (default: experiment.yaml pilot)")
    parser.add_argument("--matches", type=int, default=None,
                        help="match budget (default: experiment.yaml pilot)")
    parser.add_argument("--resume", default=None,
                        help="existing runs/pilot/<run_id> dir to restart")
    parser.add_argument("--run-root", default=None,
                        help="root dir for run output (default runs/pilot; "
                             "use runs/main for the main experiment)")
    parser.add_argument("--use-gradle", action="store_true",
                        help="force matches through ./gradlew instead of direct java")
    args = parser.parse_args(argv)

    exp = load_experiment_config()
    pilot = exp["budgets"]["pilot"]
    calls = args.calls if args.calls is not None else int(pilot["model_calls_per_run"])
    matches = args.matches if args.matches is not None else int(pilot["matches_per_run"])
    margin_lambda = float(exp["scoring"]["margin_lambda"])
    gate_cfg = exp.get("gate", {})
    gate = GateConfig(
        max_consecutive_neutral_accepts=int(
            gate_cfg.get("max_consecutive_neutral_accepts", 2)
        ),
        reject_new_exceptions=bool(gate_cfg.get("reject_new_exceptions", True)),
    )

    run_dir = _prepare_run_dir(args.arm, args.optimizer_seed, args.resume, args.run_root)
    _setup_logging(run_dir)
    if args.resume:
        log.warning(
            "RESUME: reconstructing %s from state.jsonl + match cache and "
            "continuing; already-spent budget is preserved.", run_dir,
        )
    log.info(
        "pilot run: arm=%s seed=%d calls=%d matches=%d run_dir=%s use_gradle=%s",
        args.arm, args.optimizer_seed, calls, matches, run_dir, args.use_gradle,
    )

    try:
        wiring = build_wiring(
            arm_name=args.arm,
            run_dir=run_dir,
            use_gradle=args.use_gradle,
            margin_lambda=margin_lambda,
        )
        loop = build_loop(
            arm_name=args.arm,
            optimizer_seed=args.optimizer_seed,
            calls=calls,
            matches=matches,
            run_dir=run_dir,
            wiring=wiring,
            gate=gate,
        )
        start_iteration = 0
        if args.resume:
            start_iteration = reconstruct_resume_state(run_dir, loop)
        start = time.monotonic()
        summary = loop.run(start_iteration=start_iteration)
        summary["wall_seconds"] = round(time.monotonic() - start, 1)
        (run_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
        )
        log.info("run complete: %s", json.dumps(summary, sort_keys=True))
        return 0
    except Exception:
        log.exception("PILOT RUN FAILED (fail-loud; see traceback above)")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
