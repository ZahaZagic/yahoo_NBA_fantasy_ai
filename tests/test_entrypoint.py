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
