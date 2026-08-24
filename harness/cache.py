"""Exact match cache.

The pinned engine is fully deterministic (PLAN.md section 5 rule 7): the match
seed is baked into each ``.map26``, so (engine commit, candidate hash, opponent
hash, map, side, runner config) determines exactly one outcome. Re-running a
cached cell adds zero information, so cache hits are free and shared across
arms and seeds.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


def source_tree_hash(path: Union[str, Path]) -> str:
    """SHA-256 over the sorted (relpath, file bytes) of every ``*.java`` file.

    Deterministic: files are ordered by their POSIX-style relative path, and
    each contributes its path and raw bytes (length-delimited so boundaries
    are unambiguous). Non-Java files are ignored.
    """
    root = Path(path)
    if not root.is_dir():
        raise ValueError(f"source_tree_hash expects a directory, got {root}")
    entries = sorted(
        (p.relative_to(root).as_posix(), p) for p in root.rglob("*.java") if p.is_file()
    )
    h = hashlib.sha256()
    for relpath, p in entries:
        data = p.read_bytes()
        rel_bytes = relpath.encode("utf-8")
        h.update(len(rel_bytes).to_bytes(8, "big"))
        h.update(rel_bytes)
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


@dataclass(frozen=True)
class MatchCacheKey:
    """Exact-match cache key. Two identical keys imply an identical outcome."""

    engine_commit: str
    bot_a_hash: str
    bot_b_hash: str
    map_name: str
    side: str  # e.g. "AB" vs "BA" -- side swap changes outcomes
    config_hash: str

    def digest(self) -> str:
        payload = json.dumps(dataclasses.asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MatchCache:
    """Content-addressed on-disk cache of match results and replay files.

    Layout: ``<root>/<digest[:2]>/<digest>/{key.json, result.json, replay.bc26}``.
    """

    _RESULT_NAME = "result.json"
    _REPLAY_NAME = "replay.bc26"
    _KEY_NAME = "key.json"

    def __init__(self, root_dir: Union[str, Path]):
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _entry_dir(self, key: MatchCacheKey) -> Path:
        digest = key.digest()
        return self.root / digest[:2] / digest

    def replay_path(self, key: MatchCacheKey) -> Path:
        return self._entry_dir(key) / self._REPLAY_NAME

    def get(self, key: MatchCacheKey) -> Optional[dict]:
        """Return the stored result dict (with ``replay_path`` pointing at the
        cached replay copy), or None on a miss."""
        entry = self._entry_dir(key)
        result_file = entry / self._RESULT_NAME
        replay_file = entry / self._REPLAY_NAME
        if not result_file.exists() or not replay_file.exists():
            return None
        with open(result_file, "r", encoding="utf-8") as f:
            result = json.load(f)
        result["replay_path"] = str(replay_file)
        return result

    def put(self, key: MatchCacheKey, result_dict: dict, replay_src_path: Union[str, Path]) -> dict:
        """Store a result + its replay file; returns the stored result dict.

        The replay is copied into the cache (the source file is untouched).
        Writes are atomic-ish: files are written to temp names then renamed,
        with result.json last so a partially written entry reads as a miss.
        """
        replay_src = Path(replay_src_path)
        if not replay_src.exists() or replay_src.stat().st_size == 0:
            raise ValueError(f"replay_src_path missing or empty: {replay_src}")

        entry = self._entry_dir(key)
        entry.mkdir(parents=True, exist_ok=True)

        tmp_replay = entry / (self._REPLAY_NAME + ".tmp")
        shutil.copyfile(replay_src, tmp_replay)
        tmp_replay.replace(entry / self._REPLAY_NAME)

        with open(entry / self._KEY_NAME, "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(key), f, sort_keys=True, indent=2)

        stored = dict(result_dict)
        stored.pop("replay_path", None)  # authoritative path is the cached copy
        tmp_result = entry / (self._RESULT_NAME + ".tmp")
        with open(tmp_result, "w", encoding="utf-8") as f:
            json.dump(stored, f, sort_keys=True, indent=2)
        tmp_result.replace(entry / self._RESULT_NAME)

        stored["replay_path"] = str(entry / self._REPLAY_NAME)
        return stored
