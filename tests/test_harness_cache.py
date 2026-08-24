"""Unit tests for harness.cache (pure: tmp dirs and synthetic sources only)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.cache import MatchCache, MatchCacheKey, source_tree_hash


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _make_tree(root: Path) -> None:
    _write(root, "RobotPlayer.java", "public class RobotPlayer {}\n")
    _write(root, "sub/Econ.java", "class Econ { int x = 1; }\n")
    _write(root, "notes.txt", "not java, ignored\n")


class TestSourceTreeHash:
    def test_deterministic_across_copies(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        _make_tree(a)
        _make_tree(b)
        assert source_tree_hash(a) == source_tree_hash(b)

    def test_repeat_call_stable(self, tmp_path):
        _make_tree(tmp_path)
        assert source_tree_hash(tmp_path) == source_tree_hash(tmp_path)

    def test_content_change_changes_hash(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        _make_tree(a)
        _make_tree(b)
        _write(b, "sub/Econ.java", "class Econ { int x = 2; }\n")
        assert source_tree_hash(a) != source_tree_hash(b)

    def test_relpath_change_changes_hash(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        _make_tree(a)
        _write(b, "RobotPlayer.java", "public class RobotPlayer {}\n")
        _write(b, "other/Econ.java", "class Econ { int x = 1; }\n")
        assert source_tree_hash(a) != source_tree_hash(b)

    def test_non_java_files_ignored(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        _make_tree(a)
        _make_tree(b)
        _write(b, "notes.txt", "different non-java content\n")
        _write(b, "extra.md", "also ignored\n")
        assert source_tree_hash(a) == source_tree_hash(b)

    def test_extra_java_file_changes_hash(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        _make_tree(a)
        _make_tree(b)
        _write(b, "Extra.java", "class Extra {}\n")
        assert source_tree_hash(a) != source_tree_hash(b)

    def test_rejects_non_directory(self, tmp_path):
        f = tmp_path / "x.java"
        f.write_text("class X {}\n")
        with pytest.raises(ValueError):
            source_tree_hash(f)


def _key(**overrides) -> MatchCacheKey:
    base = dict(
        engine_commit="103abf6b67a2cf544e6344dddef9318af9ae9193",
        bot_a_hash="a" * 64,
        bot_b_hash="b" * 64,
        map_name="DefaultSmall",
        side="AB",
        config_hash="c" * 64,
    )
    base.update(overrides)
    return MatchCacheKey(**base)


class TestMatchCache:
    def test_miss_returns_none(self, tmp_path):
        cache = MatchCache(tmp_path / "cache")
        assert cache.get(_key()) is None

    def test_put_get_roundtrip(self, tmp_path):
        cache = MatchCache(tmp_path / "cache")
        replay = tmp_path / "match.bc26"
        replay.write_bytes(b"\x1f\x8b fake gzip payload")
        result = {"winner": "A", "win_type": "RATKING_DESTROYED", "rounds": 1310}

        stored = cache.put(_key(), result, replay)
        got = cache.get(_key())

        assert got is not None
        assert got == stored
        for k, v in result.items():
            assert got[k] == v
        cached_replay = Path(got["replay_path"])
        assert cached_replay.exists()
        assert cached_replay.read_bytes() == replay.read_bytes()
        # the cached copy is inside the cache root, not the original file
        assert (tmp_path / "cache") in cached_replay.parents
        assert cached_replay != replay

    def test_same_key_same_result(self, tmp_path):
        cache = MatchCache(tmp_path / "cache")
        replay = tmp_path / "match.bc26"
        replay.write_bytes(b"payload")
        cache.put(_key(), {"score": 1.0}, replay)
        assert cache.get(_key()) == cache.get(_key())

    def test_source_replay_deleted_after_put(self, tmp_path):
        cache = MatchCache(tmp_path / "cache")
        replay = tmp_path / "match.bc26"
        replay.write_bytes(b"payload")
        cache.put(_key(), {"score": 1.0}, replay)
        replay.unlink()  # cache must not depend on the source file
        got = cache.get(_key())
        assert got is not None
        assert Path(got["replay_path"]).read_bytes() == b"payload"

    @pytest.mark.parametrize(
        "field,value",
        [
            ("engine_commit", "deadbeef"),
            ("bot_a_hash", "d" * 64),
            ("bot_b_hash", "e" * 64),
            ("map_name", "DefaultMedium"),
            ("side", "BA"),
            ("config_hash", "f" * 64),
        ],
    )
    def test_any_field_change_is_a_miss(self, tmp_path, field, value):
        cache = MatchCache(tmp_path / "cache")
        replay = tmp_path / "match.bc26"
        replay.write_bytes(b"payload")
        cache.put(_key(), {"score": 1.0}, replay)
        assert cache.get(_key(**{field: value})) is None

    def test_key_digest_deterministic(self):
        assert _key().digest() == _key().digest()
        assert _key().digest() != _key(side="BA").digest()

    def test_put_rejects_missing_or_empty_replay(self, tmp_path):
        cache = MatchCache(tmp_path / "cache")
        with pytest.raises(ValueError):
            cache.put(_key(), {}, tmp_path / "nope.bc26")
        empty = tmp_path / "empty.bc26"
        empty.write_bytes(b"")
        with pytest.raises(ValueError):
            cache.put(_key(), {}, empty)

    def test_persistence_across_instances(self, tmp_path):
        root = tmp_path / "cache"
        replay = tmp_path / "match.bc26"
        replay.write_bytes(b"payload")
        MatchCache(root).put(_key(), {"score": 0.5}, replay)
        got = MatchCache(root).get(_key())
        assert got is not None and got["score"] == 0.5
