"""Shared LangGraph state for the Fantasy NBA decision workflow."""

from typing import Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    """State passed between graph nodes.

    Marked ``total=False`` so nodes can return partial updates, which is how
    LangGraph merges node outputs into the running state.
    """

    # --- Inputs -----------------------------------------------------------
    query: Any  # YahooFantasySportsQuery instance (not serialisable)
    league_key: str
    league_id: str
    team_id: str
    league_type: str  # "9cat" or "points"
    league_metadata: Dict[str, Any]
    llm: Dict[str, Any]  # resolved config["llm"] block
    today_str: str

    # --- Fetched data -----------------------------------------------------
    roster: List[Dict[str, Any]]
    historical_summary: Dict[str, Any]
    live_matchup_stats: Dict[str, Any]
    fa_today: List[Dict[str, Any]]
    fa_tomorrow: List[Dict[str, Any]]

    # --- Derived rules ----------------------------------------------------
    opp_id: str
    il_slots_available: int
    roster_rules: Dict[str, Any]  # {"il": 3, "il_plus": 2, "max_adds": 7, ...}
    weekly_adds_used: int

    # --- Outputs ----------------------------------------------------------
    decisions_list: List[str]
    llm_analysis: str
    llm_error: str
    proposed_transactions: List[Dict[str, Any]]
    token_usage: Dict[str, int]
    debug_logs: List[str]
