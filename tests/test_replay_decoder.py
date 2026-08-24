"""Tests for replay.decoder against the smoke fixture (no network)."""
from pathlib import Path

import pytest

from replay import decode_footer, decode_match

FIXTURE = Path(__file__).parent / "fixtures" / "smoke.bc26"

WIN_TYPE_NAMES = {
    "RESIGNATION", "RATKING_DESTROYED", "BACKSTAB_RATKING_DESTROYED",
    "MORE_POINTS", "MORE_ROBOTS", "MORE_CHEESE", "TIE", "COIN_FLIP",
}

TEAM_STAT_KEYS = {
    "cheese_transferred", "cat_damage", "alive_rat_kings",
    "alive_baby_rats", "rat_traps", "cat_traps", "dirt",
}


@pytest.fixture(scope="module")
def decoded():
    return decode_match(FIXTURE)


def test_footer_valid(decoded):
    f = decoded["footer"]
    assert f["winner"] in ("A", "B")
    assert f["win_type"] in WIN_TYPE_NAMES
    assert f["total_rounds"] > 0


def test_round_count_plausible(decoded):
    total = decoded["footer"]["total_rounds"]
    assert len(decoded["rounds"]) == total
    assert 0 < total <= decoded["header"]["max_rounds"]
    # rounds are 1..total in order
    assert decoded["rounds"][0]["round"] == 1
    assert decoded["rounds"][-1]["round"] == total
    assert all(
        decoded["rounds"][i]["round"] == i + 1
        for i in range(len(decoded["rounds"]))
    )


def test_header_fields(decoded):
    h = decoded["header"]
    assert h["map_name"] == "DefaultSmall"
    assert h["width"] > 0 and h["height"] > 0
    assert h["symmetry"] in (0, 1, 2)
    assert isinstance(h["random_seed"], int)
    assert h["max_rounds"] > 0
    assert isinstance(h["mines"], list) and len(h["mines"]) > 0
    for x, y in h["mines"]:
        assert 0 <= x < h["width"] and 0 <= y < h["height"]
    assert set(h["teams"].keys()) == {"A", "B"}
    assert all(isinstance(v, str) for v in h["teams"].values())
    assert len(h["initial_bodies"]) > 0
    teams_seen = {b["team"] for b in h["initial_bodies"]}
    assert {"A", "B"} <= teams_seen  # both player teams start with bodies
    for b in h["initial_bodies"]:
        assert set(b) == {"id", "team", "type", "x", "y"}
        assert b["team"] in ("A", "B", "CAT")


def test_cats_are_neutral_team(decoded):
    cats = [
        b for b in decoded["header"]["initial_bodies"] if b["type"] == "CAT"
    ]
    assert cats, "fixture map has cats"
    assert all(b["team"] == "CAT" for b in cats)


def test_team_stats_present_every_round(decoded):
    for rnd in decoded["rounds"]:
        stats = rnd["team_stats"]
        assert set(stats.keys()) == {"A", "B"}
        for team_stats in stats.values():
            assert set(team_stats.keys()) == TEAM_STAT_KEYS
            assert all(isinstance(v, int) for v in team_stats.values())


def test_turns_shape(decoded):
    h = decoded["header"]
    turn_keys = {
        "id", "team", "type", "x", "y", "dir", "health", "cheese",
        "bytecodes_used", "is_cooperation", "actions",
    }
    n_turns = 0
    for rnd in decoded["rounds"]:
        assert isinstance(rnd["died_ids"], list)
        for turn in rnd["turns"]:
            n_turns += 1
            assert set(turn.keys()) == turn_keys
            assert turn["team"] in ("A", "B", "CAT")
            assert turn["type"] in ("RAT", "RAT_KING", "CAT", "NONE")
            assert 0 <= turn["x"] < h["width"]
            assert 0 <= turn["y"] < h["height"]
            assert isinstance(turn["is_cooperation"], bool)
    assert n_turns > 0


def test_no_indicator_actions(decoded):
    for rnd in decoded["rounds"]:
        for turn in rnd["turns"]:
            for a in turn["actions"]:
                assert not a["type"].startswith("Indicator")
                assert a["type"] != "NONE"


def test_decode_footer_matches_decode_match(decoded):
    f = decode_footer(FIXTURE)
    assert f["winner"] == decoded["footer"]["winner"]
    assert f["win_type"] == decoded["footer"]["win_type"]
    assert f["total_rounds"] == decoded["footer"]["total_rounds"]
    assert f["final_team_stats"] == decoded["rounds"][-1]["team_stats"]
    assert set(f["final_team_stats"].keys()) == {"A", "B"}


def test_decode_is_deterministic(decoded):
    assert decode_match(FIXTURE) == decoded
