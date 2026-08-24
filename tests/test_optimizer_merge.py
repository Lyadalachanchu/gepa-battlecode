"""Tests for optimizer/merge.py: preconditions and conflict resolution."""
import pytest

from optimizer.candidate import CandidateStore, make_candidate
from optimizer.merge import attempt_merge, changed_components_since

ROOT = {"economy": "E0", "combat": "C0", "strategy": "S0"}


def build(tmp_path):
    """root; a changed economy; b changed combat.  Complementary cousins."""
    store = CandidateStore(tmp_path)
    root = store.add(make_candidate(ROOT, generation=0))
    a = store.add(
        make_candidate(
            {**ROOT, "economy": "E1"},
            parents=(root.candidate_id,),
            generation=1,
            changed_component="economy",
        )
    )
    b = store.add(
        make_candidate(
            {**ROOT, "combat": "C1"},
            parents=(root.candidate_id,),
            generation=1,
            changed_component="combat",
        )
    )
    return store, root, a, b


class TestChangedComponents:
    def test_diff_since_ancestor(self, tmp_path):
        store, root, a, b = build(tmp_path)
        assert changed_components_since(store, a.candidate_id, root.candidate_id) == {"economy"}
        assert changed_components_since(store, b.candidate_id, root.candidate_id) == {"combat"}

    def test_normalization_ignored_changes(self, tmp_path):
        store = CandidateStore(tmp_path)
        root = store.add(make_candidate({"x": "code\n"}))
        # Whitespace-only differences do not count as changes -- but note they
        # also hash identically, so they are literally the same candidate.
        assert changed_components_since(store, root.candidate_id, root.candidate_id) == set()


class TestMergeHappyPath:
    def test_complementary_cousins_merge(self, tmp_path):
        store, root, a, b = build(tmp_path)
        scores = {a.candidate_id: (1.0, 0.0), b.candidate_id: (0.0, 1.0)}
        child = attempt_merge(store, [a.candidate_id, b.candidate_id], scores)
        assert child is not None
        assert child.components["economy"] == "E1"  # from a
        assert child.components["combat"] == "C1"  # from b
        assert child.components["strategy"] == "S0"  # untouched ancestor code
        assert set(child.parents) == {a.candidate_id, b.candidate_id}
        assert child.generation == 2
        assert child.candidate_id in store
        assert child.meta["merge"]["ancestor"] == root.candidate_id

    def test_merge_is_deterministic(self, tmp_path):
        store, root, a, b = build(tmp_path)
        scores = {a.candidate_id: (1.0, 0.0), b.candidate_id: (0.0, 1.0)}
        c1 = attempt_merge(store, [a.candidate_id, b.candidate_id], scores)
        # Second attempt: identical composition already exists -> no new child.
        c2 = attempt_merge(store, [a.candidate_id, b.candidate_id], scores)
        assert c1 is not None and c2 is None


class TestMergePreconditions:
    def test_requires_each_parent_to_lead_an_instance(self, tmp_path):
        store, root, a, b = build(tmp_path)
        # b leads nothing: a is best on both instances.
        scores = {a.candidate_id: (1.0, 1.0), b.candidate_id: (0.5, 0.5)}
        assert attempt_merge(store, [a.candidate_id, b.candidate_id], scores) is None

    def test_ancestor_descendant_pair_refused(self, tmp_path):
        store, root, a, b = build(tmp_path)
        a2 = store.add(
            make_candidate(
                {**ROOT, "economy": "E2"},
                parents=(a.candidate_id,),
                generation=2,
                changed_component="economy",
            )
        )
        scores = {a.candidate_id: (1.0, 0.0), a2.candidate_id: (0.0, 1.0)}
        assert attempt_merge(store, [a.candidate_id, a2.candidate_id], scores) is None

    def test_no_common_ancestor_refused(self, tmp_path):
        store, root, a, b = build(tmp_path)
        orphan = store.add(make_candidate({**ROOT, "strategy": "SX"}))
        scores = {a.candidate_id: (1.0, 0.0), orphan.candidate_id: (0.0, 1.0)}
        assert attempt_merge(store, [a.candidate_id, orphan.candidate_id], scores) is None

    def test_non_complementary_refused(self, tmp_path):
        # Both lineages changed only economy: nothing to compose.
        store = CandidateStore(tmp_path)
        root = store.add(make_candidate(ROOT))
        a = store.add(
            make_candidate({**ROOT, "economy": "EA"}, parents=(root.candidate_id,), generation=1)
        )
        b = store.add(
            make_candidate({**ROOT, "economy": "EB"}, parents=(root.candidate_id,), generation=1)
        )
        scores = {a.candidate_id: (1.0, 0.0), b.candidate_id: (0.0, 1.0)}
        assert attempt_merge(store, [a.candidate_id, b.candidate_id], scores) is None

    def test_single_frontier_member_no_merge(self, tmp_path):
        store, root, a, b = build(tmp_path)
        assert attempt_merge(store, [a.candidate_id], {a.candidate_id: (1.0,)}) is None


class TestConflictResolution:
    def build_overlapping(self, tmp_path, avg_a_better):
        """a changed {economy, strategy}; b changed {combat, strategy}:
        complementary uniques plus a strategy conflict."""
        store = CandidateStore(tmp_path)
        root = store.add(make_candidate(ROOT))
        a = store.add(
            make_candidate(
                {**ROOT, "economy": "EA", "strategy": "SA"},
                parents=(root.candidate_id,),
                generation=1,
            )
        )
        b = store.add(
            make_candidate(
                {**ROOT, "combat": "CB", "strategy": "SB"},
                parents=(root.candidate_id,),
                generation=1,
            )
        )
        if avg_a_better:
            scores = {a.candidate_id: (1.0, 0.9, 0.0), b.candidate_id: (0.0, 0.0, 1.0)}
        else:
            scores = {a.candidate_id: (1.0, 0.0, 0.0), b.candidate_id: (0.0, 0.9, 1.0)}
        return store, root, a, b, scores

    def test_conflict_goes_to_better_macro_average_parent(self, tmp_path):
        store, root, a, b, scores = self.build_overlapping(tmp_path, avg_a_better=True)
        child = attempt_merge(store, [a.candidate_id, b.candidate_id], scores)
        assert child is not None
        assert child.components["economy"] == "EA"
        assert child.components["combat"] == "CB"
        assert child.components["strategy"] == "SA"  # a's macro-average wins
        assert child.meta["merge"]["conflict_winner"] == a.candidate_id

    def test_conflict_other_direction(self, tmp_path):
        store, root, a, b, scores = self.build_overlapping(tmp_path, avg_a_better=False)
        child = attempt_merge(store, [a.candidate_id, b.candidate_id], scores)
        assert child is not None
        assert child.components["strategy"] == "SB"

    def test_conflict_tie_breaks_by_smaller_id(self, tmp_path):
        store, root, a, b, scores = self.build_overlapping(tmp_path, avg_a_better=True)
        tied = {
            a.candidate_id: (1.0, 0.0, 0.0),
            b.candidate_id: (0.0, 0.0, 1.0),
        }
        child = attempt_merge(store, [a.candidate_id, b.candidate_id], tied)
        assert child is not None
        winner = min(a.candidate_id, b.candidate_id)
        expected = "SA" if winner == a.candidate_id else "SB"
        assert child.components["strategy"] == expected


def test_merge_ignores_frontier_ids_missing_from_store_or_scores(tmp_path):
    store, root, a, b = build(tmp_path)
    scores = {a.candidate_id: (1.0, 0.0)}  # b has no scores yet
    assert attempt_merge(store, [a.candidate_id, b.candidate_id, "ghost"], scores) is None
