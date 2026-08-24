"""Tests for optimizer/candidate.py: content addressing, store, genealogy."""
import json

import pytest

from optimizer.candidate import (
    Candidate,
    CandidateStore,
    candidate_id_for,
    make_candidate,
    normalize_source,
)

COMPS = {"economy": "int e = 1;\n", "combat": "int c = 2;\n"}


class TestNormalization:
    def test_trailing_whitespace_and_newlines(self):
        assert normalize_source("a  \nb\t\n\n\n") == "a\nb\n"
        assert normalize_source("a\r\nb\r") == "a\nb\n"
        assert normalize_source("a") == "a\n"

    def test_id_stable_under_normalization(self):
        a = candidate_id_for({"x": "code();\n"})
        b = candidate_id_for({"x": "code();   \r\n\n"})
        assert a == b

    def test_id_sensitive_to_content_and_names(self):
        assert candidate_id_for({"x": "a\n"}) != candidate_id_for({"x": "b\n"})
        assert candidate_id_for({"x": "a\n"}) != candidate_id_for({"y": "a\n"})

    def test_id_independent_of_dict_order(self):
        assert candidate_id_for({"a": "1", "b": "2"}) == candidate_id_for(
            {"b": "2", "a": "1"}
        )


class TestStore:
    def test_add_get_roundtrip(self, tmp_path):
        store = CandidateStore(tmp_path)
        cand = make_candidate(COMPS, proposal_id="seed", meta={"note": "s"})
        store.add(cand)
        # Fresh store instance reads from disk.
        store2 = CandidateStore(tmp_path)
        got = store2.get(cand.candidate_id)
        assert got.components == dict(COMPS)
        assert got.proposal_id == "seed"
        assert got.meta == {"note": "s"}
        assert store2.all_ids() == [cand.candidate_id]

    def test_identical_content_collapses_to_same_id(self, tmp_path):
        store = CandidateStore(tmp_path)
        first = store.add(make_candidate(COMPS, proposal_id="first"))
        # Same content, different metadata: no overwrite, first record wins.
        second = store.add(make_candidate(COMPS, proposal_id="second"))
        assert first.candidate_id == second.candidate_id
        assert second.proposal_id == "first"
        assert len(store.all_ids()) == 1

    def test_add_rejects_wrong_id(self, tmp_path):
        store = CandidateStore(tmp_path)
        bad = Candidate(candidate_id="deadbeef", components=COMPS)
        with pytest.raises(ValueError):
            store.add(bad)

    def test_get_missing_raises(self, tmp_path):
        store = CandidateStore(tmp_path)
        with pytest.raises(KeyError):
            store.get("nope")
        assert store.get("nope", default=None) is None

    def test_json_on_disk(self, tmp_path):
        store = CandidateStore(tmp_path)
        cand = store.add(make_candidate(COMPS))
        raw = json.loads((tmp_path / f"{cand.candidate_id}.json").read_text())
        assert raw["candidate_id"] == cand.candidate_id
        assert raw["components"] == dict(COMPS)


def lineage_store(tmp_path):
    """root -> a -> a2 ; root -> b   (two lineages off one ancestor)."""
    store = CandidateStore(tmp_path)
    root = store.add(make_candidate({"e": "0", "c": "0"}, generation=0))
    a = store.add(
        make_candidate(
            {"e": "A1", "c": "0"},
            parents=(root.candidate_id,),
            generation=1,
            changed_component="e",
        )
    )
    a2 = store.add(
        make_candidate(
            {"e": "A2", "c": "0"},
            parents=(a.candidate_id,),
            generation=2,
            changed_component="e",
        )
    )
    b = store.add(
        make_candidate(
            {"e": "0", "c": "B1"},
            parents=(root.candidate_id,),
            generation=1,
            changed_component="c",
        )
    )
    return store, root, a, a2, b


class TestGenealogy:
    def test_is_ancestor(self, tmp_path):
        store, root, a, a2, b = lineage_store(tmp_path)
        assert store.is_ancestor(root.candidate_id, a2.candidate_id)
        assert store.is_ancestor(a.candidate_id, a2.candidate_id)
        assert not store.is_ancestor(a2.candidate_id, a.candidate_id)
        assert not store.is_ancestor(b.candidate_id, a2.candidate_id)
        # A candidate is not its own strict ancestor.
        assert not store.is_ancestor(a.candidate_id, a.candidate_id)

    def test_common_ancestor_cousins(self, tmp_path):
        store, root, a, a2, b = lineage_store(tmp_path)
        assert store.common_ancestor(a2.candidate_id, b.candidate_id) == root.candidate_id
        assert store.common_ancestor(a.candidate_id, b.candidate_id) == root.candidate_id

    def test_common_ancestor_direct_line_is_the_elder(self, tmp_path):
        store, root, a, a2, b = lineage_store(tmp_path)
        assert store.common_ancestor(a.candidate_id, a2.candidate_id) == a.candidate_id

    def test_common_ancestor_none_for_disjoint_roots(self, tmp_path):
        store, root, a, a2, b = lineage_store(tmp_path)
        orphan = store.add(make_candidate({"e": "X", "c": "X"}))
        assert store.common_ancestor(orphan.candidate_id, a.candidate_id) is None
