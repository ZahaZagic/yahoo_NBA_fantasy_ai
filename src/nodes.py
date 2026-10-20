"""LangGraph nodes for the Yahoo Fantasy NBA decision workflow.

Each node is a pure-ish function of ``AgentState`` -> partial ``AgentState``.
Data fetching lives in :mod:`utils.yahoo_api`, scoring in :mod:`analysis`, and
prompting in :mod:`prompts.strategy`, so this module only wires them together.
"""

from __future__ import annotations

import datetime
import json
import logging

try:  # package imports (python -m src.main)
    from .analysis import build_strategy_context
    from .prompts.strategy import SYSTEM_PROMPT, build_human_prompt
    from .schemas import Transaction
    from .state import AgentState
    from .utils.llm import LLMError, build_llm, extract_json
    from .utils.yahoo_api import (
        get_free_agents_by_date,
        get_historical_team_summary,
        get_live_matchup,
        get_team_roster,
    )
except ImportError:  # script imports (python src/main.py)
    from analysis import build_strategy_context
    from prompts.strategy import SYSTEM_PROMPT, build_human_prompt
    from schemas import Transaction
    from state import AgentState
    from utils.llm import LLMError, build_llm, extract_json
    from utils.yahoo_api import (
        get_free_agents_by_date,
        get_historical_team_summary,
        get_live_matchup,
        get_team_roster,
    )

logger = logging.getLogger(__name__)

#: Fallback roster shape when the league settings endpoint is unavailable.
DEFAULT_ROSTER_RULES = {"il": 0, "il_plus": 0, "max_adds": 7, "standard_slots": 13}

#: Approximate USD per 1M tokens, used only for the human-facing cost estimate.
MODEL_COST_PER_MILLION = {
    "google/gemini-2.0-flash-001": 0.10,
    "google/gemini-2.0-flash": 0.10,
    "google/gemini-1.5-flash": 0.075,
}


def _fetch_roster_rules(query, league_key: str) -> dict:
    """Read IL/IL+ slot counts and the weekly add limit from league settings.

    A *successful* read uses the league's real values (so a league with no add
    limit keeps ``max_adds: 0``, rendered as "unlimited"). A *failed* read falls
    back to :data:`DEFAULT_ROSTER_RULES` rather than zeros, because reporting
    "0 slots / unlimited adds" during an API outage would actively mislead the
    prompt.
    """
    rules = {"il": 0, "il_plus": 0, "max_adds": 0, "standard_slots": 0}
    url = (
        "https://fantasysports.yahooapis.com/fantasy/v2/league/"
        f"{league_key}/settings?format=json"
    )
    try:
        response = query.oauth.session.get(url)
        payload = response.json().get("fantasy_content", {}).get("league", [])
        parsed_settings = False
        for item in payload:
            if not (isinstance(item, dict) and "settings" in item):
                continue
            settings = item["settings"][0]
            parsed_settings = True
            rules["max_adds"] = int(settings.get("max_weekly_adds", 0))
            for entry in settings.get("roster_positions", []):
                info = entry.get("roster_position", {})
                position = info.get("position")
                count = int(info.get("count", 0))
                if position == "IL":
                    rules["il"] = count
                elif position == "IL+":
                    rules["il_plus"] = count
                else:
                    rules["standard_slots"] += count
        if not parsed_settings:
            raise ValueError("settings block missing from league response")
        if rules["standard_slots"] == 0:
            # Some leagues omit roster_positions; keep a sane capacity.
            rules["standard_slots"] = DEFAULT_ROSTER_RULES["standard_slots"]
    except Exception as exc:  # noqa: BLE001 - settings are best-effort
        logger.warning("Could not read league settings (%s); using defaults", exc)
        return dict(DEFAULT_ROSTER_RULES)
    return rules


def _fetch_weekly_adds_used(query, league_key: str, team_id: str) -> int:
    """Read how many adds the team has already spent this week."""
    team_key = f"{league_key}.t.{team_id}"
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/team/{team_key}?format=json"
    try:
        response = query.oauth.session.get(url)
        payload = response.json().get("fantasy_content", {}).get("team", [])
        for part in payload:
            if isinstance(part, dict) and "roster_adds" in part:
                return int(part["roster_adds"].get("value", 0))
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.warning("Could not read weekly adds (%s)", exc)
    return 0


def fetch_data_node(state: AgentState) -> dict:
    """Collect roster, matchup, history and free agents from Yahoo + ESPN."""
    logger.info("fetch: collecting league data")
    league_key = state["league_key"]
    league_id = state["league_id"]
    team_id = state["team_id"]
    query = state["query"]
    current_week = state["league_metadata"].get("current_week", 1)

    roster_rules = _fetch_roster_rules(query, league_key)
    adds_used = _fetch_weekly_adds_used(query, league_key, team_id)

    roster = get_team_roster(query, league_id, team_id, "My Team")

    opponent_id, live_stats = get_live_matchup(query, league_id, team_id, current_week)
    history_mine = get_historical_team_summary(query, league_id, team_id, current_week)
    history_opponent = (
        get_historical_team_summary(query, league_id, opponent_id, current_week)
        if opponent_id
        else []
    )

    today = datetime.date.today().strftime("%Y-%m-%d")
    by_ownership = get_free_agents_by_date(query, league_key, today, sort_type="R_PO")
    by_rank = get_free_agents_by_date(query, league_key, today, sort_type="OR")

    # Deduplicate on Yahoo's player_id (names carry decoration suffixes).
    merged: dict = {}
    for player in by_ownership + by_rank:
        key = player.get("player_id") or player.get("Player")
        merged.setdefault(key, player)
    free_agents = sorted(
        merged.values(), key=lambda p: p.get("% Owned", 0) or 0, reverse=True
    )

    logger.info(
        "fetch: %d roster players, %d free agents, opponent=%s, week=%s",
        len(roster), len(free_agents), opponent_id, current_week,
    )

    return {
        "roster": roster,
        "opp_id": opponent_id or "",
        "live_matchup_stats": {"week": current_week, "stats": live_stats},
        "il_slots_available": roster_rules["il"],
        "roster_rules": roster_rules,
        "weekly_adds_used": adds_used,
        "fa_today": free_agents,
        "league_key": league_key,
        "league_id": league_id,
        "team_id": team_id,
        "historical_summary": {"my": history_mine, "opp": history_opponent},
        "debug_logs": state["debug_logs"] + ["fetch: data collection complete"],
    }


def analyze_injuries_node(state: AgentState) -> dict:
    """Flag injured players occupying active slots and healthy players stuck in IL."""
    logger.info("injuries: checking IL and injury status")
    roster = state["roster"]
    decisions: list[str] = []

    active_or_bench = [
        p for p in roster if p.get("selected_position") not in ("IL", "IL+")
    ]
    il_players = [p for p in roster if p.get("selected_position") in ("IL", "IL+")]

    injured_on_active = [
        p for p in active_or_bench if p.get("Status") in ("INJ", "O")
    ]
    for player in injured_on_active:
        decisions.append(
            f"STAYING ON ACTIVE: {player['Player']} is {player['Status']}. "
            "Move to IL to free a spot."
        )

    healthy_in_il = [p for p in il_players if p.get("Status") == "OK"]
    for player in healthy_in_il:
        decisions.append(
            f"STUCK IN IL: {player['Player']} is OK. Activate him to start scoring."
        )

    standard_slots = int((state.get("roster_rules") or {}).get("standard_slots", 13) or 13)
    if healthy_in_il and len(active_or_bench) >= standard_slots:
        if not injured_on_active:
            decisions.append(
                "SQUAD FULL: You must DROP a healthy player to activate your "
                "healthy IL player(s)."
            )
        else:
            decisions.append(
                "SQUAD FULL: Move your injured active players to IL to make room "
                "for healthy players."
            )

    if state["il_slots_available"] == 0 and injured_on_active:
        decisions.append(
            "IL Slots FULL. You must drop someone if you want to move an injured "
            "player to IL."
        )

    return {
        "decisions_list": state["decisions_list"] + decisions,
        "debug_logs": state["debug_logs"] + ["injuries: IL analysis complete"],
    }


def optimize_roster_node(state: AgentState) -> dict:
    """Greedily assign today's best lineup (read-only; prints the plan).

    Assignment is a three-pass greedy fill: specialist slots first, then the
    G/F flex slots, then Util. Ties break on points per game.
    """
    logger.info("lineup: computing optimal assignment for today")
    roster = state["roster"]
    standard_slots = int((state.get("roster_rules") or {}).get("standard_slots", 13) or 13)

    eligible = [
        p
        for p in roster
        if p.get("has_game_today") == 1
        and p.get("is_editable") == 1
        and p.get("Status") == "OK"
    ]

    def value(player: dict) -> float:
        try:
            return float(player.get("PTS", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    ranked = sorted(eligible, key=value, reverse=True)

    slots: dict = {
        "PG": None, "SG": None, "G": None,
        "SF": None, "PF": None, "F": None,
        "C": [None, None],
        "Util": [None, None],
    }
    assigned: set = set()

    def try_assign(player: dict, slot: str) -> bool:
        player_id = player.get("player_id")
        if not player_id or player_id in assigned:
            return False
        positions = player.get("eligible_positions", []) or []
        eligible_for_slot = (
            slot in positions
            or (slot == "G" and ("PG" in positions or "SG" in positions))
            or (slot == "F" and ("SF" in positions or "PF" in positions))
            or slot == "Util"
        )
        if not eligible_for_slot:
            return False

        current = slots[slot]
        if isinstance(current, list):
            if None in current:
                current[current.index(None)] = player
                assigned.add(player_id)
                return True
            return False
        if current is None:
            slots[slot] = player
            assigned.add(player_id)
            return True
        return False

    for player in ranked:
        for slot in ("PG", "SG", "SF", "PF", "C"):
            if try_assign(player, slot):
                break

    for player in ranked:
        if player.get("player_id") in assigned:
            continue
        for slot in ("G", "F"):
            if try_assign(player, slot):
                break

    for player in ranked:
        if player.get("player_id") in assigned:
            continue
        try_assign(player, "Util")

    position_map: dict = {}
    instructions: list[str] = []
    for slot, occupant in slots.items():
        for player in occupant if isinstance(occupant, list) else [occupant]:
            if not player:
                continue
            position_map[player["player_id"]] = slot
            instructions.append(f"   [START] {player['Player']} -> {slot}")

    for index, player in enumerate(roster):
        player_id = player.get("player_id")
        if (
            index < standard_slots
            and player_id
            and player_id not in assigned
            and player.get("is_editable") == 1
        ):
            position_map[player_id] = "BN"
            instructions.append(f"   [BENCH] {player['Player']} -> BN")

    if instructions:
        print("\n--- PROPOSED LINEUP (read-only) ---")
        for instruction in instructions:
            print(instruction)
        print("-----------------------------------\n")
        # Execution is intentionally disabled; see yahoo_api.update_roster_positions.
    else:
        logger.info("lineup: no changes needed for today")

    return {"debug_logs": state["debug_logs"] + ["lineup: assignment computed"]}


def _validate_transactions(raw_plan: object) -> list[Transaction]:
    """Validate the model's JSON, keeping every entry that is well-formed.

    Validation is per-transaction rather than all-or-nothing: one malformed entry
    (for example a model echoing the schema's ``"add | drop | activate | move"``
    placeholder) discards that entry only, instead of silently voiding an
    otherwise usable plan.
    """
    if not isinstance(raw_plan, dict):
        return []

    entries = raw_plan.get("transactions")
    if entries is None:
        return []
    if not isinstance(entries, list):
        logger.warning("`transactions` is not a list; ignoring the plan")
        return []

    validated: list[Transaction] = []
    for index, entry in enumerate(entries):
        try:
            validated.append(Transaction.model_validate(entry))
        except Exception as exc:  # noqa: BLE001 - skip bad entry, keep the rest
            logger.warning("Dropping invalid transaction #%d: %s", index, exc)
    return validated


def llm_strategy_node(state: AgentState) -> dict:
    """Run the LangChain strategy chain and parse its structured plan."""
    logger.info("strategy: requesting matchup plan from the LLM")

    context = build_strategy_context(state)
    current_date = datetime.date.today().strftime("%B %d, %Y")
    human_prompt = build_human_prompt(context, current_date)

    llm_config = state.get("llm") or {}
    if not llm_config.get("api_key"):
        message = (
            "Error: no LLM API key configured. Set LLM_API_KEY in your .env file."
        )
        logger.error("strategy: %s", message)
        return {
            "llm_analysis": message,
            "proposed_transactions": [],
            "llm_error": message,
            "debug_logs": state["debug_logs"] + ["strategy: missing LLM API key"],
        }

    try:
        client = build_llm(llm_config)
        response = client.invoke(SYSTEM_PROMPT, human_prompt)
    except LLMError as exc:
        message = f"LLM request failed: {exc}"
        logger.error("strategy: %s", message)
        return {
            "llm_analysis": message,
            "proposed_transactions": [],
            "llm_error": str(exc),
            "debug_logs": state["debug_logs"] + [f"strategy: {message}"],
        }

    parsed = extract_json(response.text)
    transactions = _validate_transactions(parsed)
    if parsed is None:
        logger.warning("strategy: response contained no parseable JSON block")

    logger.info(
        "strategy: %d token(s) in, %d out, %d transaction(s) proposed",
        response.usage.get("prompt_tokens", 0),
        response.usage.get("completion_tokens", 0),
        len(transactions),
    )

    return {
        "llm_analysis": response.text,
        "proposed_transactions": [t.model_dump() for t in transactions],
        "token_usage": response.usage,
        "llm_error": "",
        "debug_logs": state["debug_logs"] + ["strategy: plan received"],
    }


def format_json_output_node(state: AgentState) -> dict:
    """Print the consolidated, human-readable report."""
    logger.info("output: rendering final report")

    token_usage = state.get("token_usage") or {}
    report = {
        "summary": {
            "il_slots": state["il_slots_available"],
            "opponent_id": state["opp_id"],
            "system_decisions": state["decisions_list"],
        },
        "proposed_transactions": state.get("proposed_transactions", []),
        "llm_recommendations": state.get("llm_analysis", ""),
        "token_usage": token_usage,
        "debug_trace": state["debug_logs"],
    }

    print("\n" + "=" * 60)
    print(" FANTASY NBA MATCHUP PLAN ")
    print("=" * 60)

    if state.get("llm_error"):
        print(f"\n[LLM ERROR] {state['llm_error']}")

    transactions = report["proposed_transactions"]
    if transactions:
        print("\n[PROPOSED TRANSACTIONS -- manual review required]")
        for transaction in transactions:
            print(
                f" -> {str(transaction.get('type', '?')).upper()}: "
                f"{transaction.get('player')} "
                f"(Drop/Move: {transaction.get('drop') or 'None'})"
            )
            if transaction.get("instruction"):
                print(f"    Instruction: {transaction['instruction']}")
            print(f"    Reason: {transaction.get('reason') or 'N/A'}")
    else:
        print("\n[PROPOSED TRANSACTIONS] none -- no move justified this week.")

    print("\n[AI ANALYSIS]")
    print(report["llm_recommendations"] or "(empty)")

    if token_usage:
        total = int(token_usage.get("total_tokens", 0) or 0)
        model = (state.get("llm") or {}).get("model", "")
        rate = MODEL_COST_PER_MILLION.get(model, 0.10)
        print("\n[TOKEN USAGE]")
        print(f" -> Prompt:     {token_usage.get('prompt_tokens', 0)}")
        print(f" -> Completion: {token_usage.get('completion_tokens', 0)}")
        print(f" -> Total:      {total}")
        print(f" -> Est. cost:  ${(total / 1_000_000) * rate:.6f}")

    return state
