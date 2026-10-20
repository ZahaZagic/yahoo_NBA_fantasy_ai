"""Tests for the node helpers that do not need a graph or a network."""

import unittest

import tests  # noqa: F401 - ensures src/ is on sys.path

from nodes import (
    DEFAULT_ROSTER_RULES,
    _fetch_roster_rules,
    _validate_transactions,
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


class TestFetchRosterRules(unittest.TestCase):
    def test_reads_il_and_il_plus_separately(self):
        query = _FakeQuery(
            _FakeSession(
                league_payload(7, [("PG", 1), ("SG", 1), ("C", 2), ("IL", 2), ("IL+", 1)])
            )
        )
        rules = _fetch_roster_rules(query, "466.l.1")
        self.assertEqual(rules["il"], 2)
        self.assertEqual(rules["il_plus"], 1)
        self.assertEqual(rules["standard_slots"], 4)
        self.assertEqual(rules["max_adds"], 7)

    def test_failure_falls_back_to_defaults_not_zeros(self):
        query = _FakeQuery(_FakeSession(exc=RuntimeError("boom")))
        rules = _fetch_roster_rules(query, "466.l.1")
        self.assertEqual(rules, DEFAULT_ROSTER_RULES)

    def test_missing_settings_block_falls_back_to_defaults(self):
        query = _FakeQuery(_FakeSession({"fantasy_content": {"league": []}}))
        rules = _fetch_roster_rules(query, "466.l.1")
        self.assertEqual(rules, DEFAULT_ROSTER_RULES)

    def test_zero_add_limit_is_preserved_on_success(self):
        """A league without an add cap keeps 0, rendered as 'unlimited'."""
        query = _FakeQuery(_FakeSession(league_payload(0, [("PG", 5)])))
        rules = _fetch_roster_rules(query, "466.l.1")
        self.assertEqual(rules["max_adds"], 0)
        self.assertEqual(rules["standard_slots"], 5)

    def test_missing_roster_positions_keeps_sane_capacity(self):
        query = _FakeQuery(_FakeSession(league_payload(7, [])))
        rules = _fetch_roster_rules(query, "466.l.1")
        self.assertEqual(rules["standard_slots"], DEFAULT_ROSTER_RULES["standard_slots"])


class TestValidateTransactions(unittest.TestCase):
    def test_accepts_well_formed_entries(self):
        plan = {"transactions": [
            {"type": "add", "player": "A", "player_id": "1"},
            {"type": "move", "player": "B", "drop": "C"},
        ]}
        result = _validate_transactions(plan)
        self.assertEqual([t.type for t in result], ["add", "move"])

    def test_drops_only_the_invalid_entry(self):
        """A model echoing the schema placeholder must not void the whole plan."""
        plan = {"transactions": [
            {"type": "add | drop | activate | move", "player": "<name>"},
            {"type": "add", "player": "Real Target", "player_id": "9"},
        ]}
        result = _validate_transactions(plan)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].player, "Real Target")

    def test_blank_player_is_rejected(self):
        plan = {"transactions": [{"type": "add", "player": "   "}]}
        self.assertEqual(_validate_transactions(plan), [])

    def test_non_dict_plan_returns_empty(self):
        for bad in (None, [], "str", 42):
            self.assertEqual(_validate_transactions(bad), [])

    def test_missing_transactions_key_returns_empty(self):
        self.assertEqual(_validate_transactions({}), [])

    def test_non_list_transactions_returns_empty(self):
        self.assertEqual(_validate_transactions({"transactions": "nope"}), [])


if __name__ == "__main__":
    unittest.main()
