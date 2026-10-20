"""End-to-end integration test of the compiled LangGraph workflow.

Yahoo fetching and the LLM call are both replaced with fakes, so the test proves
the *wiring*: node order, state merging, prompt construction, JSON parsing,
schema validation, and report rendering.
"""

import contextlib
import io
import unittest
from unittest import mock

import tests  # noqa: F401 - ensures src/ is on sys.path

import src.main as main_module
import src.nodes as nodes_module
from src.utils.llm import LLMResponse

FAKE_REPORT = """A) Matchup Win Plan
- Key Factors: trailing PTS, contest STL.
B) Injury Status & Return Timelines
- Hurt Guy: re-evaluate in 2 weeks.
C) Category / Points Risk Analysis
- PTS -30.0 live vs +20.0 seasonal.
D) Player Value Ranking
1. Star Guy (OK|Core Anchor) - best volume.
E) Drop Priority
- none required.
F) Streaming Rule
- stream only if adds remain.
G) Add Priority
1. Streamer A
H) Automation JSON
```json
{"transactions": [
  {"type": "move", "player": "Healthy IL Guy", "drop": "Hurt Guy",
   "player_id": "3", "drop_id": "2", "instruction": "activate from IL",
   "reason": "frees an IL slot and adds a game today"}
]}
```"""

ROSTER = [
    {"Player": "Star Guy", "player_id": "1", "Status": "OK", "StatusFull": "OK",
     "selected_position": "PG", "Season Rank": 12, "has_game_today": 1,
     "is_editable": 1, "PTS": 27.5, "REB": 6.0, "eligible_positions": ["PG", "SG"]},
    {"Player": "Hurt Guy", "player_id": "2", "Status": "INJ", "StatusFull": "Injured",
     "selected_position": "C", "Season Rank": 55, "has_game_today": 0,
     "is_editable": 1, "PTS": 20.0, "eligible_positions": ["C"]},
    {"Player": "Healthy IL Guy", "player_id": "3", "Status": "OK", "StatusFull": "OK",
     "selected_position": "IL", "Season Rank": 140, "has_game_today": 1,
     "is_editable": 1, "PTS": 18.0, "eligible_positions": ["C"]},
]

FREE_AGENTS = [
    {"Player": "Streamer A", "player_id": "9", "% Owned": 40, "PTS": 18.0,
     "MPG": 30.0, "REB": 9.0, "has_game_today": 1},
]

FETCH_RESULT = {
    "roster": ROSTER,
    "opp_id": "7",
    "live_matchup_stats": {"week": 5, "stats": [
        {"team_id": "1", "PTS": 210.0},
        {"team_id": "7", "PTS": 240.0},
    ]},
    "il_slots_available": 2,
    "roster_rules": {"il": 2, "il_plus": 1, "max_adds": 7, "standard_slots": 13},
    "weekly_adds_used": 3,
    "fa_today": FREE_AGENTS,
    "historical_summary": {
        "my": [{"PTS": 500, "REB": 180, "FGM/A": "190/400"}],
        "opp": [{"PTS": 480, "REB": 200, "FGM/A": "180/390"}],
    },
}

INITIAL_STATE = {
    "roster": [],
    "league_metadata": {"current_week": 5},
    "opp_id": "",
    "il_slots_available": 0,
    "fa_today": [],
    "fa_tomorrow": [],
    "historical_summary": {},
    "decisions_list": [],
    "debug_logs": [],
    "query": None,
    "league_key": "466.l.123456",
    "league_id": "123456",
    "team_id": "1",
    "league_type": "9cat",
    "llm": {"api_key": "sk-or-v1-test", "model": "google/gemini-2.0-flash-001"},
    "token_usage": {},
    "llm_analysis": "",
    "llm_error": "",
    "proposed_transactions": [],
    "today_str": "2026-02-15",
    "weekly_adds_used": 0,
    "roster_rules": {"il": 0, "il_plus": 0, "max_adds": 7, "standard_slots": 13},
}

captured = {}


def fake_fetch(state):
    captured["fetched"] = True
    return dict(FETCH_RESULT)


def fake_build_llm(config):
    captured["llm_config"] = config

    class _Fake:
        def invoke(self, system_prompt, human_prompt):
            captured["system"] = system_prompt
            captured["human"] = human_prompt
            return LLMResponse(
                text=FAKE_REPORT,
                usage={"prompt_tokens": 1800, "completion_tokens": 320,
                       "total_tokens": 2120},
                model=config.get("model", ""),
            )

    return _Fake()


class TestWorkflowEndToEnd(unittest.TestCase):
    def run_workflow(self):
        with mock.patch.object(main_module, "fetch_data_node", fake_fetch), \
                mock.patch.object(nodes_module, "build_llm", fake_build_llm), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            app = main_module.build_graph()
            final_state = app.invoke(dict(INITIAL_STATE))
        return final_state, stdout.getvalue()

    def test_pipeline_completes_all_nodes(self):
        final_state, _ = self.run_workflow()
        self.assertTrue(captured.get("fetched"))
        self.assertEqual(final_state["opp_id"], "7")
        self.assertEqual(final_state["il_slots_available"], 2)

    def test_prompt_is_built_from_fetched_data(self):
        self.run_workflow()
        self.assertIn("Fantasy Basketball Matchup Optimization Agent", captured["system"])
        self.assertIn("## 5. FREE AGENTS", captured["human"])
        self.assertIn("Streamer A", captured["human"])
        self.assertIn("OPEN ROSTER SPOTS", captured["human"])

    def test_transactions_are_parsed_and_validated(self):
        final_state, _ = self.run_workflow()
        transactions = final_state["proposed_transactions"]
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["type"], "move")
        self.assertEqual(transactions[0]["player"], "Healthy IL Guy")
        self.assertEqual(transactions[0]["drop"], "Hurt Guy")

    def test_token_usage_propagates(self):
        final_state, _ = self.run_workflow()
        self.assertEqual(final_state["token_usage"]["total_tokens"], 2120)

    def test_report_is_printed(self):
        _, stdout = self.run_workflow()
        self.assertIn("FANTASY NBA MATCHUP PLAN", stdout)
        self.assertIn("PROPOSED TRANSACTIONS", stdout)
        self.assertIn("TOKEN USAGE", stdout)

    def test_injury_node_records_il_conflict(self):
        final_state, _ = self.run_workflow()
        joined = " ".join(final_state["decisions_list"])
        self.assertIn("Hurt Guy", joined)
        self.assertIn("Healthy IL Guy", joined)


class TestWorkflowDegradesGracefully(unittest.TestCase):
    def test_missing_llm_key_still_completes(self):
        state = dict(INITIAL_STATE)
        state["llm"] = {"api_key": None}
        with mock.patch.object(main_module, "fetch_data_node", fake_fetch), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            app = main_module.build_graph()
            final_state = app.invoke(state)
        self.assertEqual(final_state["proposed_transactions"], [])
        self.assertIn("LLM_API_KEY", final_state["llm_error"])
        self.assertIn("FANTASY NBA MATCHUP PLAN", stdout.getvalue())

    def test_llm_failure_is_non_fatal(self):
        def exploding_llm(config):
            class _Boom:
                def invoke(self, *_args):
                    from src.utils.llm import LLMError

                    raise LLMError("429 rate limited")

            return _Boom()

        with mock.patch.object(main_module, "fetch_data_node", fake_fetch), \
                mock.patch.object(nodes_module, "build_llm", exploding_llm), \
                contextlib.redirect_stdout(io.StringIO()):
            app = main_module.build_graph()
            final_state = app.invoke(dict(INITIAL_STATE))
        self.assertIn("429", final_state["llm_error"])
        self.assertEqual(final_state["proposed_transactions"], [])


if __name__ == "__main__":
    unittest.main()
