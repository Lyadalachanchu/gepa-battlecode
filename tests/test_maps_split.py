"""Tests for evaluation.maps and scripts/split_maps.py (PLAN.md section 12)."""
from __future__ import annotations

import json
from collections import defaultdict

import pytest

from evaluation.maps import all_map_metadata, map_metadata, maps_dir, size_bucket
from scripts.split_maps import LOCK_PATH, SPLIT_SEED, build_lock

EXPECTED_DIMS = {
    "DefaultSmall": (30, 30),
    "DefaultMedium": (45, 45),
    "Hike": (20, 60),
}

METADATA_KEYS = {
    "name", "width", "height", "symmetry", "random_seed", "n_mines",
    "n_cats", "n_initial_rats", "wall_density", "dirt_density",
    "geometry_group",
}


@pytest.fixture(scope="module")
def metas() -> list[dict]:
    return all_map_metadata()


@pytest.fixture(scope="module")
def lock() -> dict:
    return build_lock()


@pytest.mark.parametrize("name,dims", sorted(EXPECTED_DIMS.items()))
def test_map_metadata_known_maps(name: str, dims: tuple[int, int]) -> None:
    meta = map_metadata(maps_dir() / f"{name}.map26")
    assert set(meta) == METADATA_KEYS
    assert meta["name"] == name
    assert (meta["width"], meta["height"]) == dims
    assert meta["symmetry"] in (0, 1, 2)
    assert meta["n_cats"] >= 1
    assert meta["n_initial_rats"] >= 2  # one rat king per team at minimum
    assert meta["n_mines"] > 0
    assert 0.0 <= meta["wall_density"] < 1.0
    assert 0.0 <= meta["dirt_density"] < 1.0
    assert isinstance(meta["geometry_group"], str) and len(meta["geometry_group"]) == 64


def test_all_maps_parse(metas: list[dict]) -> None:
    assert len(metas) == 74
    assert [m["name"] for m in metas] == sorted(m["name"] for m in metas)


def test_split_is_partition(lock: dict, metas: list[dict]) -> None:
    splits = lock["splits"]
    assert set(splits) == {"feedback", "pareto", "test"}
    names = [n for v in splits.values() for n in v]
    assert len(names) == len(set(names)) == 74
    assert sorted(names) == [m["name"] for m in metas]


def test_pareto_maps(lock: dict) -> None:
    assert len(lock["pareto_maps"]) == 4
    assert len(set(lock["pareto_maps"])) == 4
    assert set(lock["pareto_maps"]) <= set(lock["splits"]["pareto"])


def test_groups_never_straddle_splits(lock: dict) -> None:
    split_of = {n: k for k, v in lock["splits"].items() for n in v}
    group_splits: dict[str, set[str]] = defaultdict(set)
    for m in lock["maps"]:
        group_splits[m["geometry_group"]].add(split_of[m["name"]])
    straddlers = {g: s for g, s in group_splits.items() if len(s) != 1}
    assert not straddlers


def test_deterministic(lock: dict) -> None:
    again = build_lock(seed=SPLIT_SEED)
    assert json.dumps(lock, sort_keys=True) == json.dumps(again, sort_keys=True)


def test_lockfile_matches_build(lock: dict) -> None:
    """configs/maps.lock.json on disk was produced by build_lock at SPLIT_SEED."""
    on_disk = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert on_disk["seed"] == SPLIT_SEED
    assert on_disk == lock


def test_size_bucket_boundaries() -> None:
    assert size_bucket(30, 30) == "small"
    assert size_bucket(35, 20) == "small"
    assert size_bucket(36, 20) == "medium"
    assert size_bucket(50, 50) == "medium"
    assert size_bucket(20, 60) == "large"
