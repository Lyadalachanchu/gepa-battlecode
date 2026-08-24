"""Resolve the pinned engine's FlatBuffers Python bindings and import them.

The engine location and the relative path of the generated Python bindings are
frozen in configs/engine.lock.json.  This module inserts the bindings directory
into sys.path (idempotently) and exposes small helpers to import modules from
the generated ``battlecode.schema`` package.

NOTE: the generated python dir contains a few stale leftover classes (e.g.
AttackAction, BuildAction) that are not in the current Action union declared in
schema/battlecode.fbs.  Only import what the .fbs declares.
"""
from __future__ import annotations

import importlib
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_LOCK_PATH = REPO_ROOT / "configs" / "engine.lock.json"


@lru_cache(maxsize=None)
def load_engine_lock(lock_path: str | Path = ENGINE_LOCK_PATH) -> dict:
    """Load and cache configs/engine.lock.json as a dict."""
    with open(lock_path, "r", encoding="utf-8") as f:
        return json.load(f)


def engine_root(lock_path: str | Path = ENGINE_LOCK_PATH) -> Path:
    """Absolute path of the pinned engine clone."""
    return Path(load_engine_lock(lock_path)["local_path"])


def schema_python_dir(lock_path: str | Path = ENGINE_LOCK_PATH) -> Path:
    """Absolute path of the generated Python bindings directory."""
    lock = load_engine_lock(lock_path)
    return Path(lock["local_path"]) / lock["schema_python_path"]


def ensure_schema_on_path(lock_path: str | Path = ENGINE_LOCK_PATH) -> Path:
    """Insert the bindings directory into sys.path (idempotent).

    Returns the directory that was ensured on the path.
    """
    d = schema_python_dir(lock_path)
    if not d.is_dir():
        raise FileNotFoundError(
            f"schema python dir not found: {d} (check configs/engine.lock.json)"
        )
    s = str(d)
    if s not in sys.path:
        sys.path.insert(0, s)
    return d


def import_schema_module(name: str) -> Any:
    """Import ``battlecode.schema.<name>`` (e.g. 'Round', 'GameWrapper')."""
    ensure_schema_on_path()
    return importlib.import_module(f"battlecode.schema.{name}")


@lru_cache(maxsize=None)
def schema_class(name: str) -> Any:
    """Return the class/enum-holder named ``name`` from its schema module.

    Generated modules each contain one same-named class (e.g. Round.Round).
    """
    return getattr(import_schema_module(name), name)


def enum_name_map(enum_holder_name: str) -> dict[int, str]:
    """Map enum value -> enum member name for a generated enum class."""
    cls = schema_class(enum_holder_name)
    return {
        v: k
        for k, v in vars(cls).items()
        if not k.startswith("_") and isinstance(v, int)
    }
