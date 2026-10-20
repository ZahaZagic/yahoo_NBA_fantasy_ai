"""Tests for the pure analysis helpers."""

import unittest

import tests  # noqa: F401 - ensures src/ is on sys.path

from analysis import (
    average_history,
    build_strategy_context,
    categorize_roster,
    compact_waiver_pool,
    compare_history,
    compare_live,
    compute_capacity,
    is_injured,
)


class TestAverageHistory(unittest.TestCase):
    def test_empty_history_returns_empty(self):
        self.assertEqual(average_history([]), {})
        self.assertEqual(average_history(None), {})

    def test_averages_counting_stats_per_week(self):
        history = [{"PTS": 100}, {"PTS": 200}]
        self.assertEqual(average_history(history)["PTS"], 150.0)

    def test_averages_makes_and_attempts_separately(self):
        history = [{"FGM/A": "10/20"}, {"FGM/A": "20/40"}]
        self.assertEqual(average_history(history)["FGM/A"], "15.0/30.0")

    def test_ignores_malformed_volume_strings(self):
        result = average_history([{"FGM/A": "not-a-fraction"}])
        self.assertEqual(result["FGM/A"], "0.0/0.0")


class TestCompareHistory(unittest.TestCase):
    def test_higher_counting_stat_wins(self):
        result = compare_history({"PTS": 500}, {"PTS": 480})
        self.assertEqual(result["PTS"]["Status"], "WON")
        self.assertEqual(result["PTS"]["Diff"], 20.0)

    def test_turnovers_are_inverted(self):
        result = compare_history({"TO": 70}, {"TO": 65})
        self.assertEqual(result["TO"]["Status"], "LOST")

    def test_volume_strings_are_informational(self):
        result = compare_history({"FGM/A": "10/20"}, {"FGM/A": "9/19"})
        self.assertEqual(result["FGM/A"]["Status"], "INFO")

    def test_no_history_either_side_yields_empty_block(self):
        """Start of season: no completed weeks means no comparison at all."""
        self.assertEqual(compare_history({}, {}), {})

    def test_metric_missing_on_one_side_is_no_data(self):
        """Must not compare a present value against an implicit zero."""
        result = compare_history({"PTS": 500}, {})
        self.assertEqual(result["PTS"]["Status"], "NO_DATA")
        self.assertNotIn("Diff", result["PTS"])

    def test_equal_averages_are_tied(self):
        result = compare_history({"PTS": 480}, {"PTS": 480})
        self.assertEqual(result["PTS"]["Status"], "TIED")


class TestCompareLive(unittest.TestCase):
    ROWS = [
        {"team_id": "13", "PTS": 210.0},
        {"team_id": "7", "PTS": 240.0},
    ]

    def test_identifies_my_team_by_id(self):
        result = compare_live(self.ROWS, "13")
        self.assertEqual(result["PTS"]["Me"], 210.0)
        self.assertEqual(result["PTS"]["Opp"], 240.0)
        self.assertEqual(result["PTS"]["Status"], "LOST")

    def test_single_row_yields_empty(self):
        self.assertEqual(compare_live([{"team_id": "13"}], "13"), {})

    def test_missing_team_id_defaults_to_first_row(self):
        result = compare_live(self.ROWS, "does-not-exist")
        self.assertEqual(result["PTS"]["Me"], 210.0)

    def test_zero_totals_are_tied_not_lost(self):
        """Week start: 0-0 must not be reported as losing every category."""
        rows = [
            {"team_id": "13", "PTS": 0, "REB": 0},
            {"team_id": "7", "PTS": 0, "REB": 0},
        ]
        result = compare_live(rows, "13")
        self.assertEqual(result["PTS"]["Status"], "TIED")
        self.assertEqual(result["REB"]["Status"], "TIED")


class TestRosterClassification(unittest.TestCase):
    ROSTER = [
        {"Player": "Healthy Starter", "player_id": "1", "Status": "OK",
         "selected_position": "PG"},
        {"Player": "Hurt Starter", "player_id": "2", "Status": "INJ",
         "selected_position": "C"},
        {"Player": "Healthy In IL", "player_id": "3", "Status": "OK",
         "selected_position": "IL"},
        {"Player": "Hurt In IL+", "player_id": "4", "Status": "O",
         "selected_position": "IL+"},
    ]

    def test_groups_by_health_and_slot(self):
        groups = categorize_roster(self.ROSTER)
        self.assertEqual(len(groups["HEALTHY_ACTIVE_ROSTER"]), 1)
        self.assertEqual(len(groups["INJURED_ON_ACTIVE_ROSTER"]), 1)
        self.assertEqual(len(groups["HEALTHY_STUCK_IN_IL"]), 1)
        self.assertEqual(len(groups["INJURED_IN_IL_SLOTS"]), 1)

    def test_is_injured_recognises_gtd(self):
        self.assertTrue(is_injured({"Status": "GTD"}))
        self.assertFalse(is_injured({"Status": "OK"}))

    def test_capacity_accounts_for_both_il_types(self):
        groups = categorize_roster(self.ROSTER)
        capacity = compute_capacity(
            groups, {"il": 2, "il_plus": 1, "standard_slots": 13}
        )
        self.assertEqual(capacity["total_il"], 3)
        self.assertEqual(capacity["used_il"], 2)
        self.assertEqual(capacity["empty_il"], 1)
        self.assertEqual(capacity["used_standard"], 2)
        self.assertEqual(capacity["open_standard"], 11)

    def test_capacity_never_goes_negative(self):
        groups = categorize_roster(self.ROSTER)
        capacity = compute_capacity(groups, {"il": 0, "standard_slots": 1})
        self.assertEqual(capacity["open_standard"], 0)
        self.assertEqual(capacity["empty_il"], 0)


class TestWaiverPool(unittest.TestCase):
    def test_trims_to_expected_shape(self):
        pool = compact_waiver_pool(
            [{"Player": "A", "player_id": "9", "% Owned": 40, "junk": "x"}], limit=1
        )
        self.assertEqual(pool[0]["name"], "A")
        self.assertNotIn("junk", pool[0])

    def test_respects_limit(self):
        players = [{"Player": f"P{i}"} for i in range(40)]
        self.assertEqual(len(compact_waiver_pool(players, limit=5)), 5)


class TestBuildStrategyContext(unittest.TestCase):
    def test_assembles_expected_keys(self):
        state = {
            "league_type": "points",
            "team_id": "13",
            "roster": [{"Player": "A", "player_id": "1", "Status": "OK",
                        "selected_position": "PG"}],
            "roster_rules": {"il": 1, "standard_slots": 13},
            "historical_summary": {"my": [{"PTS": 100}], "opp": []},
            "live_matchup_stats": {"stats": []},
            "fa_today": [],
            "weekly_adds_used": 2,
        }
        context = build_strategy_context(state)
        for key in (
            "capacity", "live_comparison", "history_comparison",
            "roster_by_status", "waiver_candidates",
        ):
            self.assertIn(key, context)
        self.assertEqual(context["league_type"], "points")
        self.assertEqual(context["weekly_adds_used"], 2)


if __name__ == "__main__":
    unittest.main()
