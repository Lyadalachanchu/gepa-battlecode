"""Map metadata from raw ``.map26`` flatbuffers (PLAN.md section 12).

A ``.map26`` file is a *raw* (un-gzipped) FlatBuffers buffer whose root table
is ``GameMap`` (name, size, symmetry, randomSeed, walls, dirt, cheese,
cheeseMines, catWaypoint*, initialBodies).  We parse it with the engine's
bundled Python bindings via :mod:`replay.schema_loader` -- no engine JVM is
needed.

The one derived field is ``geometry_group``: a canonical hash of the walls
bitmap under the dihedral transforms (rotations/reflections; for non-square
maps only the transforms that preserve the map's dimensions).  Maps that are
rotated/reflected clones of each other share a group, so the split logic can
keep whole groups on one side of a feedback/pareto/test boundary.

Conventions
-----------
* Flat wall/dirt arrays are indexed ``x + y * width`` (engine convention).
* ``n_initial_rats`` counts initial bodies of type RAT or RAT_KING (official
  maps only place RAT_KINGs and CATs; baby-RAT spawns would be counted too).
* ``symmetry`` is the raw engine int: 0 rotation, 1 horizontal, 2 vertical.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Sequence

from replay.schema_loader import ENGINE_LOCK_PATH, engine_root, load_engine_lock, schema_class

__all__ = [
    "SYMMETRY_NAMES",
    "maps_dir",
    "load_game_map",
    "walls_grid",
    "geometry_group",
    "map_metadata",
    "all_map_metadata",
    "size_bucket",
]

#: GameMap.symmetry int -> human-readable name (schema comment, battlecode.fbs).
SYMMETRY_NAMES = {0: "rotation", 1: "horizontal", 2: "vertical"}

Grid = tuple[tuple[bool, ...], ...]


def maps_dir(lock_path: str | Path = ENGINE_LOCK_PATH) -> Path:
    """Absolute path of the pinned engine's official maps directory."""
    lock = load_engine_lock(lock_path)
    return engine_root(lock_path) / lock.get("maps_dir", "maps")


def load_game_map(path: str | Path) -> Any:
    """Parse a raw ``.map26`` file into a generated ``GameMap`` accessor."""
    buf = Path(path).read_bytes()
    return schema_class("GameMap").GetRootAs(buf, 0)


def walls_grid(game_map: Any) -> Grid:
    """The walls bitmap as a row-major grid: ``grid[y][x]`` (bools)."""
    size = game_map.Size()
    width, height = size.X(), size.Y()
    flat = [bool(game_map.Walls(i)) for i in range(game_map.WallsLength())]
    if len(flat) != width * height:
        raise ValueError(
            f"walls length {len(flat)} != width*height {width * height}"
        )
    return tuple(
        tuple(flat[x + y * width] for x in range(width)) for y in range(height)
    )


def _dihedral_transforms(grid: Grid) -> Iterable[Grid]:
    """Dihedral images of ``grid`` that preserve its dimensions.

    All 8 transforms for square grids; the 4 dimension-preserving ones
    (identity, 180-degree rotation, horizontal flip, vertical flip) otherwise.
    """
    height = len(grid)
    width = len(grid[0]) if height else 0

    def flip_h(g: Grid) -> Grid:  # mirror across the vertical axis
        return tuple(tuple(reversed(row)) for row in g)

    def flip_v(g: Grid) -> Grid:  # mirror across the horizontal axis
        return tuple(reversed(g))

    def transpose(g: Grid) -> Grid:
        return tuple(zip(*g))

    yield grid
    yield flip_h(grid)
    yield flip_v(grid)
    yield flip_h(flip_v(grid))  # 180-degree rotation
    if width == height:
        t = transpose(grid)
        yield t
        yield flip_h(t)  # rotate 90
        yield flip_v(t)  # rotate 270
        yield flip_h(flip_v(t))  # anti-transpose


def _grid_digest(grid: Grid) -> str:
    height = len(grid)
    width = len(grid[0]) if height else 0
    payload = f"{width}x{height}:".encode() + bytes(
        cell for row in grid for cell in row
    )
    return hashlib.sha256(payload).hexdigest()


def geometry_group(grid: Grid) -> str:
    """Canonical geometry hash: min sha256 over the dihedral images of ``grid``.

    Rotated/reflected wall-bitmap clones map to the same group id.
    """
    return min(_grid_digest(g) for g in _dihedral_transforms(grid))


def size_bucket(width: int, height: int) -> str:
    """Size bucket by max dimension: small <=35, medium <=50, large >50."""
    dim = max(width, height)
    if dim <= 35:
        return "small"
    if dim <= 50:
        return "medium"
    return "large"


def map_metadata(path: str | Path) -> dict[str, Any]:
    """Metadata dict for one ``.map26`` file (PLAN.md section 12).

    Keys: name (filename stem), width, height, symmetry, random_seed, n_mines,
    n_cats, n_initial_rats, wall_density, dirt_density, geometry_group.
    """
    path = Path(path)
    gm = load_game_map(path)
    size = gm.Size()
    width, height = size.X(), size.Y()
    n_cells = width * height

    robot_type = schema_class("RobotType")
    n_cats = 0
    n_rats = 0
    bodies = gm.InitialBodies()
    for i in range(bodies.SpawnActionsLength() if bodies is not None else 0):
        rt = bodies.SpawnActions(i).RobotType()
        if rt == robot_type.CAT:
            n_cats += 1
        elif rt in (robot_type.RAT, robot_type.RAT_KING):
            n_rats += 1

    mines = gm.CheeseMines()
    n_mines = mines.XsLength() if mines is not None else 0

    n_walls = sum(bool(gm.Walls(i)) for i in range(gm.WallsLength()))
    n_dirt = sum(bool(gm.Dirt(i)) for i in range(gm.DirtLength()))

    return {
        "name": path.stem,
        "width": width,
        "height": height,
        "symmetry": gm.Symmetry(),
        "random_seed": gm.RandomSeed(),
        "n_mines": n_mines,
        "n_cats": n_cats,
        "n_initial_rats": n_rats,
        "wall_density": round(n_walls / n_cells, 6) if n_cells else 0.0,
        "dirt_density": round(n_dirt / n_cells, 6) if n_cells else 0.0,
        "geometry_group": geometry_group(walls_grid(gm)),
    }


def all_map_metadata(
    directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Metadata for every ``*.map26`` in ``directory`` (default: engine maps),
    sorted by map name."""
    d = Path(directory) if directory is not None else maps_dir()
    paths: Sequence[Path] = sorted(d.glob("*.map26"))
    if not paths:
        raise FileNotFoundError(f"no .map26 files found in {d}")
    return sorted((map_metadata(p) for p in paths), key=lambda m: m["name"])
