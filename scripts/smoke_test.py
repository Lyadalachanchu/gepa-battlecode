#!/usr/bin/env python3
"""End-to-end smoke test: run one real match and (if available) decode its footer.

Runs examplefuncsplayer vs examplefuncsplayer on DefaultSmall via the harness,
writing the replay to runs/smoke/. Then, import-guarded (the replay package is
built concurrently by another workstream), tries replay.decoder.decode_footer
on the produced file and prints the result.

Exit code 0 on success.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness import EngineConfig, run_match  # noqa: E402


def main() -> int:
    engine = EngineConfig.from_lock(REPO_ROOT / "configs" / "engine.lock.json")
    out_dir = REPO_ROOT / "runs" / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    replay_out = out_dir / "smoke.bc26"

    print(f"[smoke] running examplefuncsplayer vs examplefuncsplayer on DefaultSmall "
          f"(engine: {engine.engine_path})")
    result = run_match(
        team_a="examplefuncsplayer",
        team_b="examplefuncsplayer",
        map_name="DefaultSmall",
        replay_out=replay_out,
        engine=engine,
    )
    size = Path(result.replay_path).stat().st_size
    print(f"[smoke] match OK: replay={result.replay_path} ({size} bytes), "
          f"returncode={result.returncode}, duration={result.duration_s:.1f}s")

    try:
        from replay.decoder import decode_footer  # type: ignore
    except ImportError as exc:
        print(f"[smoke] replay.decoder not available yet ({exc}); skipping footer decode")
        return 0

    try:
        footer = decode_footer(result.replay_path)
    except Exception as exc:  # decoding failure is a real smoke failure
        print(f"[smoke] FAIL: decode_footer raised: {exc!r}")
        return 1
    print(f"[smoke] decode_footer: {footer}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
