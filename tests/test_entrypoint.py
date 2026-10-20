"""Regression tests for the entry point and import modes.

The project supports two invocation styles:

* ``python -m src.main`` (package mode, relative imports)
* ``python src/main.py`` (script mode, ``sys.path`` contains ``src/``)

Both used to be possible to break independently, so both are asserted here.
"""

import subprocess
import sys
import unittest
from pathlib import Path

import tests  # noqa: F401 - ensures src/ is on sys.path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestPackageImportMode(unittest.TestCase):
    def test_package_modules_import_cleanly(self):
        """Relative imports must work when imported as ``src.<module>``."""
        import importlib

        for module in (
            "src.state",
            "src.analysis",
            "src.schemas",
            "src.prompts.strategy",
            "src.utils.config_loader",
            "src.utils.llm",
            "src.utils.yahoo_api",
            "src.utils.stats_parser",
            "src.utils.scheduler",
            "src.nodes",
            "src.main",
        ):
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.import_module(module))

    def test_graph_builds_with_all_nodes(self):
        from src.main import build_graph

        graph = build_graph().get_graph()
        for node in (
            "fetch", "optimize_roster", "analyze_injuries",
            "llm_strategy", "format_output",
        ):
            self.assertIn(node, graph.nodes)


class TestCliEntrypoint(unittest.TestCase):
    def _run(self, args):
        return subprocess.run(
            args,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_module_mode_help_exits_zero(self):
        result = self._run([sys.executable, "-m", "src.main", "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--profile", result.stdout)

    def test_script_mode_help_exits_zero(self):
        result = self._run(
            [sys.executable, str(PROJECT_ROOT / "src" / "main.py"), "--help"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--profile", result.stdout)

    def test_missing_credentials_exit_with_config_error(self):
        """A run with no credentials must fail fast with exit code 2."""
        result = self._run(
            [sys.executable, "-m", "src.main", "--profile", "definitely-missing"]
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Configuration problem", result.stderr)


if __name__ == "__main__":
    unittest.main()


class TestSeasonDetection(unittest.TestCase):
    """A guessed season key would silently read a different year's league."""

    class _Session:
        def __init__(self, payloads=None, exc=None):
            self._payloads = payloads or []
            self._exc = exc
            self.calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            if self._exc:
                raise self._exc
            payload = self._payloads[min(self.calls - 1, len(self._payloads) - 1)]
            return type("R", (), {"json": lambda _self: payload})()

    def _query(self, session):
        return type("Q", (), {"oauth": type("O", (), {"session": session})()})()

    def test_detects_game_key_from_yahoo(self):
        from src.main import detect_game_key_and_week

        session = self._Session([
            {"fantasy_content": {"game": [{"game_key": 475}]}},
            {"fantasy_content": {"league": [{"current_week": 3}]}},
        ])
        game_key, week = detect_game_key_and_week(
            self._query(session), "123456", "nba"
        )
        self.assertEqual(game_key, 475)
        self.assertEqual(week, 3)

    def test_raises_rather_than_guessing_a_season(self):
        from src.main import detect_game_key_and_week

        session = self._Session(exc=RuntimeError("network down"))
        with self.assertRaises(RuntimeError) as ctx:
            detect_game_key_and_week(self._query(session), "123456", "nba")
        self.assertIn("YAHOO_GAME_ID", str(ctx.exception))

    def test_403_is_reported_with_yahoo_error_text(self):
        """An unauthorized app must not look like a network problem."""
        from src.main import detect_game_key_and_week

        class _Resp:
            status_code = 403

            def json(self):
                return {"error": {
                    "description": "This application is not authorized to "
                                   "perform this action."
                }}

        class _S:
            def get(self, *a, **k):
                return _Resp()

        query = type("Q", (), {"oauth": type("O", (), {"session": _S()})()})()
        with self.assertRaises(RuntimeError) as ctx:
            detect_game_key_and_week(query, "123456", "nba")
        message = str(ctx.exception)
        self.assertIn("403", message)
        self.assertIn("not authorized", message)
        self.assertIn("Fantasy Sports permission", message)

    def test_uses_configured_game_id_when_detection_fails(self):
        from src.main import detect_game_key_and_week

        session = self._Session(exc=RuntimeError("network down"))
        game_key, _ = detect_game_key_and_week(
            self._query(session), "123456", "nba", game_id=499
        )
        self.assertEqual(game_key, 499)


if __name__ == "__main__":
    unittest.main()
