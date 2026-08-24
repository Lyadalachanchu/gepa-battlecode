#!/usr/bin/env python3
"""Deterministic map splits: feedback / pareto / test (PLAN.md section 12).

Splits the 74 official maps into ~60% feedback / ~20% pareto / ~20% test,
stratified by size bucket (small <=35, medium <=50, large >50 on the max
dimension).  The unit of assignment is a *geometry group* (rotated/reflected
wall-bitmap clones, see evaluation.maps.geometry_group) -- a group is never
split across sets, so no near-duplicate of a pareto/test map leaks into
feedback.

From the pareto split, exactly 4 maps are chosen as the Pareto-validation
maps for the 48-instance grid (6 dev opponents x 4 maps x 2 sides):
candidates are sorted by (max dimension, cat count, name) and picked by
stride, giving a deterministic spread of size buckets and cat counts.

Everything is seeded by the fixed SPLIT_SEED constant, which is written into
the lockfile.  Re-running this script reproduces configs/maps.lock.json
byte-for-byte.
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from evaluation.maps import all_map_metadata, size_bucket  # noqa: E402

#: Fixed split seed, frozen into the lockfile (PLAN.md: frozen before optimization).
SPLIT_SEED = 2026

#: Fraction of each size bucket's maps reserved for the pareto / test splits.
PARETO_FRACTION = 0.2
TEST_FRACTION = 0.2

#: Number of Pareto-validation maps for the 48-instance grid.
N_PARETO_MAPS = 4

BUCKET_ORDER = ("small", "medium", "large")

LOCK_PATH = REPO_ROOT / "configs" / "maps.lock.json"


def _group_maps(metas: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """geometry_group id -> its maps (metas assumed sorted by name)."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in metas:
        groups[m["geometry_group"]].append(m)
    return dict(groups)


def _group_bucket(members: list[dict[str, Any]]) -> str:
    """Size bucket of a geometry group (max dimension over its members)."""
    dim = max(max(m["width"], m["height"]) for m in members)
    return size_bucket(dim, dim)


def _choose_pareto_maps(pareto_metas: list[dict[str, Any]]) -> list[str]:
    """Pick N_PARETO_MAPS from the pareto split: sort by (max dimension,
    cat count, name) and take evenly-strided indices -- a deterministic
    spread of size buckets and cat counts."""
    if len(pareto_metas) < N_PARETO_MAPS:
        raise ValueError(
            f"pareto split has {len(pareto_metas)} maps < {N_PARETO_MAPS}"
        )
    ordered = sorted(
        pareto_metas,
        key=lambda m: (max(m["width"], m["height"]), m["n_cats"], m["name"]),
    )
    n = len(ordered)
    picks = [ordered[i * (n - 1) // (N_PARETO_MAPS - 1)] for i in range(N_PARETO_MAPS)]
    names = [m["name"] for m in picks]
    if len(set(names)) != N_PARETO_MAPS:
        raise AssertionError(f"stride picks collided: {names}")
    return names


def build_lock(
    seed: int = SPLIT_SEED, directory: str | Path | None = None
) -> dict[str, Any]:
    """Compute the full maps lockfile content as a dict (deterministic)."""
    metas = all_map_metadata(directory)
    groups = _group_maps(metas)

    # Stratify geometry groups by size bucket.
    by_bucket: dict[str, list[str]] = {b: [] for b in BUCKET_ORDER}
    for gid in sorted(groups):
        by_bucket[_group_bucket(groups[gid])].append(gid)

    rng = random.Random(seed)
    splits: dict[str, list[str]] = {"feedback": [], "pareto": [], "test": []}
    for bucket in BUCKET_ORDER:
        gids = sorted(by_bucket[bucket])
        rng.shuffle(gids)
        n_maps = sum(len(groups[g]) for g in gids)
        target_pareto = round(PARETO_FRACTION * n_maps)
        target_test = round(TEST_FRACTION * n_maps)
        counts = {"pareto": 0, "test": 0}
        for gid in gids:
            if counts["pareto"] < target_pareto:
                dest = "pareto"
            elif counts["test"] < target_test:
                dest = "test"
            else:
                dest = "feedback"
            names = [m["name"] for m in groups[gid]]
            splits[dest].extend(names)
            if dest in counts:
                counts[dest] += len(names)

    for key in splits:
        splits[key] = sorted(splits[key])

    meta_by_name = {m["name"]: m for m in metas}
    pareto_maps = _choose_pareto_maps([meta_by_name[n] for n in splits["pareto"]])

    return {
        "seed": seed,
        "pareto_maps": pareto_maps,
        "splits": splits,
        "maps": metas,
    }


def write_lock(lock: dict[str, Any], path: str | Path = LOCK_PATH) -> None:
    Path(path).write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    lock = build_lock()
    write_lock(lock)
    splits = lock["splits"]
    total = sum(len(v) for v in splits.values())
    print(f"[split_maps] wrote {LOCK_PATH}")
    print(f"[split_maps] seed={lock['seed']}  maps={total}  "
          + "  ".join(f"{k}={len(v)}" for k, v in sorted(splits.items())))
    print(f"[split_maps] pareto_maps: {', '.join(lock['pareto_maps'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
