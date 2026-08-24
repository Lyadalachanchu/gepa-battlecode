#!/usr/bin/env python3
"""Replay/token measurement over a directory of .bc26 files (PLAN.md s16).

Per replay: rounds, peak live units, raw/decoded sizes, and trace tokens at
level 0 plus each degrade-ladder level; across replays: median and p90 per
metric.  Results land in runs/trace_measurement.json (override with --out).

Usage:
    python scripts/measure_traces.py runs/equivalence
    python scripts/measure_traces.py <replay_dir> --out runs/trace_measurement.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from replay import TraceConfig  # noqa: E402
from replay.measure import measure_one  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "runs" / "trace_measurement.json"


def p90(values: Sequence[float]) -> float:
    """90th percentile (nearest-rank on the sorted values)."""
    if not values:
        raise ValueError("p90 of empty sequence")
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(0.9 * (len(ordered) - 1))))
    return float(ordered[rank])


def summarize(rows: list[dict]) -> dict:
    """The PLAN section 16 table: per-metric medians and p90s."""
    if not rows:
        return {}
    levels = sorted(rows[0]["trace_tokens"])
    scalar_metrics = {
        "rounds": [r["rounds"] for r in rows],
        "peak_live_units": [r["peak_live_units"] for r in rows],
        "raw_bytes": [r["raw_bytes"] for r in rows],
        "gunzipped_bytes": [r["gunzipped_bytes"] for r in rows],
        "full_decode_json_bytes": [r["full_decode_json_bytes"] for r in rows],
    }
    out: dict = {"n_replays": len(rows)}
    for name, vals in scalar_metrics.items():
        out[name] = {"median": statistics.median(vals), "p90": p90(vals)}
    out["trace_tokens"] = {
        level: {
            "median": statistics.median([r["trace_tokens"][level] for r in rows]),
            "p90": p90([r["trace_tokens"][level] for r in rows]),
        }
        for level in levels
    }
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("replay_dir", help="directory containing .bc26 files")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help=f"output JSON path (default {DEFAULT_OUT})")
    parser.add_argument("--limit", type=int, default=None,
                        help="measure at most N replays (sorted by name)")
    args = parser.parse_args(argv)

    replay_dir = Path(args.replay_dir)
    paths = sorted(replay_dir.rglob("*.bc26"))
    if args.limit is not None:
        paths = paths[: args.limit]
    if not paths:
        print(f"no .bc26 files under {replay_dir}", file=sys.stderr)
        return 2

    cfg = TraceConfig.from_experiment_yaml()
    rows = []
    for p in paths:
        row = measure_one(p, cfg)
        rows.append(row)
        print(
            f"{p.name}: rounds={row['rounds']} units={row['peak_live_units']} "
            + " ".join(f"{k}={v}" for k, v in sorted(row["trace_tokens"].items()))
        )

    payload = {
        "trace_config": {
            "snapshot_stride_rounds": cfg.snapshot_stride_rounds,
            "event_window_rounds": cfg.event_window_rounds,
            "final_window_rounds": cfg.final_window_rounds,
            "replay_token_budget": cfg.replay_token_budget,
            "max_games_per_call": cfg.max_games_per_call,
        },
        "per_replay": rows,
        "summary": summarize(rows),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out_path} ({len(rows)} replays)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
