"""Tiered deterministic trace builder (PLAN.md sections 6-7).

The model-facing trace is a deterministic, content-independent projection of a
decoded replay with four tiers:

    T0  header block (map, teams, mines, initial spawns, result)
    T1  per-round team aggregate lines, run-length collapsed
    T2  always-kept event lines every round
    T3  full unit-state snapshots at stride rounds AND dense per-round windows
        around decisive events, delta-encoded where trivial

No semantic judgment, no labels, no summarization -- representation only.
Degradation ladder (frozen): stride40 -> windows_only -> shrink_window.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tokens import count_tokens

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENT_YAML = REPO_ROOT / "configs" / "experiment.yaml"

DEGRADE_LADDER = ("stride40", "windows_only", "shrink_window")

# T2 events whose actor/target teams can realize "inter-team contact"
_PLAYER_TEAMS = ("A", "B")


@dataclass(frozen=True)
class TraceConfig:
    snapshot_stride_rounds: int = 20
    event_window_rounds: int = 10
    final_window_rounds: int = 50
    replay_token_budget: int = 250_000
    max_games_per_call: int = 4

    @classmethod
    def from_experiment_yaml(
        cls, path: str | Path = EXPERIMENT_YAML
    ) -> "TraceConfig":
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        t = data["trace"]
        return cls(
            snapshot_stride_rounds=int(t["snapshot_stride_rounds"]),
            event_window_rounds=int(t["event_window_rounds"]),
            final_window_rounds=int(t["final_window_rounds"]),
            replay_token_budget=int(t["replay_token_budget"]),
            max_games_per_call=int(t["max_games_per_call"]),
        )


def degrade(cfg: TraceConfig, level: int) -> TraceConfig:
    """Return cfg degraded to `level` on the frozen ladder.

    level 0: unchanged.  1 (stride40): double the T3 stride (min 40).
    2 (windows_only): drop stride snapshots, keep event/final windows.
    3+ (shrink_window): windows_only plus halve the windows once per level
    beyond 2 (floor 1).
    """
    if level <= 0:
        return dataclasses.replace(cfg)
    if level == 1:
        return dataclasses.replace(
            cfg, snapshot_stride_rounds=max(40, cfg.snapshot_stride_rounds * 2)
        )
    if level == 2:
        return dataclasses.replace(cfg, snapshot_stride_rounds=0)
    shrink = level - 2
    return dataclasses.replace(
        cfg,
        snapshot_stride_rounds=0,
        event_window_rounds=max(1, cfg.event_window_rounds >> shrink),
        final_window_rounds=max(1, cfg.final_window_rounds >> shrink),
    )


MAX_DEGRADE_LEVEL = 8  # windows have shrunk to 1 long before this


# ---------------------------------------------------------------------------
# trace construction
# ---------------------------------------------------------------------------

def _header_lines(decoded: dict[str, Any]) -> list[str]:
    h = decoded["header"]
    f = decoded["footer"]
    lines = [
        f"GAME map={h['map_name']} size={h['width']}x{h['height']}"
        f" sym={h['symmetry']} seed={h['random_seed']}"
        f" maxRounds={h['max_rounds']}",
        f"TEAM A={h['teams'].get('A')}",
        f"TEAM B={h['teams'].get('B')}",
    ]
    if h["mines"]:
        lines.append(
            "MINES " + " ".join(f"({x},{y})" for x, y in h["mines"])
        )
    for b in h["initial_bodies"]:
        lines.append(
            f"INIT #{b['id']} {b['team']} {b['type']} @({b['x']},{b['y']})"
        )
    lines.append(
        f"RESULT winner={f['winner']} winType={f['win_type']}"
        f" rounds={f['total_rounds']}"
    )
    return lines


def _agg_content(stats: dict[str, dict[str, int]]) -> str:
    parts = []
    for team in _PLAYER_TEAMS:
        s = stats.get(team)
        if s is None:
            continue
        parts.append(
            f"{team}[c{s['cheese_transferred']} d{s['cat_damage']}"
            f" k{s['alive_rat_kings']} r{s['alive_baby_rats']}"
            f" t{s['rat_traps']}/{s['cat_traps']} w{s['dirt']}]"
        )
    return " ".join(parts)


def _aggregate_lines(decoded: dict[str, Any]) -> list[str]:
    """T1: one line per round, consecutive identical contents collapsed."""
    lines: list[str] = []
    run_start = run_end = None
    run_content = None
    for rnd in decoded["rounds"]:
        content = _agg_content(rnd["team_stats"])
        r = rnd["round"]
        if content == run_content and run_end == r - 1:
            run_end = r
            continue
        if run_content is not None:
            span = f"r{run_start}" if run_start == run_end else f"r{run_start}-{run_end}"
            lines.append(f"{span} {run_content}")
        run_start = run_end = r
        run_content = content
    if run_content is not None:
        span = f"r{run_start}" if run_start == run_end else f"r{run_start}-{run_end}"
        lines.append(f"{span} {run_content}")
    return lines


class _EventPass:
    """One pass over rounds collecting T2 event lines + decisive rounds."""

    def __init__(self, decoded: dict[str, Any]) -> None:
        self.lines: list[tuple[int, str]] = []  # (round, line)
        self.decisive: set[int] = set()
        self._id_team: dict[int, str] = {}
        self._id_type: dict[int, str] = {}
        for b in decoded["header"]["initial_bodies"]:
            self._id_team[b["id"]] = b["team"]
            self._id_type[b["id"]] = b["type"]
        self._coop: dict[str, bool | None] = {"A": None, "B": None}
        self._first_contact_seen = False
        self._run(decoded)

    def _team_of(self, rid: int) -> str:
        return self._id_team.get(rid, "?")

    def _contact(self, r: int, t1: str, t2: str) -> None:
        if (
            not self._first_contact_seen
            and t1 in _PLAYER_TEAMS
            and t2 in _PLAYER_TEAMS
            and t1 != t2
        ):
            self._first_contact_seen = True
            self.decisive.add(r)

    def _run(self, decoded: dict[str, Any]) -> None:
        for rnd in decoded["rounds"]:
            r = rnd["round"]
            coop_seen: dict[str, bool] = {}
            for turn in rnd["turns"]:
                actor = turn["id"]
                ateam = turn["team"]
                self._id_team.setdefault(actor, ateam)
                self._id_type.setdefault(actor, turn["type"])
                if ateam in _PLAYER_TEAMS and ateam not in coop_seen:
                    coop_seen[ateam] = turn["is_cooperation"]
                for a in turn["actions"]:
                    self._one_action(r, actor, ateam, a)
            for rid in rnd["died_ids"]:
                team = self._team_of(rid)
                self.lines.append((r, f"DIE #{rid} {team} EOR"))
                if self._id_type.get(rid) == "RAT_KING":
                    self.decisive.add(r)
            for team, coop in coop_seen.items():
                prev = self._coop[team]
                if prev is True and coop is False:
                    self.lines.append((r, f"BACKSTAB {team}"))
                    self.decisive.add(r)
                self._coop[team] = coop

    def _one_action(self, r: int, actor: int, ateam: str, a: dict) -> None:
        t = a["type"]
        if t == "SpawnAction":
            self._id_team[a["spawn_id"]] = a["team"]
            self._id_type[a["spawn_id"]] = a["robot_type"]
            self.lines.append(
                (r, f"SPAWN #{a['spawn_id']} {a['team']} {a['robot_type']}"
                    f" @({a['x']},{a['y']})")
            )
        elif t == "DieAction":
            tid = a["target_id"]
            team = self._team_of(tid)
            exc = " EXCEPTION" if a["die_type"] == "EXCEPTION" else ""
            self.lines.append((r, f"DIE #{tid} {team}{exc}"))
            if self._id_type.get(tid) == "RAT_KING":
                self.decisive.add(r)
        elif t == "UpgradeToRatKing":
            self._id_type[actor] = "RAT_KING"
            self.lines.append((r, f"KING #{actor} {ateam}"))
        elif t == "PlaceTrap":
            kind = "RAT" if a["is_rat_trap"] else "CAT"
            self.lines.append(
                (r, f"TRAP+ {a['team']} {kind} @({a['x']},{a['y']})")
            )
        elif t == "TriggerTrap":
            self.lines.append(
                (r, f"TRAP! {a['team']} @({a['x']},{a['y']})"
                    f" by#{actor}{ateam}")
            )
            self._contact(r, a["team"], ateam)
        elif t == "RatNap":
            tid = a["target_id"]
            tteam = self._team_of(tid)
            self.lines.append((r, f"NAP #{actor}{ateam}->#{tid}{tteam}"))
            self._contact(r, ateam, tteam)
        elif t == "ThrowRat":
            tid = a["target_id"]
            tteam = self._team_of(tid)
            self.lines.append(
                (r, f"THROW #{actor}{ateam}->#{tid}{tteam}"
                    f" @({a['x']},{a['y']})")
            )
            self._contact(r, ateam, tteam)
        elif t == "CatFeed":
            tid = a["target_id"]
            self.lines.append(
                (r, f"CATFEED #{actor}->#{tid}{self._team_of(tid)}")
            )
        elif t == "CatPounce":
            self.lines.append(
                (r, f"POUNCE #{actor} ({a['from_x']},{a['from_y']})"
                    f"->({a['to_x']},{a['to_y']})")
            )
        elif t == "RatSqueak":
            self.lines.append(
                (r, f"SQUEAK #{actor}{ateam} @({a['x']},{a['y']})")
            )


def _snapshot_rounds(
    decoded: dict[str, Any], cfg: TraceConfig, decisive: set[int]
) -> list[int]:
    rounds = [rnd["round"] for rnd in decoded["rounds"]]
    if not rounds:
        return []
    last = rounds[-1]
    keep: set[int] = set()
    stride = cfg.snapshot_stride_rounds
    if stride > 0:
        keep.update(r for r in rounds if r % stride == 0)
    w = cfg.event_window_rounds
    for d in decisive:
        keep.update(range(d - w, d + w + 1))
    keep.update(range(last - cfg.final_window_rounds + 1, last + 1))
    present = set(rounds)
    return sorted(keep & present)


def _unit_state(turn: dict[str, Any]) -> tuple:
    return (turn["x"], turn["y"], turn["dir"], turn["health"], turn["cheese"])


def _fmt_unit(uid: int, st: tuple) -> str:
    x, y, d, h, c = st
    return f"#{uid}({x},{y})d{d}h{h}c{c}"


def _snapshot_lines(
    decoded: dict[str, Any], cfg: TraceConfig, decisive: set[int]
) -> list[str]:
    """T3: full snapshots at stride/window rounds, delta-encoded where the
    previous emitted snapshot is the immediately preceding round."""
    wanted = set(_snapshot_rounds(decoded, cfg, decisive))
    lines: list[str] = []
    prev_round: int | None = None
    prev_state: dict[int, tuple] = {}
    for rnd in decoded["rounds"]:
        r = rnd["round"]
        if r not in wanted:
            continue
        state: dict[int, tuple] = {}
        team_of: dict[int, str] = {}
        for turn in rnd["turns"]:
            state[turn["id"]] = _unit_state(turn)
            team_of[turn["id"]] = turn["team"]
        if prev_round == r - 1:
            changed = [
                _fmt_unit(uid, st)
                for uid, st in sorted(state.items())
                if prev_state.get(uid) != st
            ]
            gone = [f"-#{uid}" for uid in sorted(prev_state) if uid not in state]
            body = " ".join(changed + gone) or "="
            lines.append(f"r{r} DSNP {body}")
        else:
            parts = []
            for team in ("A", "B", "CAT"):
                units = [
                    _fmt_unit(uid, st)
                    for uid, st in sorted(state.items())
                    if team_of[uid] == team
                ]
                if units:
                    parts.append(f"{team}: " + " ".join(units))
            lines.append(f"r{r} SNAP " + " | ".join(parts))
        prev_round = r
        prev_state = state
    return lines


def build_trace(decoded: dict[str, Any], cfg: TraceConfig) -> str:
    """Build the tiered deterministic trace text for one decoded game."""
    ep = _EventPass(decoded)
    out: list[str] = []
    out.extend(_header_lines(decoded))
    out.append("T1 AGGREGATES")
    out.extend(_aggregate_lines(decoded))
    out.append("T2 EVENTS")
    out.extend(f"r{r} {line}" for r, line in ep.lines)
    out.append("T3 SNAPSHOTS")
    out.extend(_snapshot_lines(decoded, cfg, ep.decisive))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# packing
# ---------------------------------------------------------------------------

_GAME_SEP = "\n\n"


def pack_traces(
    traces: list[str],
    budget_tokens: int,
    max_games: int,
    decoded_games: list[dict[str, Any]] | None = None,
    base_cfg: TraceConfig | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Pack complete traces in order until the token budget is hit.

    Adds whole traces (never truncates mid-game).  If the next trace does not
    fit and its decoded game + base config are supplied, it is rebuilt at
    increasing degrade-ladder levels until it fits; packing stops at the first
    game that cannot be made to fit.  Returns (packed_text, manifest); the
    manifest has one entry per considered trace:
    {index, included, tokens, degrade_level, reason}.
    """
    packed_parts: list[str] = []
    manifest: list[dict[str, Any]] = []
    packed_text = ""
    for i, trace in enumerate(traces):
        if len(packed_parts) >= max_games:
            manifest.append(
                {"index": i, "included": False, "tokens": None,
                 "degrade_level": None, "reason": "max_games"}
            )
            continue
        chosen: str | None = None
        level_used: int | None = None
        candidate = trace
        level = 0
        while True:
            tentative = (
                packed_text + _GAME_SEP + candidate if packed_parts else candidate
            )
            if count_tokens(tentative) <= budget_tokens:
                chosen = candidate
                level_used = level
                packed_text = tentative
                break
            if (
                decoded_games is None
                or base_cfg is None
                or i >= len(decoded_games)
                or level >= MAX_DEGRADE_LEVEL
            ):
                break
            level += 1
            candidate = build_trace(decoded_games[i], degrade(base_cfg, level))
        if chosen is not None:
            packed_parts.append(chosen)
            manifest.append(
                {"index": i, "included": True,
                 "tokens": count_tokens(chosen),
                 "degrade_level": level_used, "reason": "ok"}
            )
        else:
            manifest.append(
                {"index": i, "included": False,
                 "tokens": count_tokens(trace),
                 "degrade_level": None, "reason": "over_budget"}
            )
            break  # sampler order: stop at first game that cannot fit
    return packed_text, manifest
