"""Tests for configuration resolution (env vars, config file, defaults)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests  # noqa: F401 - ensures src/ is on sys.path

from utils.config_loader import ConfigError, load_config

BASE_ENV = {
    "YAHOO_CONSUMER_KEY": "env-key",
    "YAHOO_CONSUMER_SECRET": "env-secret",
    "YAHOO_LEAGUE_ID": "123456",
    "YAHOO_TEAM_ID": "7",
    "YAHOO_GAME_CODE": "nba",
    "LEAGUE_TYPE": "9cat",
    "LLM_API_KEY": "env-llm-key",
}


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def write_config(self, payload):
        path = self.tmp_path / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def clear_env(self):
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestEnvOnlyMode(ConfigTestCase):
    def test_resolves_from_environment_without_config_file(self):
        self.clear_env()
        with mock.patch.dict(os.environ, BASE_ENV):
            config = load_config(config_path=self.tmp_path / "missing.json")
        self.assertEqual(config["client_id"], "env-key")
        self.assertEqual(config["league_id"], "123456")
        self.assertEqual(config["team_id"], "7")
        self.assertEqual(config["league_type"], "9cat")
        self.assertEqual(config["llm"]["api_key"], "env-llm-key")

    def test_missing_credentials_raise_actionable_error(self):
        self.clear_env()
        with mock.patch.dict(os.environ, {"YAHOO_CONSUMER_KEY": "only-key"}):
            with self.assertRaises(ConfigError) as ctx:
                load_config(config_path=self.tmp_path / "missing.json")
        self.assertIn("client_secret", str(ctx.exception))
        self.assertIn("YAHOO_CONSUMER_SECRET", str(ctx.exception))


class TestConfigFileMode(ConfigTestCase):
    def payload(self):
        return {
            "default_profile": "main",
            "llm": {"model": "file/model", "temperature": 0.5},
            "profiles": {
                "main": {
                    "client_id": "file-key",
                    "client_secret": "file-secret",
                    "league_id": "999",
                    "team_id": "3",
                    "league_type": "points",
                }
            },
        }

    def test_reads_profile_from_file(self):
        self.clear_env()
        path = self.write_config(self.payload())
        with mock.patch.dict(os.environ, {}):
            config = load_config(config_path=path)
        self.assertEqual(config["client_id"], "file-key")
        self.assertEqual(config["league_type"], "points")
        self.assertEqual(config["llm"]["model"], "file/model")
        self.assertEqual(config["llm"]["temperature"], 0.5)

    def test_environment_overrides_file(self):
        self.clear_env()
        path = self.write_config(self.payload())
        with mock.patch.dict(os.environ, BASE_ENV):
            config = load_config(config_path=path)
        self.assertEqual(config["client_id"], "env-key")
        self.assertEqual(config["league_type"], "9cat")

    def test_unknown_profile_lists_available_ones(self):
        self.clear_env()
        path = self.write_config(self.payload())
        with mock.patch.dict(os.environ, {}):
            with self.assertRaises(ConfigError) as ctx:
                load_config(profile_name="nope", config_path=path)
        self.assertIn("main", str(ctx.exception))

    def test_invalid_league_type_rejected(self):
        self.clear_env()
        payload = self.payload()
        payload["profiles"]["main"]["league_type"] = "roto"
        path = self.write_config(payload)
        with mock.patch.dict(os.environ, {}):
            with self.assertRaises(ConfigError):
                load_config(config_path=path)

    def test_legacy_gemini_api_key_is_honoured(self):
        self.clear_env()
        payload = self.payload()
        payload["gemini_api_key"] = "legacy-key"
        path = self.write_config(payload)
        with mock.patch.dict(os.environ, {}):
            config = load_config(config_path=path)
        self.assertEqual(config["llm"]["api_key"], "legacy-key")

    def test_malformed_json_raises_config_error(self):
        self.clear_env()
        path = self.tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with mock.patch.dict(os.environ, {}):
            with self.assertRaises(ConfigError):
                load_config(config_path=path)

    def test_game_id_is_parsed_as_integer(self):
        self.clear_env()
        path = self.write_config(self.payload())
        with mock.patch.dict(os.environ, {"YAHOO_GAME_ID": "475"}):
            config = load_config(config_path=path)
        self.assertEqual(config["game_id"], 475)

    def test_game_id_absent_is_none(self):
        self.clear_env()
        path = self.write_config(self.payload())
        with mock.patch.dict(os.environ, {}):
            config = load_config(config_path=path)
        self.assertIsNone(config["game_id"])

    def test_non_numeric_game_id_is_rejected(self):
        self.clear_env()
        path = self.write_config(self.payload())
        with mock.patch.dict(os.environ, {"YAHOO_GAME_ID": "nba-2026"}):
            with self.assertRaises(ConfigError):
                load_config(config_path=path)

    def test_llm_defaults_are_applied(self):
        self.clear_env()
        path = self.write_config(self.payload())
        with mock.patch.dict(os.environ, {}):
            config = load_config(config_path=path)
        self.assertEqual(
            config["llm"]["base_url"], "https://openrouter.ai/api/v1"
        )
        self.assertEqual(config["llm"]["max_retries"], 3)


if __name__ == "__main__":
    unittest.main()
