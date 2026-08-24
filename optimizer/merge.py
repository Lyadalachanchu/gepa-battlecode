"""System-aware merge (arm D; PLAN.md section 13 / v2 s29).

Combine two frontier candidates whose lineages changed *different*
components since their common ancestor, producing a child that inherits
both improvements.

Preconditions for a pair (a, b):
* both live in the store and share a common ancestor;
* neither is an ancestor of the other;
* each leads at least one Pareto instance in ``scores``;
* their changed-component sets since the ancestor are complementary: each
  parent changed at least one component the other did not.

Composition starts from the ancestor's components, then applies each
parent's changed components.  A conflict (both changed the same component)
resolves to the version from the parent with the better macro-average
(tiebreak: lexicographically smaller candidate_id).
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from optimizer.candidate import Candidate, CandidateStore, make_candidate
from optimizer.pareto import DEFAULT_TOL, macro_average, per_instance_best_sets

__all__ = ["attempt_merge", "changed_components_since"]


def changed_components_since(
    store: CandidateStore, candidate_id: str, ancestor_id: str
) -> set[str]:
    """Component names whose (normalized-equivalent) source differs between
    a candidate and its ancestor."""
    from optimizer.candidate import normalize_source

    cand = store.get(candidate_id)
    anc = store.get(ancestor_id)
    changed: set[str] = set()
    for name in set(cand.components) | set(anc.components):
        a_src = cand.components.get(name)
        b_src = anc.components.get(name)
        if a_src is None or b_src is None:
            changed.add(name)
        elif normalize_source(a_src) != normalize_source(b_src):
            changed.add(name)
    return changed


def _leads_any_instance(
    cid: str, scores: Mapping[str, Sequence[float]], tol: float
) -> bool:
    best_sets = per_instance_best_sets(scores, tol)
    return any(cid in s for s in best_sets)


def attempt_merge(
    store: CandidateStore,
    frontier_ids: Sequence[str],
    scores: Mapping[str, Sequence[float]],
    tol: float = DEFAULT_TOL,
) -> Optional[Candidate]:
    """Try to merge one eligible pair of frontier candidates.

    Pairs are examined in deterministic sorted order; the first pair that
    satisfies all preconditions and composes to genuinely new content is
    merged, added to the store, and returned.  Returns None when no pair is
    eligible.
    """
    ids = sorted(set(frontier_ids))
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            child = _try_pair(store, a, b, scores, tol)
            if child is not None:
                return child
    return None


def _try_pair(
    store: CandidateStore,
    a: str,
    b: str,
    scores: Mapping[str, Sequence[float]],
    tol: float,
) -> Optional[Candidate]:
    if a not in store or b not in store:
        return None
    if a not in scores or b not in scores:
        return None
    if store.is_ancestor(a, b) or store.is_ancestor(b, a):
        return None
    ancestor = store.common_ancestor(a, b)
    if ancestor is None:
        return None
    if not (_leads_any_instance(a, scores, tol) and _leads_any_instance(b, scores, tol)):
        return None

    changed_a = changed_components_since(store, a, ancestor)
    changed_b = changed_components_since(store, b, ancestor)
    # Complementary: each parent contributes something the other did not.
    if not (changed_a - changed_b) or not (changed_b - changed_a):
        return None

    # Conflict resolution: better macro-average parent wins; tie -> smaller id.
    avg_a = macro_average(scores[a])
    avg_b = macro_average(scores[b])
    if avg_a > avg_b + tol:
        winner = a
    elif avg_b > avg_a + tol:
        winner = b
    else:
        winner = min(a, b)

    anc = store.get(ancestor)
    cand_a = store.get(a)
    cand_b = store.get(b)
    components = dict(anc.components)
    for name in sorted(changed_a - changed_b):
        components[name] = cand_a.components[name]
    for name in sorted(changed_b - changed_a):
        components[name] = cand_b.components[name]
    for name in sorted(changed_a & changed_b):
        components[name] = store.get(winner).components[name]

    child = make_candidate(
        components,
        parents=(a, b),
        generation=max(cand_a.generation, cand_b.generation) + 1,
        changed_component=None,
        proposal_id=f"merge:{a[:8]}+{b[:8]}",
        meta={
            "merge": {
                "ancestor": ancestor,
                "changed_a": sorted(changed_a),
                "changed_b": sorted(changed_b),
                "conflict_winner": winner if (changed_a & changed_b) else None,
            }
        },
    )
    # Degenerate: composition reproduces an existing pool member (e.g. one
    # of the parents, or the ancestor itself) -> nothing new, no merge.
    if child.candidate_id in (a, b, ancestor) or child.candidate_id in store:
        return None
    return store.add(child)
