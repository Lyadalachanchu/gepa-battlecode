"""configs/opponents.lock.json schema (PLAN.md section 10).

The lockfile pins every opponent: repo, commit, package, lineage, dev/test
split, license, where its sources and compiled classes live, and the honest
compile/smoke outcome.  ``strength_tier`` starts null and is filled in by
``scripts/calibrate_opponents.py``.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

VALID_SPLITS = ("dev", "test")
VALID_TIERS = ("floor", "weak", "mid", "strong")


@dataclass
class OpponentEntry:
    id: str
    repo: str
    commit: str
    package: str
    lineage: str
    split: str  # "dev" | "test"
    license: str
    source_dir: Optional[str]  # None for engine built-ins
    classes_dir: Optional[str]  # None until compiled (or for built-ins)
    compiled: bool = False
    javac_error: Optional[str] = None
    smoke_ok: bool = False
    strength_tier: Optional[str] = None

    def __post_init__(self) -> None:
        if self.split not in VALID_SPLITS:
            raise ValueError(f"split must be one of {VALID_SPLITS}, got {self.split!r}")
        if self.strength_tier is not None and self.strength_tier not in VALID_TIERS:
            raise ValueError(
                f"strength_tier must be null or one of {VALID_TIERS}, got {self.strength_tier!r}"
            )


def load_lockfile(path: Union[str, Path]) -> list[OpponentEntry]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [OpponentEntry(**entry) for entry in raw["opponents"]]


def save_lockfile(path: Union[str, Path], entries: list[OpponentEntry]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"opponents": [dataclasses.asdict(e) for e in entries]}
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    tmp.replace(path)
