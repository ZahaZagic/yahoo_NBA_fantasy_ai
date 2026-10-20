"""Pure analysis helpers for the strategy chain.

Everything in this module is a side-effect-free transformation of data already
fetched by :mod:`src.nodes`. Keeping it separate from the graph nodes makes the
scoring logic unit-testable without any Yahoo or LLM calls.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: Categories compared in 9-cat leagues, in prompt order.
NINE_CAT_METRICS: Tuple[str, ...] = (
    "PTS", "REB", "AST", "STL", "BLK", "3PTM", "FG%", "FT%", "TO",
)

#: Additional rate stats we surface when comparing season-long history.
HISTORY_METRICS: Tuple[str, ...] = NINE_CAT_METRICS + ("FGM/A", "FTM/A")

#: Categories where a *lower* value is better.
INVERSE_CATEGORIES = frozenset({"TO"})

#: Yahoo roster statuses that mean a player cannot be relied upon to play.
INJURED_STATUSES = frozenset({"INJ", "O", "IL", "GTD", "SSPD", "SUSP"})


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_injured(player: Dict[str, Any]) -> bool:
    """Return True when the player is not reliably available."""
    status = str(player.get("Status") or player.get("status") or "OK")
    if status in INJURED_STATUSES:
        return True
    return any(marker in status for marker in ("INJ", "Out", "GTD", "SSPD"))


def average_history(history: Optional[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    """Average weekly team totals into a per-week profile.

    ``FGM/A`` and ``FTM/A`` are averaged as separate makes/attempts so the
    percentage context (volume) is preserved rather than averaged naively.
    """
    if not history:
        return {}

    sums = {metric: 0.0 for metric in NINE_CAT_METRICS}
    volumes = {"FGM": 0.0, "FGA": 0.0, "FTM": 0.0, "FTA": 0.0}
    weeks = 0

    for week in history:
        weeks += 1
        for metric in NINE_CAT_METRICS:
            if metric in week:
                sums[metric] += _to_float(week.get(metric))

        for key, prefix in (("FGM/A", "FGM"), ("FTM/A", "FTM")):
            raw = week.get(key, "")
            if "/" in str(raw):
                made, attempts = str(raw).split("/", 1)
                volumes[prefix] += _to_float(made)
                volumes[prefix[:-1] + "A"] += _to_float(attempts)

    if weeks == 0:
        return {}

    averages: Dict[str, Any] = {
        metric: round(sums[metric] / weeks, 3) for metric in NINE_CAT_METRICS
    }
    averages["FGM/A"] = (
        f"{round(volumes['FGM'] / weeks, 1)}/{round(volumes['FGA'] / weeks, 1)}"
    )
    averages["FTM/A"] = (
        f"{round(volumes['FTM'] / weeks, 1)}/{round(volumes['FTA'] / weeks, 1)}"
    )
    return averages


def _verdict(diff: float, metric: str) -> str:
    won = diff < 0 if metric in INVERSE_CATEGORIES else diff > 0
    return "WON" if won else "LOST"


def compare_history(
    my_average: Dict[str, Any], opponent_average: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """Compare season-long weekly averages against the opponent's."""
    comparison: Dict[str, Dict[str, Any]] = {}
    for metric in HISTORY_METRICS:
        mine = my_average.get(metric, 0)
        theirs = opponent_average.get(metric, 0)
        if isinstance(mine, str):
            comparison[metric] = {"Me": mine, "Opp": theirs, "Status": "INFO"}
            continue
        diff = round(_to_float(mine) - _to_float(theirs), 3)
        comparison[metric] = {
            "Me": mine,
            "Opp": theirs,
            "Diff": diff,
            "Status": _verdict(diff, metric),
        }
    return comparison


def compare_live(
    live_rows: Sequence[Dict[str, Any]], my_team_id: Any
) -> Dict[str, Dict[str, Any]]:
    """Compare the in-progress weekly matchup totals.

    The caller is responsible for having at least two team rows; a single row
    (e.g. a bye or an unparsed response) yields an empty comparison.
    """
    if len(live_rows) < 2:
        return {}

    my_row = next(
        (row for row in live_rows if str(row.get("team_id")) == str(my_team_id)),
        live_rows[0],
    )
    opponent_row = next(
        (row for row in live_rows if str(row.get("team_id")) != str(my_team_id)),
        None,
    )
    if opponent_row is None:
        return {}

    comparison: Dict[str, Dict[str, Any]] = {}
    for metric in NINE_CAT_METRICS:
        mine = _to_float(my_row.get(metric))
        theirs = _to_float(opponent_row.get(metric))
        diff = round(mine - theirs, 2)
        comparison[metric] = {
            "Me": mine,
            "Opp": theirs,
            "Diff": diff,
            "Status": _verdict(diff, metric),
        }
    return comparison


def categorize_roster(roster: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group the roster by (health x IL slot) so the model sees the real constraint."""
    groups: Dict[str, List[Dict[str, Any]]] = {
        "HEALTHY_ACTIVE_ROSTER": [],
        "HEALTHY_STUCK_IN_IL": [],
        "INJURED_ON_ACTIVE_ROSTER": [],
        "INJURED_IN_IL_SLOTS": [],
    }

    for player in roster:
        in_il_slot = player.get("selected_position") in ("IL", "IL+")
        entry = {
            "name": player.get("Player", "Unknown"),
            "player_id": player.get("player_id"),
            "status": player.get("Status", "OK"),
            "status_full": player.get("StatusFull", "OK"),
            "season_rank": player.get("Season Rank", "-"),
            "current_rank": player.get("Current Rank", "-"),
            "current_position": player.get("selected_position"),
            "injury_note": player.get("injury_note", ""),
            "has_game_today": player.get("has_game_today"),
            "per_game": {
                key: player.get(key)
                for key in ("MPG", "FGA", "PTS", "REB", "AST", "STL", "BLK", "3PTM", "TO")
                if key in player
            },
        }

        if is_injured(player):
            key = "INJURED_IN_IL_SLOTS" if in_il_slot else "INJURED_ON_ACTIVE_ROSTER"
        else:
            key = "HEALTHY_STUCK_IN_IL" if in_il_slot else "HEALTHY_ACTIVE_ROSTER"
        groups[key].append(entry)

    return groups


def compute_capacity(
    groups: Dict[str, List[Dict[str, Any]]], roster_rules: Dict[str, Any]
) -> Dict[str, int]:
    """Derive open standard slots and empty IL slots from rules + grouping."""
    total_standard = int(roster_rules.get("standard_slots", 13) or 13)
    total_il = int(roster_rules.get("il", 0) or 0) + int(
        roster_rules.get("il_plus", 0) or 0
    )

    used_standard = len(groups["HEALTHY_ACTIVE_ROSTER"]) + len(
        groups["INJURED_ON_ACTIVE_ROSTER"]
    )
    used_il = len(groups["HEALTHY_STUCK_IN_IL"]) + len(groups["INJURED_IN_IL_SLOTS"])

    return {
        "total_standard": total_standard,
        "used_standard": used_standard,
        "open_standard": max(0, total_standard - used_standard),
        "total_il": total_il,
        "used_il": used_il,
        "empty_il": max(0, total_il - used_il),
    }


def _compact_waiver_candidate(player: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a free-agent row to the fields the model actually reasons about.

    The raw rows carry pandas artefacts and duplicated keys; sending them
    verbatim roughly doubles the prompt size for no added signal.
    """
    keys = (
        "player_id", "Team", "% Owned", "playing_today",
        "Season Rank", "Current Rank", "MPG", "FGA",
        "PTS", "REB", "AST", "STL", "BLK", "3PTM", "TO",
    )
    return {"name": player.get("Player", "Unknown"), **{
        key: player.get(key) for key in keys if key in player
    }}


def compact_waiver_pool(
    free_agents: Iterable[Dict[str, Any]], limit: int = 25
) -> List[Dict[str, Any]]:
    """Take the top ``limit`` free agents, trimmed for the prompt."""
    trimmed: List[Dict[str, Any]] = []
    for player in list(free_agents)[:limit]:
        trimmed.append(_compact_waiver_candidate(player))
    return trimmed


def build_strategy_context(state: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the full prompt payload from a graph state snapshot."""
    history = state.get("historical_summary") or {}
    my_average = average_history(history.get("my"))
    opponent_average = average_history(history.get("opp"))

    groups = categorize_roster(state.get("roster") or [])
    roster_rules = state.get("roster_rules") or {}
    capacity = compute_capacity(groups, roster_rules)

    live_rows = (state.get("live_matchup_stats") or {}).get("stats") or []
    live_comparison = compare_live(live_rows, state.get("team_id"))

    return {
        "league_type": str(state.get("league_type", "9cat")).lower(),
        "roster_rules": roster_rules,
        "capacity": capacity,
        "live_comparison": live_comparison,
        "history_comparison": compare_history(my_average, opponent_average),
        "history_weeks": {
            "my": len(history.get("my") or []),
            "opp": len(history.get("opp") or []),
        },
        "roster_by_status": groups,
        "weekly_adds_used": int(state.get("weekly_adds_used", 0) or 0),
        "waiver_candidates": compact_waiver_pool(state.get("fa_today") or []),
    }
