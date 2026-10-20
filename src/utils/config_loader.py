"""Configuration loading for the Fantasy NBA agent.

Resolution order (highest priority first):

1. Environment variables (optionally loaded from a local ``.env`` file).
2. ``config.json`` at the project root (git-ignored, created from
   ``config.example.json``).
3. Built-in defaults.

This means the repository can be cloned and run with **only** a ``.env`` file --
no secrets ever need to live in a tracked JSON file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:  # python-dotenv is optional at runtime
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - graceful degradation
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

#: Defaults for the LLM block. Overridable via config.json or environment.
DEFAULT_LLM: Dict[str, Any] = {
    "base_url": "https://openrouter.ai/api/v1",
    "model": "google/gemini-2.0-flash-001",
    "temperature": 0.2,
    "max_tokens": 4000,
    "timeout_seconds": 90,
    "max_retries": 3,
}

DEFAULT_GAME_CODE = "nba"
DEFAULT_LEAGUE_TYPE = "9cat"
VALID_LEAGUE_TYPES = {"9cat", "points"}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _load_dotenv_once() -> None:
    """Load ``.env`` from the project root (idempotent, never overrides env)."""
    if load_dotenv is None:
        return
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _env(*names: str, default: Optional[str] = None) -> Optional[str]:
    """Return the first non-empty environment variable among ``names``."""
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def _read_config_file(config_path: Optional[Path]) -> Dict[str, Any]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config.json must contain a JSON object at the top level.")
    return data


def _merge_llm(config: Dict[str, Any]) -> Dict[str, Any]:
    llm = dict(DEFAULT_LLM)
    llm.update(config.get("llm") or {})

    llm["api_key"] = _env(
        "LLM_API_KEY",
        "OPENROUTER_API_KEY",
        # Legacy key names kept so existing local configs keep working.
        default=config.get("llm_api_key") or config.get("gemini_api_key"),
    )
    llm["base_url"] = _env("LLM_BASE_URL", default=llm["base_url"])
    llm["model"] = _env("LLM_MODEL", default=llm["model"])
    return llm


def load_config(
    profile_name: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build the fully resolved runtime configuration.

    Args:
        profile_name: Name of the profile to load. Defaults to
            ``default_profile`` in ``config.json``.
        config_path: Optional explicit path to a config file. When omitted the
            project-root ``config.json`` is used if present.

    Returns:
        A flat dictionary with Yahoo credentials, league selection, and a
        nested ``llm`` block.

    Raises:
        ConfigError: If no profile can be resolved or required identifiers are
            missing.
    """
    _load_dotenv_once()

    config = _read_config_file(config_path)
    profiles = config.get("profiles") or {}

    name = profile_name or config.get("default_profile") or "default"
    profile = profiles.get(name)

    if profile is None:
        if profiles:
            raise ConfigError(
                f"Profile '{name}' not found. Available profiles: "
                f"{', '.join(sorted(profiles))}"
            )
        # Pure environment-variable mode: no config.json required.
        profile = {}

    resolved: Dict[str, Any] = {
        "profile_name": name,
        "client_id": _env(
            "YAHOO_CONSUMER_KEY", "YAHOO_CLIENT_ID",
            default=profile.get("client_id"),
        ),
        "client_secret": _env(
            "YAHOO_CONSUMER_SECRET", "YAHOO_CLIENT_SECRET",
            default=profile.get("client_secret"),
        ),
        "league_id": _env("YAHOO_LEAGUE_ID", default=profile.get("league_id")),
        "team_id": _env("YAHOO_TEAM_ID", default=profile.get("team_id")),
        "game_code": _env(
            "YAHOO_GAME_CODE", default=profile.get("game_code", DEFAULT_GAME_CODE)
        ),
        "league_type": _env(
            "LEAGUE_TYPE", default=profile.get("league_type", DEFAULT_LEAGUE_TYPE)
        ),
        # Optional season pin (e.g. 466 = 2025-26). Only used when the season
        # cannot be detected from Yahoo; never guessed.
        "game_id": _env("YAHOO_GAME_ID", default=profile.get("game_id")),
        "llm": _merge_llm(config),
    }

    if resolved["game_id"] is not None:
        try:
            resolved["game_id"] = int(resolved["game_id"])
        except (TypeError, ValueError):
            raise ConfigError(
                f"game_id/YAHOO_GAME_ID must be an integer season key, got "
                f"'{resolved['game_id']}'."
            )

    resolved["league_type"] = str(resolved["league_type"]).lower()
    if resolved["league_type"] not in VALID_LEAGUE_TYPES:
        raise ConfigError(
            f"league_type must be one of {sorted(VALID_LEAGUE_TYPES)}, "
            f"got '{resolved['league_type']}'."
        )

    for field, env_hint in (
        ("client_id", "YAHOO_CONSUMER_KEY"),
        ("client_secret", "YAHOO_CONSUMER_SECRET"),
        ("league_id", "YAHOO_LEAGUE_ID"),
        ("team_id", "YAHOO_TEAM_ID"),
    ):
        if not resolved.get(field):
            raise ConfigError(
                f"Missing '{field}'. Set {env_hint} in your .env file or add it "
                f"to profile '{name}' in config.json (see config.example.json)."
            )
        resolved[field] = str(resolved[field])

    return resolved
