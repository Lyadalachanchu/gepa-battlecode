#!/usr/bin/env python3
"""Calibration round-robin: empirical strength tiers for opponents.

Every smoke-ok opponent plays a fixed ladder (examplefuncsplayer,
lectureplayer, and the strongest compiled dev bot) on a small map grid,
both sides.  Mean outcome (evaluation.scoring.game_outcome; determinism
makes repeats worthless) ranks the bots; rank quartiles become
floor/weak/mid/strong tiers written back into configs/opponents.lock.json.

Matches go through the shared exact MatchCache, so re-runs are free.

Usage:
    python scripts/calibrate_opponents.py                    # full calibration
    python scripts/calibrate_opponents.py --only lectureplayer
    python scripts/calibrate_opponents.py --maps DefaultSmall
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from evaluation.scoring import game_outcome  # noqa: E402
from harness import EngineConfig, MatchRunError, run_match  # noqa: E402
from harness.cache import MatchCache, MatchCacheKey, source_tree_hash  # noqa: E402
from opponents.lockfile import (  # noqa: E402
    VALID_TIERS,
    OpponentEntry,
    load_lockfile,
    save_lockfile,
)
from replay.decoder import decode_footer  # noqa: E402

LOCK_PATH = REPO_ROOT / "configs" / "opponents.lock.json"
DEFAULT_MAPS = ("DefaultSmall", "DefaultMedium")
DEFAULT_CACHE_ROOT = REPO_ROOT / "runs" / "match_cache"
RESULTS_OUT = REPO_ROOT / "runs" / "calibration" / "results.json"

# Ladder anchor 3 ("strongest compiled dev bot") preference order.
STRONG_DEV_PREFERENCE = (
    "alext101_finalsbot",
    "spsquared_delta",
    "alext101_sprint2bot",
    "alext101_sprint1bot",
)

# Constant runner-config hash: fixed headless flags enforced by harness.run_match.
CONFIG_HASH = hashlib.sha256(
    b"headless;validateMaps=false;alternateOrder=false;showIndicators=false"
).hexdigest()


def bot_hash(entry: OpponentEntry) -> str:
    """Content hash for cache keys: source-tree hash, or a stable tag for
    engine built-ins (whose source is pinned by the engine commit)."""
    if entry.source_dir is None:
        return f"builtin:{entry.package}@{entry.commit}"
    return source_tree_hash(Path(entry.source_dir) / entry.package)


def cached_match_footer(
    cache: MatchCache,
    engine: EngineConfig,
    team_a: OpponentEntry,
    team_b: OpponentEntry,
    map_name: str,
    timeout_s: int,
) -> dict:
    """Footer dict for team_a-vs-team_b on map_name, via the exact cache."""
    key = MatchCacheKey(
        engine_commit=engine.commit,
        bot_a_hash=bot_hash(team_a),
        bot_b_hash=bot_hash(team_b),
        map_name=map_name,
        side="AB",  # orientation already encoded by (bot_a_hash, bot_b_hash)
        config_hash=CONFIG_HASH,
    )
    hit = cache.get(key)
    if hit is not None:
        return hit["footer"]

    with tempfile.TemporaryDirectory(prefix="cal_") as tmp:
        replay_out = Path(tmp) / "m.bc26"
        result = run_match(
            team_a=team_a.package,
            team_b=team_b.package,
            map_name=map_name,
            replay_out=replay_out,
            engine=engine,
            class_location_a=team_a.classes_dir,
            class_location_b=team_b.classes_dir,
            timeout_s=timeout_s,
        )
        footer = decode_footer(result.replay_path)
        footer_jsonable = {
            "winner": footer["winner"],
            "win_type": footer["win_type"],
            "total_rounds": footer["total_rounds"],
        }
        cache.put(
            key,
            {"footer": footer_jsonable, "duration_s": result.duration_s},
            result.replay_path,
        )
        return footer_jsonable


def assign_tiers(means: dict[str, float]) -> dict[str, str]:
    """Rank-quartile tiers, ascending: bottom quartile = floor, top = strong.

    Equal means always share a tier (the tier of the group's lowest rank).
    """
    ordered = sorted(means, key=lambda bot_id: (means[bot_id], bot_id))
    n = len(ordered)
    tiers: dict[str, str] = {}
    group_start = 0
    for i, bot_id in enumerate(ordered):
        if i > 0 and means[bot_id] != means[ordered[i - 1]]:
            group_start = i
        tiers[bot_id] = VALID_TIERS[min(3, 4 * group_start // n)]
    return tiers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--maps", nargs="+", default=list(DEFAULT_MAPS))
    parser.add_argument("--only", action="append", default=None,
                        help="calibrate only these opponent id(s); repeatable")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--results-out", type=Path, default=RESULTS_OUT)
    parser.add_argument("--timeout-s", type=int, default=1800)
    args = parser.parse_args()

    engine = EngineConfig.from_lock(REPO_ROOT / "configs" / "engine.lock.json")
    cache = MatchCache(args.cache_root)
    entries = load_lockfile(args.lock)
    by_id = {e.id: e for e in entries}

    runnable = [e for e in entries if e.compiled and e.smoke_ok]
    candidates = [e for e in runnable if not args.only or e.id in args.only]
    if not candidates:
        print("[calibrate] no smoke-ok candidates matched; nothing to do")
        return 1

    strong_dev: Optional[OpponentEntry] = next(
        (by_id[i] for i in STRONG_DEV_PREFERENCE
         if i in by_id and by_id[i].compiled and by_id[i].smoke_ok),
        None,
    )
    ladder = [e for e in (by_id.get("examplefuncsplayer"),
                          by_id.get("lectureplayer"),
                          strong_dev)
              if e is not None and e.compiled and e.smoke_ok]
    # De-dup while preserving order (strong_dev can never be the built-ins,
    # but be safe).
    seen: set[str] = set()
    ladder = [e for e in ladder if not (e.id in seen or seen.add(e.id))]
    print(f"[calibrate] ladder: {[e.id for e in ladder]}; "
          f"maps: {args.maps}; candidates: {[e.id for e in candidates]}")

    games: list[dict] = []
    means: dict[str, float] = {}
    for cand in candidates:
        outcomes: list[float] = []
        for opp in ladder:
            if opp.id == cand.id:
                continue  # a mirror match carries no strength information
            for map_name in args.maps:
                for side in ("A", "B"):
                    team_a, team_b = (cand, opp) if side == "A" else (opp, cand)
                    try:
                        footer = cached_match_footer(
                            cache, engine, team_a, team_b, map_name, args.timeout_s
                        )
                    except (MatchRunError, ValueError, OSError) as exc:  # record, keep going
                        print(f"[calibrate] MATCH FAILED {cand.id} (side {side}) "
                              f"vs {opp.id} on {map_name}: {exc}")
                        games.append({
                            "candidate": cand.id, "opponent": opp.id,
                            "map": map_name, "side": side,
                            "error": str(exc)[:500],
                        })
                        continue
                    outcome = game_outcome(footer["win_type"], footer["winner"], side)
                    outcomes.append(outcome)
                    games.append({
                        "candidate": cand.id, "opponent": opp.id,
                        "map": map_name, "side": side,
                        "winner": footer["winner"],
                        "win_type": footer["win_type"],
                        "total_rounds": footer["total_rounds"],
                        "outcome": outcome,
                    })
                    print(f"[calibrate] {cand.id} (side {side}) vs {opp.id} "
                          f"on {map_name}: outcome={outcome} "
                          f"({footer['win_type']})")
        if outcomes:
            means[cand.id] = statistics.mean(outcomes)

    tiers = assign_tiers(means) if means else {}
    for bot_id, tier in tiers.items():
        by_id[bot_id].strength_tier = tier
    save_lockfile(args.lock, entries)

    args.results_out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.results_out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "ladder": [e.id for e in ladder],
                "maps": list(args.maps),
                "games": games,
                "mean_outcome": means,
                "tiers": tiers,
            },
            f, indent=2,
        )
        f.write("\n")

    print(f"\n[calibrate] wrote tiers to {args.lock} and results to {args.results_out}")
    for bot_id in sorted(means, key=means.get):
        print(f"  {bot_id:24} mean_outcome={means[bot_id]:.3f} tier={tiers[bot_id]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
