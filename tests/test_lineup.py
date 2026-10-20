"""Tests for league-layout handling and season detection.

Both exist because the original code hardcoded one league's shape and one
season, which would silently produce wrong output for anyone else's league.
"""

import contextlib
import io
import unittest

import tests  # noqa: F401 - ensures src/ is on sys.path

from nodes import (
    DEFAULT_ROSTER_RULES,
    DEFAULT_SLOT_LAYOUT,
    _fetch_roster_rules,
    build_slot_map,
    optimize_roster_node,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc

    def get(self, *args, **kwargs):
        if self._exc:
            raise self._exc
        return _FakeResponse(self._payload)


class _FakeQuery:
    def __init__(self, session):
        self.oauth = type("OAuth", (), {"session": session})()


def league_payload(max_adds, positions):
    return {
        "fantasy_content": {
            "league": [
                {
                    "settings": [
                        {
                            "max_weekly_adds": max_adds,
                            "roster_positions": [
                                {"roster_position": {"position": p, "count": c}}
                                for p, c in positions
                            ],
                        }
                    ]
                }
            ]
        }
    }


class TestSlotLayoutFromSettings(unittest.TestCase):
    def test_captures_each_position_separately(self):
        query = _FakeQuery(
            _FakeSession(league_payload(7, [
                ("PG", 1), ("SG", 1), ("G", 1), ("SF", 1), ("PF", 1),
                ("F", 1), ("C", 2), ("Util", 2), ("BN", 3), ("IL", 2), ("IL+", 1),
            ]))
        )
        rules = _fetch_roster_rules(query, "466.l.1")
        layout = {e["position"]: e["count"] for e in rules["active_slots"]}
        self.assertEqual(layout, {
            "PG": 1, "SG": 1, "G": 1, "SF": 1, "PF": 1, "F": 1, "C": 2, "Util": 2,
        })

    def test_bench_is_capacity_but_not_an_assignable_slot(self):
        query = _FakeQuery(_FakeSession(league_payload(7, [("PG", 2), ("BN", 3)])))
        rules = _fetch_roster_rules(query, "466.l.1")
        self.assertEqual(rules["bench_slots"], 3)
        positions = [e["position"] for e in rules["active_slots"]]
        self.assertNotIn("BN", positions)
        self.assertEqual(rules["standard_slots"], 5)  # 2 active + 3 bench

    def test_non_standard_league_layout_is_preserved(self):
        """A 3-centre league with no Util slot must not be reshaped."""
        query = _FakeQuery(
            _FakeSession(league_payload(4, [("PG", 1), ("C", 3), ("BN", 2)]))
        )
        rules = _fetch_roster_rules(query, "466.l.1")
        layout = {e["position"]: e["count"] for e in rules["active_slots"]}
        self.assertEqual(layout, {"PG": 1, "C": 3})
        self.assertNotIn("Util", layout)

    def test_failure_returns_default_shape(self):
        query = _FakeQuery(_FakeSession(exc=RuntimeError("boom")))
        self.assertEqual(_fetch_roster_rules(query, "466.l.1"), DEFAULT_ROSTER_RULES)


class TestBuildSlotMap(unittest.TestCase):
    def test_uses_league_layout(self):
        slots = build_slot_map({"active_slots": [
            {"position": "PG", "count": 2}, {"position": "C", "count": 3},
        ]})
        self.assertEqual(slots, {"PG": [None, None], "C": [None, None, None]})

    def test_falls_back_to_default_layout(self):
        slots = build_slot_map({})
        expected = {pos: [None] * n for pos, n in DEFAULT_SLOT_LAYOUT}
        self.assertEqual(slots, expected)

    def test_ignores_zero_count_entries(self):
        slots = build_slot_map({"active_slots": [{"position": "Util", "count": 0}]})
        self.assertEqual(slots, {})


class TestOptimizeRosterUsesLeagueLayout(unittest.TestCase):
    def make_player(self, pid, positions, pts):
        return {
            "Player": f"Player{pid}",
            "player_id": pid,
            "Status": "OK",
            "has_game_today": 1,
            "is_editable": 1,
            "PTS": pts,
            "eligible_positions": positions,
        }

    def run_node(self, roster, rules):
        state = {
            "roster": roster,
            "roster_rules": rules,
            "debug_logs": [],
        }
        with contextlib.redirect_stdout(io.StringIO()) as out:
            optimize_roster_node(state)
        return out.getvalue()

    def test_three_centre_league_starts_three_centres(self):
        roster = [self.make_player(i, ["C"], 20 - i) for i in range(1, 5)]
        rules = {
            "standard_slots": 4,
            "active_slots": [{"position": "C", "count": 3}],
        }
        output = self.run_node(roster, rules)
        self.assertEqual(output.count("-> C"), 3)
        # The fourth centre cannot start and must be benched.
        self.assertEqual(output.count("-> BN"), 1)

    def test_league_without_util_slot_does_not_assign_util(self):
        roster = [self.make_player(1, ["PG", "SG"], 20)]
        rules = {
            "standard_slots": 1,
            "active_slots": [{"position": "PG", "count": 1}],
        }
        output = self.run_node(roster, rules)
        self.assertIn("-> PG", output)
        self.assertNotIn("Util", output)

    def test_util_slot_accepts_any_position(self):
        roster = [self.make_player(1, ["C"], 20)]
        rules = {
            "standard_slots": 1,
            "active_slots": [{"position": "Util", "count": 1}],
        }
        output = self.run_node(roster, rules)
        self.assertIn("-> Util", output)


if __name__ == "__main__":
    unittest.main()
