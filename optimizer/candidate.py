"""Immutable content-addressed candidate store (PLAN.md section 18).

A candidate is a full set of component sources.  Its identity is the sha256
of the *normalized* sources, so byte-identical bots minted twice collapse to
one id, and the store never overwrites.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Mapping, Optional

__all__ = [
    "Candidate",
    "CandidateStore",
    "normalize_source",
    "candidate_id_for",
    "make_candidate",
]


def normalize_source(source: str) -> str:
    """Normalization applied before hashing: CRLF -> LF, strip trailing
    whitespace per line, exactly one trailing newline."""
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def candidate_id_for(components: Mapping[str, str]) -> str:
    """sha256 over the sorted (name, normalized source) pairs."""
    h = hashlib.sha256()
    for name in sorted(components):
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        h.update(normalize_source(components[name]).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


@dataclass(frozen=True)
class Candidate:
    """One bot version.  Immutable; identity derives from ``components``."""

    candidate_id: str
    components: Mapping[str, str]
    parents: tuple[str, ...] = ()
    generation: int = 0
    changed_component: Optional[str] = None
    proposal_id: Optional[str] = None
    meta: Mapping[str, object] = field(default_factory=dict)

    def to_json_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "components": dict(self.components),
            "parents": list(self.parents),
            "generation": self.generation,
            "changed_component": self.changed_component,
            "proposal_id": self.proposal_id,
            "meta": dict(self.meta),
        }

    @staticmethod
    def from_json_dict(d: Mapping[str, object]) -> "Candidate":
        return Candidate(
            candidate_id=str(d["candidate_id"]),
            components=dict(d["components"]),  # type: ignore[arg-type]
            parents=tuple(d.get("parents", ())),  # type: ignore[arg-type]
            generation=int(d.get("generation", 0)),  # type: ignore[arg-type]
            changed_component=d.get("changed_component"),  # type: ignore[arg-type]
            proposal_id=d.get("proposal_id"),  # type: ignore[arg-type]
            meta=dict(d.get("meta", {})),  # type: ignore[arg-type]
        )


def make_candidate(
    components: Mapping[str, str],
    parents: tuple[str, ...] | list[str] = (),
    generation: int = 0,
    changed_component: Optional[str] = None,
    proposal_id: Optional[str] = None,
    meta: Optional[Mapping[str, object]] = None,
) -> Candidate:
    """Build a Candidate with its content-addressed id computed for you."""
    return Candidate(
        candidate_id=candidate_id_for(components),
        components={k: components[k] for k in sorted(components)},
        parents=tuple(parents),
        generation=generation,
        changed_component=changed_component,
        proposal_id=proposal_id,
        meta=dict(meta or {}),
    )


class CandidateStore:
    """JSON-file-per-candidate store under ``dir``.  Never overwrites: adding
    content that hashes to an existing id returns the stored candidate."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Candidate] = {}

    # -- persistence ------------------------------------------------------
    def _path(self, candidate_id: str) -> Path:
        return self.dir / f"{candidate_id}.json"

    def add(self, candidate: Candidate) -> Candidate:
        """Insert; identical content collapses to the same id (first write
        wins, the stored record is returned)."""
        expected = candidate_id_for(candidate.components)
        if candidate.candidate_id != expected:
            raise ValueError(
                f"candidate_id {candidate.candidate_id} does not match content "
                f"hash {expected}; use make_candidate()"
            )
        existing = self.get(candidate.candidate_id, default=None)
        if existing is not None:
            return existing
        path = self._path(candidate.candidate_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(candidate.to_json_dict(), indent=1, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(path)
        self._cache[candidate.candidate_id] = candidate
        return candidate

    def get(self, candidate_id: str, default: object = ...) -> Candidate:
        if candidate_id in self._cache:
            return self._cache[candidate_id]
        path = self._path(candidate_id)
        if not path.exists():
            if default is ...:
                raise KeyError(candidate_id)
            return default  # type: ignore[return-value]
        cand = Candidate.from_json_dict(json.loads(path.read_text(encoding="utf-8")))
        self._cache[candidate_id] = cand
        return cand

    def __contains__(self, candidate_id: str) -> bool:
        return candidate_id in self._cache or self._path(candidate_id).exists()

    def all_ids(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.json"))

    def __iter__(self) -> Iterator[Candidate]:
        for cid in self.all_ids():
            yield self.get(cid)

    # -- genealogy --------------------------------------------------------
    def ancestors(self, candidate_id: str) -> set[str]:
        """All strict ancestors (transitive parents) of a candidate."""
        seen: set[str] = set()
        stack = list(self.get(candidate_id).parents)
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            parent = self.get(cid, default=None)
            if parent is not None:
                stack.extend(parent.parents)
        return seen

    def is_ancestor(self, ancestor_id: str, descendant_id: str) -> bool:
        """True iff ``ancestor_id`` is a strict ancestor of ``descendant_id``."""
        if ancestor_id == descendant_id:
            return False
        return ancestor_id in self.ancestors(descendant_id)

    def common_ancestor(self, a_id: str, b_id: str) -> Optional[str]:
        """Nearest common ancestor: the shared ancestor (a candidate itself
        counts as its own 'ancestor' here when one is an ancestor of the
        other) with the highest generation; ties break by id."""
        anc_a = self.ancestors(a_id) | {a_id}
        anc_b = self.ancestors(b_id) | {b_id}
        common = anc_a & anc_b
        if not common:
            return None
        known = [c for c in common if self.get(c, default=None) is not None]
        if not known:
            return None
        return max(known, key=lambda c: (self.get(c).generation, c))
