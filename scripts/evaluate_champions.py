"""Held-out evaluation of run champions (PLAN.md sections 17, 32).

For every finished run under --run-root, load its champion (summary.json
best_candidate) and play the three held-out quadrants:

  A. map generalization      dev opponents  x test maps
  B. opponent generalization test opponents x Pareto maps
  C. joint (PRIMARY)         test opponents x test maps

plus the seed bot once as a baseline.  Games go through the exact match
cache, so reruns are free and identical champions collapse.  The optimizer
never sees these numbers; run this only after a run has its summary.

Usage:
    python scripts/evaluate_champions.py [--run-root runs/main]
        [--only <run_name>] [--limit N] [--workers 4]

Output: runs/heldout/<run_name>.json per run + runs/heldout/summary.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from evaluation.scoring import game_outcome  # noqa: E402
from experiment.direct_runner import DirectRunner  # noqa: E402
from experiment.candidates import CandidateCompiler  # noqa: E402
from experiment.scenarios import ScenarioExecutor  # noqa: E402
from harness.cache import MatchCache  # noqa: E402
from harness.runner import EngineConfig  # noqa: E402
from opponents.lockfile import load_lockfile  # noqa: E402
from optimizer.candidate import CandidateStore  # noqa: E402
from optimizer.feedback_sampler import Scenario  # noqa: E402

log = logging.getLogger("evaluate_champions")

MAPS_LOCK = REPO_ROOT / "configs" / "maps.lock.json"
OPPONENTS_LOCK = REPO_ROOT / "configs" / "opponents.lock.json"
HELDOUT_DIR = REPO_ROOT / "runs" / "heldout"
SEED_BASELINE_KEY = "__seed__"


def build_quadrants() -> dict[str, list[Scenario]]:
    maps = json.loads(MAPS_LOCK.read_text())
    test_maps = maps["splits"]["test"]
    pareto_maps = maps["pareto_maps"]
    opps = [o for o in load_lockfile(OPPONENTS_LOCK) if o.compiled and o.smoke_ok]
    dev = sorted(o.id for o in opps if o.split == "dev")
    test = sorted(o.id for o in opps if o.split == "test")
    if not test:
        raise RuntimeError("no usable test-split opponents in the lockfile")

    def grid(opponents, map_names):
        return [
            Scenario(opponent=o, map_name=m, side=s)
            for o in opponents for m in map_names for s in ("A", "B")
        ]

    return {
        "map_generalization": grid(dev, test_maps),
        "opponent_generalization": grid(test, pareto_maps),
        "joint": grid(test, test_maps),
    }


def evaluate_candidate(
    executor: ScenarioExecutor,
    candidate,
    quadrants: dict[str, list[Scenario]],
    limit: int | None,
    workers: int,
) -> dict:
    out: dict = {"quadrants": {}, "games": 0}
    for qname, scenarios in quadrants.items():
        todo = scenarios[:limit] if limit else scenarios
        def play(s):
            r = executor.run_scenario(candidate, s)
            return {
                "opponent": s.opponent, "map": s.map_name, "side": s.side,
                "outcome": game_outcome(r["win_type"], r["winner"], s.side),
                "win_type": r["win_type"], "cache_hit": bool(r.get("cache_hit")),
            }
        with ThreadPoolExecutor(max_workers=workers) as pool:
            records = list(pool.map(play, todo))
        mean = sum(r["outcome"] for r in records) / len(records)
        out["quadrants"][qname] = {
            "mean_outcome": mean, "n_games": len(records), "games": records,
        }
        out["games"] += len(records)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-root", default="runs/main")
    ap.add_argument("--only", default=None, help="evaluate a single run dir name")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap games per quadrant (smoke testing only)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--skip-seed", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    engine = EngineConfig.from_lock()
    runner = DirectRunner(engine=engine)
    compiler = CandidateCompiler(HELDOUT_DIR / "candidates", engine=engine)
    executor = ScenarioExecutor(
        cache=MatchCache(REPO_ROOT / "runs" / "match_cache"),
        runner=runner,
        opponents={o.id: o for o in load_lockfile(OPPONENTS_LOCK)},
        compiler=compiler,
        engine=engine,
    )
    quadrants = build_quadrants()
    HELDOUT_DIR.mkdir(parents=True, exist_ok=True)

    run_root = Path(args.run_root)
    results: dict[str, dict] = {}
    seed_done = args.skip_seed
    for run_dir in sorted(run_root.iterdir()):
        if args.only and run_dir.name != args.only:
            continue
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            log.info("skipping %s (no summary yet)", run_dir.name)
            continue
        summary = json.loads(summary_path.read_text())
        store = CandidateStore(run_dir / "candidates_store")
        champion = store.get(summary["best_candidate"], None)
        if champion is None:
            log.error("%s: champion %s missing from store", run_dir.name,
                      summary["best_candidate"])
            continue
        if not seed_done:
            seed = store.get(json.loads((run_dir / "run_meta.json").read_text())
                             ["seed_candidate"], None)
            if seed is not None:
                log.info("evaluating seed baseline")
                results[SEED_BASELINE_KEY] = evaluate_candidate(
                    executor, seed, quadrants, args.limit, args.workers)
                seed_done = True
        log.info("evaluating %s champion %s", run_dir.name,
                 champion.candidate_id[:12])
        res = evaluate_candidate(executor, champion, quadrants,
                                 args.limit, args.workers)
        res["champion"] = champion.candidate_id
        res["train_macro_average"] = summary.get("best_macro_average")
        results[run_dir.name] = res
        (HELDOUT_DIR / f"{run_dir.name}.json").write_text(
            json.dumps(res, indent=1, sort_keys=True), encoding="utf-8")
        log.info("%s: joint=%.3f map_gen=%.3f opp_gen=%.3f (%d games)",
                 run_dir.name,
                 res["quadrants"]["joint"]["mean_outcome"],
                 res["quadrants"]["map_generalization"]["mean_outcome"],
                 res["quadrants"]["opponent_generalization"]["mean_outcome"],
                 res["games"])

    (HELDOUT_DIR / "summary.json").write_text(
        json.dumps(
            {k: {"joint": v["quadrants"]["joint"]["mean_outcome"],
                 "map_generalization":
                     v["quadrants"]["map_generalization"]["mean_outcome"],
                 "opponent_generalization":
                     v["quadrants"]["opponent_generalization"]["mean_outcome"],
                 "train_macro_average": v.get("train_macro_average")}
             for k, v in results.items()},
            indent=1, sort_keys=True),
        encoding="utf-8")
    log.info("wrote %s (%d entries)", HELDOUT_DIR / "summary.json", len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
