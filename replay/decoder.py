"""Decode .bc26 replay files into plain-Python dicts.

A .bc26 replay is a GZIP-compressed GameWrapper flatbuffer.  The event stream
is: GameHeader, MatchHeader, Round*, MatchFooter, GameFooter.  This module
decodes exactly what the replay contains -- no game-logic reimplementation,
no judgments.

Public contract (other packages import EXACTLY these):
    decode_match(path)  -> {"header": {...}, "rounds": [...], "footer": {...}}
    decode_footer(path) -> {"winner", "win_type", "total_rounds",
                            "final_team_stats"}

Team letters come from GameHeader TeamData order: first TeamData -> "A",
second -> "B".  Cats belong to the neutral team (team id 0) -> "CAT".
Indicator* actions are always skipped (the harness disables them; we skip
defensively regardless).

Engine quirk (verified at the pinned commit, GameWorld.java ~line 1013): the
replay's per-round ``teamAliveRatKings`` aggregate actually carries the
combined stat ``numRatKings + 10 * teamCheese``.  We decode the raw replay
value into ``alive_rat_kings`` without reinterpretation.
"""
from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any, Callable

from . import schema_loader

TEAM_STAT_KEYS = (
    "cheese_transferred",
    "cat_damage",
    "alive_rat_kings",
    "alive_baby_rats",
    "rat_traps",
    "cat_traps",
    "dirt",
)

_SKIPPED_ACTIONS = frozenset(
    {"IndicatorStringAction", "IndicatorDotAction", "IndicatorLineAction"}
)


def _loc_xy(loc: int, width: int) -> tuple[int, int]:
    """Engine encoding: loc = x + width * y (GameMaker.locationToInt)."""
    return loc % width, loc // width


class _SchemaCtx:
    """Lazily-imported schema classes + enum maps, shared per decode call."""

    def __init__(self) -> None:
        sc = schema_loader.schema_class
        self.GameWrapper = sc("GameWrapper")
        self.GameHeader = sc("GameHeader")
        self.MatchHeader = sc("MatchHeader")
        self.MatchFooter = sc("MatchFooter")
        self.Round = sc("Round")
        self.Event = sc("Event")
        self.Action = sc("Action")
        self.action_names = schema_loader.enum_name_map("Action")
        self.win_type_names = schema_loader.enum_name_map("WinType")
        self.die_type_names = schema_loader.enum_name_map("DieType")
        self.robot_type_names = schema_loader.enum_name_map("RobotType")
        # action struct classes, keyed by union member name
        self.action_cls = {
            name: sc(name)
            for name in self.action_names.values()
            if name not in ("NONE", "IndicatorDotAction", "IndicatorLineAction")
            and name != "IndicatorStringAction"
        }


def _init_at(cls: Any, tab: Any) -> Any:
    """Instantiate a generated flatbuffers struct/table at a union Table pos."""
    obj = cls()
    obj.Init(tab.Bytes, tab.Pos)
    return obj


def _team_letter(team_id: int, letters: dict[int, str]) -> str:
    return letters.get(team_id, "CAT")


def _action_fields(
    ctx: _SchemaCtx, name: str, obj: Any, width: int, letters: dict[int, str]
) -> dict[str, Any]:
    """Extract the action-specific fields for one decoded union member."""
    out: dict[str, Any] = {"type": name}
    if name in ("CatFeed", "RatAttack", "RatNap"):
        out["target_id"] = obj.Id()
    elif name in ("RatCollision", "PlaceDirt", "BreakDirt", "CheesePickup",
                  "CatScratch", "RatSqueak"):
        out["x"], out["y"] = _loc_xy(obj.Loc(), width)
    elif name == "CheeseSpawn":
        out["x"], out["y"] = _loc_xy(obj.Loc(), width)
        out["amount"] = obj.Amount()
    elif name == "CheeseTransfer":
        out["target_id"] = obj.Id()
        out["amount"] = obj.Amount()
    elif name == "CatPounce":
        out["from_x"], out["from_y"] = _loc_xy(obj.StartLoc(), width)
        out["to_x"], out["to_y"] = _loc_xy(obj.EndLoc(), width)
    elif name == "PlaceTrap":
        out["x"], out["y"] = _loc_xy(obj.Loc(), width)
        out["team"] = _team_letter(obj.Team(), letters)
        out["is_rat_trap"] = bool(obj.IsRatTrapType())
    elif name in ("RemoveTrap", "TriggerTrap"):
        out["x"], out["y"] = _loc_xy(obj.Loc(), width)
        out["team"] = _team_letter(obj.Team(), letters)
    elif name == "ThrowRat":
        out["target_id"] = obj.Id()
        out["x"], out["y"] = _loc_xy(obj.Loc(), width)
    elif name == "UpgradeToRatKing":
        pass  # phantom-only struct
    elif name == "DamageAction":
        out["target_id"] = obj.Id()
        out["damage"] = obj.Damage()
    elif name == "StunAction":
        out["target_id"] = obj.Id()
        out["cooldown"] = obj.Cooldown()
    elif name == "SpawnAction":
        out["spawn_id"] = obj.Id()
        out["x"], out["y"] = obj.X(), obj.Y()
        out["dir"] = obj.Dir()
        out["team"] = _team_letter(obj.Team(), letters)
        out["robot_type"] = ctx.robot_type_names.get(obj.RobotType(), "NONE")
    elif name == "DieAction":
        out["target_id"] = obj.Id()
        out["die_type"] = ctx.die_type_names.get(obj.DieType(), "UNKNOWN")
    return out


def _read_events(path: str | Path, ctx: _SchemaCtx) -> Any:
    with gzip.open(path, "rb") as f:
        buf = f.read()
    return ctx.GameWrapper.GetRootAs(buf, 0)


def decode_match(path: str | Path) -> dict[str, Any]:
    """Decode one full match from a .bc26 replay into plain dicts."""
    ctx = _SchemaCtx()
    gw = _read_events(path, ctx)
    ev_enum = ctx.Event

    # --- GameHeader: team letter mapping from TeamData order/ids ---
    letters: dict[int, str] = {}
    team_names: dict[str, str] = {"A": None, "B": None}
    header_tab = None
    for i in range(gw.EventsLength()):
        w = gw.Events(i)
        if w.EType() == ev_enum.GameHeader:
            header_tab = w.E()
            break
    if header_tab is None:
        raise ValueError(f"no GameHeader in replay: {path}")
    gh = _init_at(ctx.GameHeader, header_tab)
    letter_order = "AB"
    for i in range(gh.TeamsLength()):
        td = gh.Teams(i)
        if i < len(letter_order):
            letter = letter_order[i]
            letters[td.TeamId()] = letter
            name = td.Name()
            team_names[letter] = name.decode("utf-8") if name else None
    letters.setdefault(0, "CAT")

    # --- MatchHeader (first match only; harness runs one map per JVM) ---
    if gw.MatchHeadersLength() < 1:
        raise ValueError(f"no MatchHeader in replay: {path}")
    mh = _init_at(ctx.MatchHeader, gw.Events(gw.MatchHeaders(0)).E())
    gm = mh.Map()
    size = gm.Size()
    width, height = size.X(), size.Y()
    map_name = gm.Name()
    mines: list[list[int]] = []
    cm = gm.CheeseMines()
    if cm is not None:
        for i in range(cm.XsLength()):
            mines.append([cm.Xs(i), cm.Ys(i)])

    # id -> (team letter, robot type name); seeded from initial bodies
    robot_team: dict[int, str] = {}
    robot_type: dict[int, str] = {}
    initial_bodies: list[dict[str, Any]] = []
    ib = gm.InitialBodies()
    if ib is not None:
        for i in range(ib.SpawnActionsLength()):
            sa = ib.SpawnActions(i)
            rid = sa.Id()
            team = _team_letter(sa.Team(), letters)
            rtype = ctx.robot_type_names.get(sa.RobotType(), "NONE")
            robot_team[rid] = team
            robot_type[rid] = rtype
            initial_bodies.append(
                {"id": rid, "team": team, "type": rtype,
                 "x": sa.X(), "y": sa.Y()}
            )

    header = {
        "map_name": map_name.decode("utf-8") if map_name else None,
        "width": width,
        "height": height,
        "symmetry": gm.Symmetry(),
        "random_seed": gm.RandomSeed(),
        "max_rounds": mh.MaxRounds(),
        "mines": mines,
        "initial_bodies": initial_bodies,
        "teams": team_names,
    }

    # --- Rounds ---
    rounds: list[dict[str, Any]] = []
    for i in range(gw.EventsLength()):
        w = gw.Events(i)
        if w.EType() != ev_enum.Round:
            continue
        rnd = _init_at(ctx.Round, w.E())
        n_teams = rnd.TeamIdsLength()
        team_stats: dict[str, dict[str, int]] = {}
        for t in range(n_teams):
            letter = _team_letter(rnd.TeamIds(t), letters)
            team_stats[letter] = {
                "cheese_transferred": rnd.TeamCheeseTransferred(t),
                "cat_damage": rnd.TeamCatDamage(t),
                "alive_rat_kings": rnd.TeamAliveRatKings(t),
                "alive_baby_rats": rnd.TeamAliveBabyRats(t),
                "rat_traps": rnd.TeamRatTrapCount(t),
                "cat_traps": rnd.TeamCatTrapCount(t),
                "dirt": rnd.TeamDirtAmounts(t),
            }
        turns: list[dict[str, Any]] = []
        for j in range(rnd.TurnsLength()):
            tu = rnd.Turns(j)
            rid = tu.RobotId()
            actions: list[dict[str, Any]] = []
            n_actions = tu.ActionsLength()
            for k in range(n_actions):
                at = tu.ActionsType(k)
                name = ctx.action_names.get(at)
                if name is None or name == "NONE" or name in _SKIPPED_ACTIONS:
                    continue
                tab = tu.Actions(k)
                if tab is None:
                    continue
                obj = _init_at(ctx.action_cls[name], tab)
                a = _action_fields(ctx, name, obj, width, letters)
                actions.append(a)
                # keep the id -> team/type maps current
                if name == "SpawnAction":
                    robot_team[a["spawn_id"]] = a["team"]
                    robot_type[a["spawn_id"]] = a["robot_type"]
                elif name == "UpgradeToRatKing":
                    robot_type[rid] = "RAT_KING"
            turns.append(
                {
                    "id": rid,
                    "team": robot_team.get(rid, "CAT"),
                    "type": robot_type.get(rid, "NONE"),
                    "x": tu.X(),
                    "y": tu.Y(),
                    "dir": tu.Dir(),
                    "health": tu.Health(),
                    "cheese": tu.Cheese(),
                    "bytecodes_used": tu.BytecodesUsed(),
                    "is_cooperation": bool(tu.IsCooperation()),
                    "actions": actions,
                }
            )
        rounds.append(
            {
                "round": rnd.RoundId(),
                "team_stats": team_stats,
                "died_ids": [rnd.DiedIds(d) for d in range(rnd.DiedIdsLength())],
                "turns": turns,
            }
        )

    # --- MatchFooter ---
    if gw.MatchFootersLength() < 1:
        raise ValueError(f"no MatchFooter in replay: {path}")
    mf = _init_at(ctx.MatchFooter, gw.Events(gw.MatchFooters(0)).E())
    footer = {
        "winner": _team_letter(mf.Winner(), letters),
        "win_type": ctx.win_type_names.get(mf.WinType(), "UNKNOWN"),
        "total_rounds": mf.TotalRounds(),
    }

    return {"header": header, "rounds": rounds, "footer": footer}


def decode_footer(path: str | Path) -> dict[str, Any]:
    """Decode just the match outcome (+ final-round per-team aggregates).

    May decode the full file internally; the API is kept separate so callers
    that only need scores never depend on the full-round structure.
    """
    decoded = decode_match(path)
    if decoded["rounds"]:
        final_stats = decoded["rounds"][-1]["team_stats"]
    else:
        zeros = {k: 0 for k in TEAM_STAT_KEYS}
        final_stats = {"A": dict(zeros), "B": dict(zeros)}
    f = decoded["footer"]
    return {
        "winner": f["winner"],
        "win_type": f["win_type"],
        "total_rounds": f["total_rounds"],
        "final_team_stats": final_stats,
    }
