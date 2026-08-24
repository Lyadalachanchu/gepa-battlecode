#!/usr/bin/env python3
"""Behavioral equivalence check for the modular seed refactor (PLAN.md section 9).

For each (map, opponent, side) cell, runs two matches with the pinned engine:

* original seed  (``lectureplayer``,  bots/original_seed  compiled classes)
* modular seed   (``modular_seed``,   bots/modular_seed   compiled classes)

against the same opponent on the same map/side, decodes both replays, and
compares the candidate team's action streams: the sequence of
``(round, robot ordinal, per-turn state, actions)`` where per-turn state is
``(x, y, dir, health, cheese)`` and actions are the decoded action dicts —
EXCLUDING ``bytecodes_used`` (a refactor is allowed to change bytecode counts,
see the headroom rule). Reports per-game equal/diff with the first divergence,
plus the max ``bytecodes_used`` per unit type for both versions against the
engine limits (17500 baby rat / 20000 rat king; require >=30% headroom).

Usage (reduced suite):

    python scripts/check_equivalence.py \\
        --maps DefaultSmall Hike --opponents examplefuncsplayer --sides A B

Full PLAN suite is 5 maps x 2 opponents x 2 sides = 20 games per version.
Replays land under runs/equivalence/ and are reused on rerun unless --force.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness import EngineConfig, run_match  # noqa: E402
from replay import decode_match  # noqa: E402

ORIGINAL_PACKAGE = "lectureplayer"
MODULAR_PACKAGE = "modular_seed"

DEFAULT_OUT_DIR = REPO_ROOT / "runs" / "equivalence"
ORIGINAL_CLASSES = DEFAULT_OUT_DIR / "original_classes"
MODULAR_CLASSES = DEFAULT_OUT_DIR / "modular_classes"

# Engine per-turn bytecode limits (PLAN.md section 5 rule 6).
BYTECODE_LIMIT_KING = 20000
BYTECODE_LIMIT_BABY = 17500
REQUIRED_HEADROOM = 0.30

# Default = the reduced suite; the full 20-game suite passes 5 maps x 2 opponents.
DEFAULT_MAPS = ["DefaultSmall", "Hike"]
DEFAULT_OPPONENTS = ["examplefuncsplayer"]


TurnKey = tuple  # (round, ordinal, state tuple, actions tuple)


@dataclass
class GameComparison:
    map_name: str
    opponent: str
    side: str
    equal: bool
    original_len: int
    modular_len: int
    first_divergence: Optional[str] = None
    original_footer: dict = field(default_factory=dict)
    modular_footer: dict = field(default_factory=dict)
    original_bytecode_max: dict = field(default_factory=dict)
    modular_bytecode_max: dict = field(default_factory=dict)


def _action_key(action: dict[str, Any]) -> tuple:
    """Deterministic hashable form of one decoded action dict."""
    return tuple(sorted(action.items()))


def _candidate_stream(decoded: dict[str, Any], side: str) -> list[TurnKey]:
    """Candidate team's per-turn stream, excluding bytecodes_used.

    Robot ordinal = index of the turn among the candidate team's turns within
    the round (robust to any raw-id drift; ids still appear inside action
    fields such as target_id, so a real id divergence is still caught there).
    """
    stream: list[TurnKey] = []
    for rnd in decoded["rounds"]:
        ordinal = 0
        for turn in rnd["turns"]:
            if turn["team"] != side:
                continue
            state = (turn["x"], turn["y"], turn["dir"], turn["health"], turn["cheese"])
            actions = tuple(_action_key(a) for a in turn["actions"])
            stream.append((rnd["round"], ordinal, state, actions))
            ordinal += 1
    return stream


def _bytecode_max(decoded: dict[str, Any], side: str) -> dict[str, int]:
    """Max bytecodes_used per unit type for the candidate team."""
    out: dict[str, int] = {}
    for rnd in decoded["rounds"]:
        for turn in rnd["turns"]:
            if turn["team"] != side:
                continue
            rtype = turn["type"]
            bc = turn["bytecodes_used"]
            if bc > out.get(rtype, -1):
                out[rtype] = bc
    return out


def _limit_for(rtype: str) -> int:
    return BYTECODE_LIMIT_KING if "KING" in rtype.upper() else BYTECODE_LIMIT_BABY


def _describe_divergence(orig: list[TurnKey], mod: list[TurnKey]) -> str:
    n = min(len(orig), len(mod))
    for i in range(n):
        if orig[i] != mod[i]:
            o, m = orig[i], mod[i]
            return (
                f"stream index {i}: original (round={o[0]}, ordinal={o[1]}, "
                f"state(x,y,dir,health,cheese)={o[2]}, actions={o[3]}) != "
                f"modular (round={m[0]}, ordinal={m[1]}, state={m[2]}, actions={m[3]})"
            )
    return (
        f"streams identical for the first {n} turns, but lengths differ: "
        f"original={len(orig)} modular={len(mod)}"
    )


def _run_or_reuse(
    engine: EngineConfig,
    candidate_pkg: str,
    candidate_classes: Path,
    opponent: str,
    map_name: str,
    side: str,
    replay_path: Path,
    force: bool,
    timeout_s: int,
) -> Path:
    if replay_path.exists() and replay_path.stat().st_size > 0 and not force:
        return replay_path
    if side == "A":
        run_match(
            team_a=candidate_pkg,
            team_b=opponent,
            map_name=map_name,
            replay_out=replay_path,
            engine=engine,
            class_location_a=str(candidate_classes),
            timeout_s=timeout_s,
        )
    else:
        run_match(
            team_a=opponent,
            team_b=candidate_pkg,
            map_name=map_name,
            replay_out=replay_path,
            engine=engine,
            class_location_b=str(candidate_classes),
            timeout_s=timeout_s,
        )
    return replay_path


def compare_game(
    engine: EngineConfig,
    map_name: str,
    opponent: str,
    side: str,
    out_dir: Path,
    force: bool,
    timeout_s: int,
) -> GameComparison:
    tag = f"{map_name}_{opponent}_{side}"
    orig_replay = _run_or_reuse(
        engine, ORIGINAL_PACKAGE, ORIGINAL_CLASSES, opponent, map_name, side,
        out_dir / f"o_{tag}.bc26", force, timeout_s,
    )
    mod_replay = _run_or_reuse(
        engine, MODULAR_PACKAGE, MODULAR_CLASSES, opponent, map_name, side,
        out_dir / f"m_{tag}.bc26", force, timeout_s,
    )

    orig = decode_match(orig_replay)
    mod = decode_match(mod_replay)

    orig_stream = _candidate_stream(orig, side)
    mod_stream = _candidate_stream(mod, side)
    equal = orig_stream == mod_stream

    return GameComparison(
        map_name=map_name,
        opponent=opponent,
        side=side,
        equal=equal,
        original_len=len(orig_stream),
        modular_len=len(mod_stream),
        first_divergence=None if equal else _describe_divergence(orig_stream, mod_stream),
        original_footer=orig["footer"],
        modular_footer=mod["footer"],
        original_bytecode_max=_bytecode_max(orig, side),
        modular_bytecode_max=_bytecode_max(mod, side),
    )


def _merge_max(into: dict[str, int], other: dict[str, int]) -> None:
    for k, v in other.items():
        if v > into.get(k, -1):
            into[k] = v


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--maps", nargs="+", default=DEFAULT_MAPS)
    parser.add_argument("--opponents", nargs="+", default=DEFAULT_OPPONENTS)
    parser.add_argument("--sides", nargs="+", default=["A", "B"], choices=["A", "B"])
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--timeout-s", type=int, default=1200)
    parser.add_argument("--force", action="store_true",
                        help="rerun matches even if replays already exist")
    args = parser.parse_args(argv)

    engine = EngineConfig.from_lock()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for classes, pkg in ((ORIGINAL_CLASSES / ORIGINAL_PACKAGE, ORIGINAL_PACKAGE),
                         (MODULAR_CLASSES / MODULAR_PACKAGE, MODULAR_PACKAGE)):
        if not any(classes.glob("*.class")):
            print(f"ERROR: no compiled classes for {pkg} under {classes}")
            print("Compile first, e.g.:")
            print(f"  javac -cp {engine.engine_path}/engine/build/classes "
                  f"-d {classes.parent} bots/{pkg}/*.java")
            return 2

    results: list[GameComparison] = []
    orig_max: dict[str, int] = {}
    mod_max: dict[str, int] = {}

    for map_name in args.maps:
        for opponent in args.opponents:
            for side in args.sides:
                print(f"[game] map={map_name} opponent={opponent} side={side} ...",
                      flush=True)
                cmp = compare_game(engine, map_name, opponent, side,
                                   args.out_dir, args.force, args.timeout_s)
                results.append(cmp)
                _merge_max(orig_max, cmp.original_bytecode_max)
                _merge_max(mod_max, cmp.modular_bytecode_max)
                status = "EQUAL" if cmp.equal else "DIFF"
                print(f"  -> {status}  turns: original={cmp.original_len} "
                      f"modular={cmp.modular_len}  "
                      f"footer(orig)={cmp.original_footer}  "
                      f"footer(mod)={cmp.modular_footer}")
                if not cmp.equal:
                    print(f"  first divergence: {cmp.first_divergence}")

    print("\n=== Per-game summary ===")
    n_equal = 0
    for cmp in results:
        status = "EQUAL" if cmp.equal else "DIFF"
        n_equal += cmp.equal
        print(f"{status:5s}  {cmp.map_name} vs {cmp.opponent} side {cmp.side}")
    print(f"{n_equal}/{len(results)} games with identical candidate action streams")

    print("\n=== Max bytecodes_used per unit type (candidate team) ===")
    headroom_ok = True
    for label, table in (("original", orig_max), ("modular", mod_max)):
        for rtype in sorted(table):
            limit = _limit_for(rtype)
            used = table[rtype]
            headroom = 1.0 - used / limit
            flag = "" if headroom >= REQUIRED_HEADROOM else "  ** <30% HEADROOM **"
            if headroom < REQUIRED_HEADROOM:
                headroom_ok = False
            print(f"{label:8s} {rtype:12s} max={used:6d} limit={limit:5d} "
                  f"headroom={headroom:6.1%}{flag}")

    all_equal = n_equal == len(results)
    print(f"\nRESULT: {'PASS' if all_equal and headroom_ok else 'FAIL'} "
          f"(streams equal: {all_equal}, bytecode headroom >=30%: {headroom_ok})")
    return 0 if (all_equal and headroom_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
