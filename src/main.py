"""Entry point for the Yahoo Fantasy NBA analysis agent.

Usage::

    python -m src.main                      # default profile from config.json
    python -m src.main --profile my_account
    python -m src.main --verbose

The workflow is intentionally **read-only**: it fetches data, computes lineup and
waiver recommendations, and prints a plan. No Yahoo roster changes are executed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from yfpy.query import YahooFantasySportsQuery

try:  # package imports
    from .state import AgentState
    from .nodes import (
        analyze_injuries_node,
        fetch_data_node,
        format_json_output_node,
        llm_strategy_node,
        optimize_roster_node,
    )
    from .utils.config_loader import ConfigError, PROJECT_ROOT, load_config
except ImportError:  # script imports (python src/main.py)
    from state import AgentState
    from nodes import (
        analyze_injuries_node,
        fetch_data_node,
        format_json_output_node,
        llm_strategy_node,
        optimize_roster_node,
    )
    from utils.config_loader import ConfigError, PROJECT_ROOT, load_config

from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)

AUTH_ROOT = PROJECT_ROOT / "auth"


def configure_logging(verbose: bool = False) -> None:
    """Configure root logging once, for both our code and the HTTP libraries."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # These are extremely noisy at DEBUG level.
    for noisy in ("urllib3", "httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def prepare_auth_dir(profile_name: str) -> Path:
    """Return (and create) the per-profile directory that stores OAuth state.

    Migrates the legacy single-token layout (``auth/token.json``, used by
    yfpy<=15) into ``auth/<profile>/token.json`` on first run.
    """
    profile_dir = AUTH_ROOT / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)

    legacy_token = AUTH_ROOT / "token.json"
    target_token = profile_dir / "token.json"
    if legacy_token.exists() and not target_token.exists():
        shutil.copy2(legacy_token, target_token)
        logger.info("Migrated legacy auth/token.json to %s", target_token)

    return profile_dir


def load_access_token(profile_dir: Path) -> Optional[Dict[str, Any]]:
    """Load a cached Yahoo access token for yfpy 17.

    yfpy 17 persists refreshed tokens to a ``.env`` file inside the auth
    directory. When that file exists we return ``None`` so yfpy picks the fresh
    token up from the environment instead of the (now stale) token.json.
    """
    if (profile_dir / ".env").exists():
        return None

    token_file = profile_dir / "token.json"
    if not token_file.exists():
        return None
    try:
        return json.loads(token_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed token file: %s", token_file)
        return None


def _build_query(
    auth_dir: Path,
    league_id: str,
    game_code: str,
    client_id: str,
    client_secret: str,
    access_token: Optional[Dict[str, Any]],
    game_id: Optional[int] = None,
) -> YahooFantasySportsQuery:
    return YahooFantasySportsQuery(
        league_id=league_id,
        game_code=game_code,
        game_id=game_id,
        yahoo_consumer_key=client_id,
        yahoo_consumer_secret=client_secret,
        yahoo_access_token_json=access_token,
        env_var_fallback=True,
        env_file_location=auth_dir,
        save_token_data_to_env_file=True,
        browser_callback=True,
        offline=False,
    )


def detect_game_key_and_week(
    query: YahooFantasySportsQuery, league_id: str, game_code: str
) -> Tuple[int, int]:
    """Resolve the season game key and the league's current week.

    Falls back to the current NBA game key and week 1 when Yahoo cannot be
    reached, so a transient failure degrades instead of aborting the run.
    """
    default_game_key = 466  # 2025-26 NBA season
    game_key = default_game_key
    current_week = 1

    try:
        response = query.oauth.session.get(
            f"https://fantasysports.yahooapis.com/fantasy/v2/game/{game_code}",
            params={"format": "json"},
        )
        game = response.json().get("fantasy_content", {}).get("game", [])
        if isinstance(game, list) and game and isinstance(game[0], dict):
            game_key = int(game[0].get("game_key", default_game_key))
    except Exception as exc:  # noqa: BLE001 - non-fatal
        logger.warning("Could not resolve game key (%s); using %s", exc, default_game_key)

    try:
        response = query.oauth.session.get(
            f"https://fantasysports.yahooapis.com/fantasy/v2/league/"
            f"{game_key}.l.{league_id}/settings",
            params={"format": "json"},
        )
        league = response.json().get("fantasy_content", {}).get("league", [])
        for item in league if isinstance(league, list) else [league]:
            if isinstance(item, dict) and "current_week" in item:
                current_week = int(item["current_week"])
            elif isinstance(item, dict) and "settings" in item:
                settings = item["settings"][0]
                if "current_week" in settings:
                    current_week = int(settings["current_week"])
    except Exception as exc:  # noqa: BLE001 - non-fatal
        logger.warning("Could not resolve current week (%s); using week 1", exc)

    return game_key, current_week


def build_graph() -> Any:
    """Compile the LangGraph decision workflow."""
    workflow = StateGraph(AgentState)

    workflow.add_node("fetch", fetch_data_node)
    workflow.add_node("optimize_roster", optimize_roster_node)
    workflow.add_node("analyze_injuries", analyze_injuries_node)
    workflow.add_node("llm_strategy", llm_strategy_node)
    workflow.add_node("format_output", format_json_output_node)

    workflow.set_entry_point("fetch")
    workflow.add_edge("fetch", "optimize_roster")
    workflow.add_edge("optimize_roster", "analyze_injuries")
    workflow.add_edge("analyze_injuries", "llm_strategy")
    workflow.add_edge("llm_strategy", "format_output")
    workflow.add_edge("format_output", END)

    return workflow.compile()


def run_fantasy_app(profile_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load config, connect to Yahoo, and run the full analysis workflow."""
    config = load_config(profile_name)
    profile = config["profile_name"]
    league_id = config["league_id"]
    team_id = config["team_id"]
    game_code = config["game_code"]

    llm_config = config["llm"]
    if not llm_config.get("api_key"):
        logger.warning(
            "No LLM API key configured - the strategy step will be skipped. "
            "Set LLM_API_KEY in your .env file."
        )

    auth_dir = prepare_auth_dir(profile)
    query = _build_query(
        auth_dir=auth_dir,
        league_id=league_id,
        game_code=game_code,
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        access_token=load_access_token(auth_dir),
    )

    game_key, current_week = detect_game_key_and_week(query, league_id, game_code)
    # The downstream helpers read `query.game_id` to build player/team keys.
    query.game_id = int(game_key)
    league_key = f"{game_key}.l.{league_id}"

    print("\n" + "#" * 40)
    print(f" FANTASY NBA AGENT -- profile={profile} week={current_week} ")
    print("#" * 40 + "\n")

    app = build_graph()
    initial_state: AgentState = {
        "roster": [],
        "league_metadata": {"current_week": current_week},
        "opp_id": "",
        "il_slots_available": 0,
        "fa_today": [],
        "fa_tomorrow": [],
        "historical_summary": {},
        "decisions_list": [],
        "debug_logs": [],
        "query": query,
        "league_key": league_key,
        "league_id": league_id,
        "team_id": team_id,
        "league_type": config["league_type"],
        "llm": llm_config,
        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "llm_analysis": "",
        "llm_error": "",
        "proposed_transactions": [],
        "today_str": datetime.date.today().strftime("%Y-%m-%d"),
        "weekly_adds_used": 0,
        "roster_rules": {"il": 0, "il_plus": 0, "max_adds": 7, "standard_slots": 13},
    }

    return app.invoke(initial_state)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze a Yahoo Fantasy NBA matchup and print a weekly plan."
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Config profile to use (defaults to default_profile in config.json).",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable debug-level logging."
    )
    args = parser.parse_args(argv)

    configure_logging(args.verbose)

    try:
        run_fantasy_app(args.profile)
    except ConfigError as exc:
        logger.error("Configuration problem: %s", exc)
        return 2
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level guard
        logger.exception("Run failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
