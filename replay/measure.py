"""Replay/trace measurement CLI (PLAN.md section 16).

Usage:
    python -m replay.measure <replay.bc26> [<replay.bc26> ...]

Per replay, prints: raw bytes, gunzipped bytes, rounds, peak live units,
T2-relevant event counts, full-decode JSON size, and trace token counts at
each degrade-ladder level.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import Counter
from pathlib import Path

from .decoder import decode_match
from .tokens import count_tokens
from .trace import DEGRADE_LADDER, TraceConfig, build_trace, degrade

_EVENT_ACTIONS = (
    "SpawnAction", "DieAction", "UpgradeToRatKing", "PlaceTrap",
    "TriggerTrap", "RatNap", "ThrowRat", "CatFeed", "CatPounce", "RatSqueak",
)


def measure_one(path: str | Path, cfg: TraceConfig) -> dict:
    path = Path(path)
    raw_bytes = path.stat().st_size
    with gzip.open(path, "rb") as f:
        gunzipped_bytes = len(f.read())
    decoded = decode_match(path)

    peak_units = 0
    action_counts: Counter[str] = Counter()
    for rnd in decoded["rounds"]:
        peak_units = max(peak_units, len(rnd["turns"]))
        for turn in rnd["turns"]:
            for a in turn["actions"]:
                action_counts[a["type"]] += 1
        action_counts["died_ids"] += len(rnd["died_ids"])

    json_size = len(json.dumps(decoded, separators=(",", ":")))

    trace_tokens: dict[str, int] = {}
    levels = [("level0", 0)] + [
        (name, i + 1) for i, name in enumerate(DEGRADE_LADDER)
    ]
    for name, level in levels:
        trace_tokens[name] = count_tokens(
            build_trace(decoded, degrade(cfg, level))
        )

    return {
        "path": str(path),
        "raw_bytes": raw_bytes,
        "gunzipped_bytes": gunzipped_bytes,
        "rounds": len(decoded["rounds"]),
        "total_rounds": decoded["footer"]["total_rounds"],
        "winner": decoded["footer"]["winner"],
        "win_type": decoded["footer"]["win_type"],
        "peak_live_units": peak_units,
        "event_counts": {
            k: action_counts.get(k, 0) for k in _EVENT_ACTIONS
        } | {"died_ids": action_counts.get("died_ids", 0)},
        "full_decode_json_bytes": json_size,
        "trace_tokens": trace_tokens,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    cfg = TraceConfig.from_experiment_yaml()
    for p in argv:
        m = measure_one(p, cfg)
        print(f"== {m['path']}")
        print(f"  raw bytes:        {m['raw_bytes']}")
        print(f"  gunzipped bytes:  {m['gunzipped_bytes']}")
        print(f"  rounds:           {m['rounds']} (footer {m['total_rounds']})")
        print(f"  result:           winner={m['winner']} winType={m['win_type']}")
        print(f"  peak live units:  {m['peak_live_units']}")
        print(f"  event counts:     "
              + " ".join(f"{k}={v}" for k, v in m["event_counts"].items() if v)
              or "  event counts:     (none)")
        print(f"  full JSON bytes:  {m['full_decode_json_bytes']}")
        print(f"  trace tokens:     "
              + " ".join(f"{k}={v}" for k, v in m["trace_tokens"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
