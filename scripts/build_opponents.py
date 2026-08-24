#!/usr/bin/env python3
"""Build every pinned opponent and write configs/opponents.lock.json.

Per PLAN.md section 10: pin commit -> verify license -> compile against the
pinned engine -> (smoke) -> record honestly.  A bot that fails to compile
against the final-API engine is excluded, never patched.

Usage:
    python scripts/build_opponents.py                # build + smoke everything
    python scripts/build_opponents.py --skip-smoke   # compile matrix only
    python scripts/build_opponents.py --only alext101_finalsbot
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness import EngineConfig, run_match, MatchRunError  # noqa: E402
from opponents.build import compile_bot, ensure_engine_classes  # noqa: E402
from opponents.lockfile import OpponentEntry, load_lockfile, save_lockfile  # noqa: E402
from replay.decoder import decode_footer  # noqa: E402

OPPONENT_CLONES = Path("/home/user/battlecode/opponents")
LECTUREPLAYER_ROOT = Path("/home/user/battlecode/battlecode26-lectureplayer")
BUILD_ROOT = REPO_ROOT / "runs" / "opponents_build"
LOCK_PATH = REPO_ROOT / "configs" / "opponents.lock.json"
SMOKE_MAP = "DefaultSmall"
SMOKE_BASELINE = "examplefuncsplayer"  # engine built-in, floor strength

# Roster (PLAN section 10).  source_dir is the directory CONTAINING the
# package dir; None marks an engine built-in that needs no compilation.
# Excluded on purpose: unlicensed repos (erikji, daannte) and all Cambridge
# (Python-engine) repos.
ROSTER: list[dict] = [
    {
        "id": "examplefuncsplayer",
        "repo": "https://github.com/battlecode/battlecode26",
        "git_dir": "/home/user/battlecode/battlecode26",
        "package": "examplefuncsplayer",
        "lineage": "official",
        "split": "dev",
        "license": "AGPL-3.0 (engine COPYING)",
        "source_dir": None,
    },
    {
        "id": "lectureplayer",
        "repo": "https://github.com/battlecode/battlecode26-lectureplayer",
        "git_dir": str(LECTUREPLAYER_ROOT),
        "package": "lectureplayer",
        "lineage": "official",
        "split": "dev",
        "license": "AGPL-3.0",
        "source_dir": str(LECTUREPLAYER_ROOT / "src"),
    },
    {
        "id": "alext101_sprint1bot",
        "repo": "https://github.com/AlexT101/battlecode26",
        "git_dir": str(OPPONENT_CLONES / "alext101"),
        "package": "sprint1bot",
        "lineage": "alext101",
        "split": "dev",
        "license": "AGPL-3.0",
        "source_dir": str(OPPONENT_CLONES / "alext101" / "src"),
    },
    {
        "id": "alext101_sprint2bot",
        "repo": "https://github.com/AlexT101/battlecode26",
        "git_dir": str(OPPONENT_CLONES / "alext101"),
        "package": "sprint2bot",
        "lineage": "alext101",
        "split": "dev",
        "license": "AGPL-3.0",
        "source_dir": str(OPPONENT_CLONES / "alext101" / "src"),
    },
    {
        "id": "alext101_finalsbot",
        "repo": "https://github.com/AlexT101/battlecode26",
        "git_dir": str(OPPONENT_CLONES / "alext101"),
        "package": "finalsbot",
        "lineage": "alext101",
        "split": "dev",
        "license": "AGPL-3.0",
        "source_dir": str(OPPONENT_CLONES / "alext101" / "src"),
    },
    {
        "id": "spsquared_delta",
        "repo": "https://github.com/spsquared/battlecode26",
        "git_dir": str(OPPONENT_CLONES / "spsquared"),
        "package": "Delta",
        "lineage": "spsquared",
        "split": "dev",
        "license": "AGPL-3.0",
        "source_dir": str(OPPONENT_CLONES / "spsquared" / "src"),
    },
    {
        "id": "uravt_version41",
        "repo": "https://github.com/uravt/Battlecode26",
        "git_dir": str(OPPONENT_CLONES / "uravt"),
        "package": "Version41",
        "lineage": "uravt",
        "split": "test",
        "license": "AGPL-3.0",
        "source_dir": str(OPPONENT_CLONES / "uravt" / "src"),
    },
    {
        "id": "adamtan_finals",
        "repo": "https://github.com/AdamTan12/BattleCode2026",
        "git_dir": str(OPPONENT_CLONES / "adamtan"),
        "package": "Finals",
        "lineage": "adamtan",
        "split": "test",
        "license": "AGPL-3.0",
        "source_dir": str(OPPONENT_CLONES / "adamtan" / "src"),
    },
    {
        "id": "awu7_awubot",
        "repo": "https://github.com/awu7/battlecode-2026",
        "git_dir": str(OPPONENT_CLONES / "awu7"),
        "package": "awubot",
        "lineage": "awu7",
        "split": "test",
        "license": "AGPL-3.0",
        "source_dir": str(OPPONENT_CLONES / "awu7" / "src"),
    },
    {
        "id": "r3vivify_result408",
        "repo": "https://github.com/r3viviFY/battlecode26_released",
        "git_dir": str(OPPONENT_CLONES / "r3vivify"),
        "package": "result_408",
        "lineage": "r3vivify",
        "split": "test",
        "license": "MIT",
        "source_dir": str(OPPONENT_CLONES / "r3vivify" / "battlecode26" / "src"),
    },
]


def git_head(git_dir: str) -> str:
    return subprocess.run(
        ["git", "-C", git_dir, "rev-parse", "HEAD"],
        check=True, stdout=subprocess.PIPE, text=True,
    ).stdout.strip()


def first_javac_errors(javac_output: str, n: int = 6) -> str:
    lines = [ln for ln in javac_output.splitlines() if "error:" in ln]
    return "\n".join(lines[:n]) if lines else javac_output[:500]


def smoke_run(entry: OpponentEntry, engine: EngineConfig) -> tuple[bool, str]:
    """One fast match vs the built-in baseline; smoke_ok iff the replay
    footer decodes.  The opponent plays team B (baseline is team A)."""
    replay_out = BUILD_ROOT / entry.id / "smoke.bc26"
    try:
        result = run_match(
            team_a=SMOKE_BASELINE,
            team_b=entry.package,
            map_name=SMOKE_MAP,
            replay_out=replay_out,
            engine=engine,
            class_location_b=entry.classes_dir,
            timeout_s=900,
        )
    except MatchRunError as exc:
        return False, f"match failed: {exc}"[:800]
    try:
        footer = decode_footer(result.replay_path)
    except Exception as exc:
        return False, f"decode_footer failed: {exc!r}"[:800]
    return True, (
        f"winner={footer['winner']} win_type={footer['win_type']} "
        f"rounds={footer['total_rounds']} ({result.duration_s:.1f}s)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=None,
                        help="restrict to these opponent id(s); repeatable")
    parser.add_argument("--skip-smoke", action="store_true",
                        help="compile only, leave smoke_ok=False")
    parser.add_argument("--lock-out", type=Path, default=LOCK_PATH)
    args = parser.parse_args()

    engine = EngineConfig.from_lock(REPO_ROOT / "configs" / "engine.lock.json")
    engine_cp = ensure_engine_classes(engine.engine_path)
    print(f"[build] engine classpath: {engine_cp}")

    entries: list[OpponentEntry] = []
    for spec in ROSTER:
        if args.only and spec["id"] not in args.only:
            continue
        entry = OpponentEntry(
            id=spec["id"],
            repo=spec["repo"],
            commit=git_head(spec["git_dir"]),
            package=spec["package"],
            lineage=spec["lineage"],
            split=spec["split"],
            license=spec["license"],
            source_dir=spec["source_dir"],
            classes_dir=None,
        )

        if entry.source_dir is None:
            # Engine built-in: nothing to compile, harness default classpath.
            entry.compiled = True
            print(f"[build] {entry.id}: engine built-in (no compile)")
        else:
            out_dir = BUILD_ROOT / entry.id / "classes"
            result = compile_bot(entry.source_dir, entry.package, out_dir, engine_cp)
            entry.compiled = result.ok
            if result.ok:
                entry.classes_dir = result.out_classes_dir
                print(f"[build] {entry.id}: compiled OK "
                      f"({result.num_sources} sources -> {out_dir})")
            else:
                entry.javac_error = first_javac_errors(result.javac_output)
                print(f"[build] {entry.id}: COMPILE FAILED\n"
                      f"{entry.javac_error}")

        if entry.compiled and not args.skip_smoke:
            ok, detail = smoke_run(entry, engine)
            entry.smoke_ok = ok
            print(f"[smoke] {entry.id}: {'OK' if ok else 'FAILED'} -- {detail}")
        entries.append(entry)

    if args.only and args.lock_out.exists():
        # Partial rebuild: merge into the existing lockfile by id.
        merged = {e.id: e for e in load_lockfile(args.lock_out)}
        for e in entries:
            merged[e.id] = e
        roster_order = [spec["id"] for spec in ROSTER]
        all_entries = sorted(
            merged.values(),
            key=lambda e: roster_order.index(e.id) if e.id in roster_order else len(roster_order),
        )
    else:
        all_entries = entries
    save_lockfile(args.lock_out, all_entries)
    print(f"\n[build] wrote {args.lock_out}")
    print(f"{'id':24} {'split':5} {'compiled':8} {'smoke_ok':8} package")
    for e in entries:
        print(f"{e.id:24} {e.split:5} {str(e.compiled):8} {str(e.smoke_ok):8} {e.package}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
