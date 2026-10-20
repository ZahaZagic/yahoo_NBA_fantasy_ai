"""Tests for LLM output parsing, transaction validation, and prompt rendering."""

import unittest

import tests  # noqa: F401 - ensures src/ is on sys.path

from prompts.strategy import REPORT_SECTIONS, SYSTEM_PROMPT, build_human_prompt
from schemas import TransactionPlan
from utils.llm import LLMError, StrategyLLM, extract_json


class TestExtractJson(unittest.TestCase):
    def test_fenced_json_block(self):
        text = 'Preamble\n```json\n{"transactions": []}\n```\nTrailing'
        self.assertEqual(extract_json(text), {"transactions": []})

    def test_plain_json_object(self):
        self.assertEqual(extract_json('{"transactions": []}'), {"transactions": []})

    def test_json_embedded_in_prose(self):
        text = 'Here you go: {"transactions": [{"type": "add", "player": "A"}]} thanks'
        parsed = extract_json(text)
        self.assertEqual(parsed["transactions"][0]["player"], "A")

    def test_nested_braces_inside_strings(self):
        text = '```json\n{"transactions":[{"type":"add","player":"A","reason":"needs {x}"}]}\n```'
        parsed = extract_json(text)
        self.assertEqual(parsed["transactions"][0]["reason"], "needs {x}")

    def test_returns_none_when_absent(self):
        self.assertIsNone(extract_json("no json at all"))
        self.assertIsNone(extract_json(""))
        self.assertIsNone(extract_json(None))

    def test_prefers_the_last_fenced_block(self):
        """Section H comes last; an echoed schema example must not win."""
        text = (
            "Example of the shape:\n"
            '```json\n{"transactions": [{"type": "add | drop", "player": "<name>"}]}\n```\n'
            "Actual answer:\n"
            '```json\n{"transactions": [{"type": "add", "player": "Real"}]}\n```\n'
        )
        parsed = extract_json(text)
        self.assertEqual(parsed["transactions"][0]["player"], "Real")

    def test_returns_none_on_broken_json(self):
        self.assertIsNone(extract_json('```json\n{"transactions": [}\n```'))

    def test_skips_invalid_object_for_later_valid_one(self):
        text = 'bad {"a": } then {"transactions": []}'
        self.assertEqual(extract_json(text), {"transactions": []})


class TestTransactionPlan(unittest.TestCase):
    def test_validates_add_without_drop(self):
        plan = TransactionPlan.model_validate(
            {"transactions": [{"type": "add", "player": "Streamer", "drop": None}]}
        )
        self.assertEqual(plan.transactions[0].player, "Streamer")

    def test_normalises_blank_string_fields_to_none(self):
        plan = TransactionPlan.model_validate(
            {"transactions": [{"type": "add", "player": "A", "drop": "null",
                               "player_id": "N/A"}]}
        )
        self.assertIsNone(plan.transactions[0].drop)
        self.assertIsNone(plan.transactions[0].player_id)

    def test_rejects_unknown_transaction_type(self):
        with self.assertRaises(Exception):
            TransactionPlan.model_validate(
                {"transactions": [{"type": "teleport", "player": "X"}]}
            )

    def test_ignores_extra_keys(self):
        plan = TransactionPlan.model_validate(
            {"transactions": [{"type": "drop", "player": "A", "surprise": 1}]}
        )
        self.assertEqual(len(plan.transactions), 1)

    def test_missing_transactions_defaults_to_empty(self):
        self.assertEqual(TransactionPlan.model_validate({}).transactions, [])


class TestStrategyLLM(unittest.TestCase):
    def test_missing_api_key_raises_actionable_error(self):
        with self.assertRaises(LLMError) as ctx:
            StrategyLLM(api_key=None, model="m")
        self.assertIn("LLM_API_KEY", str(ctx.exception))

    def test_constructs_without_network(self):
        client = StrategyLLM(api_key="sk-test", model="google/gemini-2.0-flash-001")
        self.assertEqual(client.model, "google/gemini-2.0-flash-001")


class TestPrompts(unittest.TestCase):
    CONTEXT = {
        "league_type": "9cat",
        "roster_rules": {"il": 2, "il_plus": 1, "max_adds": 7, "standard_slots": 13},
        "capacity": {
            "total_standard": 13, "used_standard": 12, "open_standard": 1,
            "total_il": 3, "used_il": 2, "empty_il": 1,
        },
        "live_comparison": {"PTS": {"Me": 1, "Opp": 2, "Diff": -1, "Status": "LOST"}},
        "history_comparison": {},
        "history_weeks": {"my": 4, "opp": 4},
        "roster_by_status": {
            "HEALTHY_ACTIVE_ROSTER": [], "HEALTHY_STUCK_IN_IL": [],
            "INJURED_ON_ACTIVE_ROSTER": [], "INJURED_IN_IL_SLOTS": [],
        },
        "weekly_adds_used": 3,
        "waiver_candidates": [],
    }

    def test_system_prompt_has_no_unrendered_placeholders(self):
        self.assertNotIn("{{", SYSTEM_PROMPT)
        self.assertNotIn("}}", SYSTEM_PROMPT)

    def test_system_prompt_declares_all_sections(self):
        for section in REPORT_SECTIONS:
            self.assertIn(section, SYSTEM_PROMPT)

    def test_system_prompt_states_hard_constraints(self):
        self.assertIn("NEVER recommend dropping", SYSTEM_PROMPT)
        self.assertIn("Core Anchor", SYSTEM_PROMPT)

    def test_human_prompt_includes_capacity_and_data(self):
        prompt = build_human_prompt(self.CONTEXT, "February 15, 2026")
        self.assertIn("OPEN ROSTER SPOTS: 1", prompt)
        self.assertIn("EMPTY IL/IL+ SPOTS: 1", prompt)
        self.assertIn("February 15, 2026", prompt)
        self.assertIn("remaining: 4", prompt)

    def test_points_league_uses_points_objective(self):
        context = dict(self.CONTEXT, league_type="points")
        prompt = build_human_prompt(context, "February 15, 2026")
        self.assertIn("total fantasy points", prompt)
        self.assertIn("Category balance is irrelevant", prompt)

    def test_nine_cat_league_uses_category_objective(self):
        prompt = build_human_prompt(self.CONTEXT, "February 15, 2026")
        self.assertIn("9-category", prompt)
        self.assertIn("Swing", prompt)

    def test_unlimited_adds_when_limit_unknown(self):
        context = dict(self.CONTEXT, roster_rules={})
        prompt = build_human_prompt(context, "d")
        self.assertIn("remaining: unlimited", prompt)


if __name__ == "__main__":
    unittest.main()
