"""Prompt definitions for the weekly strategy chain.

The prompt is split into a *system* message (role, hard constraints, output
contract -- stable across runs) and a *human* message (this week's data). This
separation is what makes the chain behave predictably: the rules stop competing
with 800 lines of JSON for the model's attention, and the data block can be
cached independently.
"""

from __future__ import annotations

import json
from typing import Any, Dict

#: Sections the model must produce, in order.
REPORT_SECTIONS = (
    "A) Matchup Win Plan",
    "B) Injury Status & Return Timelines",
    "C) Category / Points Risk Analysis",
    "D) Player Value Ranking",
    "E) Drop Priority",
    "F) Streaming Rule",
    "G) Add Priority",
    "H) Automation JSON",
)

_TRANSACTION_JSON_SCHEMA = """{
  "transactions": [
    {
      "type": "add | drop | activate | move",
      "player": "<name of the player being added / activated / moved to active>",
      "drop": "<name of the player dropped or moved to IL, or null>",
      "player_id": "<Yahoo player_id copied verbatim from the data, or null>",
      "drop_id": "<Yahoo player_id copied verbatim from the data, or null>",
      "instruction": "<optional, <= 20 words>",
      "reason": "<why, <= 25 words>"
    }
  ]
}"""

SYSTEM_PROMPT = f"""You are a Fantasy Basketball Matchup Optimization Agent. You advise a \
single manager on one head-to-head matchup at a time.

## ROLE
You receive a complete snapshot of the manager's roster, the live matchup, their \
season-long weekly averages, and an already-filtered free-agent pool. Your job is \
to convert that snapshot into a short, decisive, actionable plan that maximizes the \
probability of winning *this week's* matchup without destroying long-term value.

## HARD CONSTRAINTS (violating any of these makes the plan unusable)
1. DATA ONLY. Reason exclusively from the JSON in the user message. Never invent \
players, ranks, stats, injury news, or player_id values. If a field is missing, \
treat it as unknown and say so rather than guessing.
2. NEVER recommend dropping a player whose role is "Core Anchor".
3. NEVER suggest moving a player to IL if their `current_position` is already \
`IL` or `IL+`.
4. Roster legality is mandatory: the plan must respect OPEN ROSTER SPOTS and \
EMPTY IL/IL+ SPOTS exactly as reported. Count them; do not assume.
5. Adds are budgeted. Do not exceed the remaining weekly adds, and do not propose \
churn that does not measurably improve this week.
6. Percentages are fractions in [0, 1]. Counting stats are per-game unless the \
block is labelled as a weekly total. Preserve units when you quote numbers.
7. Output the report sections in the stated order, then exactly one JSON block. \
No text after the JSON block.

## DECISION PRIORITY (apply in this order)
1. FREE CAPACITY FIRST. If OPEN ROSTER SPOTS > 0, prefer an add with `drop: null`.
2. INTERNAL FIX BEFORE EXTERNAL. If a healthy player is stuck in IL and an \
injured player occupies a standard slot, use `type: "move"` to swap them. This is \
not a drop.
3. DROP ONLY WHEN FORCED. Recommend a drop only when no roster spot can be opened \
by an IL move. When you must drop, drop the lowest-ranked Streamer whose loss \
costs the least in the categories you are actually contesting.
4. Then, and only then, stream from the free-agent pool.

## CLASSIFICATION RUBRIC
* Role by Season Rank: Core Anchor < 70; Solid Asset 70-130; \
Streamer / Filler > 130 or unranked. A player whose strength is one category \
(9-cat) or who has a reliable floor (points) may also be tagged Specialist.
* Category status (9-cat) is one of: Safe, Key, Swing, Lost --
  - Safe: already winning by a margin the opponent cannot close with games left.
  - Key: close, must be protected.
  - Swing: realistically flippable this week -- prioritize these.
  - Lost: not recoverable this week -- do not spend adds chasing it.
* Always report the numeric +/- delta that justifies the label.
* NO EVIDENCE IS NOT A WEAKNESS. If the season block says NO SEASON BASELINE, or a \
metric's status is TIED / NO_DATA, you have no information about it. Say so plainly \
and do not label that category Core/Swing/Lost, quote a delta, or claim the roster \
is deficient in it. This is the normal state at the start of a season and in week 1 \
of a matchup -- reason instead from roster composition, games remaining, and the \
live score.
* A TIED live matchup with all-zero totals means the scoring period has not started. \
Do not read it as a 0-0 loss.
* GTD means UNCERTAIN. Recommend activating a GTD player only if they play today, \
and prefer parking a confirmed Out (INJ) player in that IL slot instead.

## STYLE
* Be terse. Do not restate the input data; reference players by name.
* One sentence per ranking entry. Justify, don't narrate.
* No hedging, no filler, no apologies. State the recommendation and the number \
behind it.
* If the data is too thin to support a recommendation, say exactly what is missing \
and emit `{{"transactions": []}}`.

## OUTPUT CONTRACT
Produce these sections, in this order:

{chr(10).join('* ' + section for section in REPORT_SECTIONS)}

Section H must be a single fenced ```json block matching exactly this shape \
(keys only, no commentary inside the block):

```json
{_TRANSACTION_JSON_SCHEMA}
```

Rules for Section H:
* Emit `{{"transactions": []}}` if no move is justified. An empty plan is a valid \
and often correct answer.
* Copy `player_id` / `drop_id` verbatim from the data. If the id is unknown, use \
null rather than inventing one.
* Every transaction must be independently executable in the order listed."""

_LEAGUE_OBJECTIVES: Dict[str, str] = {
    "9cat": (
        "Objective: maximize the probability of winning THIS WEEK's head-to-head "
        "9-category matchup (FG%, FT%, 3PTM, PTS, REB, AST, STL, BLK, TO). "
        "Categories are won individually -- a 5-4 win is worth the same as 9-0. "
        "Prioritize flippable (Swing) categories over defending already-Safe ones."
    ),
    "points": (
        "Objective: maximize total fantasy points scored THIS WEEK in a "
        "head-to-head points league. Value is purely total point volume: games "
        "remaining, minutes, usage, and per-game scoring all matter. "
        "Category balance is irrelevant here."
    ),
}

_LEAGUE_CONTEXT: Dict[str, str] = {
    "9cat": "- Format: 9-cat H2H categories (FG%, FT%, 3PTM, PTS, REB, AST, STL, BLK, TO)",
    "points": "- Format: H2H points league (total fantasy points only)",
}


def _json_block(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _history_block(context: Dict[str, Any]) -> str:
    """Render the season-baseline section, including the no-baseline case.

    At the start of a season there are no completed weeks, so there is nothing to
    compare. Saying so explicitly is far better than handing the model a table of
    zeros, which it would read as "losing every category".
    """
    weeks = context.get("history_weeks") or {}
    mine = int(weeks.get("my", 0) or 0)
    theirs = int(weeks.get("opp", 0) or 0)
    comparison = context.get("history_comparison") or {}

    if not comparison:
        return (
            "NO SEASON BASELINE AVAILABLE — there are no completed weeks yet "
            f"(mine: {mine}, opponent: {theirs}).\n"
            "Do not invent a baseline and do not describe any category as a "
            "weakness or strength on historical grounds. Reason only from the "
            "imported roster, the live matchup, and the schedule."
        )

    header = (
        f"Based on {mine} completed week(s) for me and {theirs} for the opponent.\n"
        "Status values: WON / LOST are decided by the sign of Diff (Turnovers "
        "inverted); TIED means the gap is zero so far; INFO means a volume stat "
        "reported as makes/attempts; NO_DATA means the metric is missing on at "
        "least one side and must not be treated as a zero."
    )
    if mine == 0 or theirs == 0:
        header += (
            "\nCAUTION: one side has no completed weeks, so the sample is "
            "asymmetric. Treat this comparison as weak evidence."
        )
    return header + "\n" + _json_block(comparison)


def build_human_prompt(context: Dict[str, Any], current_date: str) -> str:
    """Render the per-week data payload for the human turn.

    Args:
        context: Output of :func:`src.analysis.build_strategy_context`.
        current_date: Human-readable date, e.g. ``"February 15, 2026"``.

    Returns:
        The formatted user message.
    """
    league_type = context.get("league_type", "9cat")
    objective = _LEAGUE_OBJECTIVES.get(league_type, _LEAGUE_OBJECTIVES["9cat"])
    league_context = _LEAGUE_CONTEXT.get(league_type, _LEAGUE_CONTEXT["9cat"])

    capacity = context.get("capacity", {})
    groups = context.get("roster_by_status", {})
    rules = context.get("roster_rules", {})

    max_adds = int(rules.get("max_adds", 0) or 0)
    adds_used = int(context.get("weekly_adds_used", 0) or 0)
    adds_remaining = max(0, max_adds - adds_used) if max_adds else "unlimited"

    return f"""Today is {current_date}.
League type: {league_type.upper()}

{objective}

{league_context}
- Weekly matchup, possibly mid-week.
- Adds are a limited resource; churn has a cost.

## 1. ROSTER RULES AND CURRENT CAPACITY
- Standard roster capacity: {capacity.get('total_standard')} slots
- Occupied standard slots: {capacity.get('used_standard')}
- OPEN ROSTER SPOTS: {capacity.get('open_standard')}
- Total IL/IL+ capacity: {capacity.get('total_il')} slots
- Occupied IL/IL+ slots: {capacity.get('used_il')}
- EMPTY IL/IL+ SPOTS: {capacity.get('empty_il')}
- Healthy players stuck in IL: {len(groups.get('HEALTHY_STUCK_IN_IL', []))}
- Injured players on the active roster: {len(groups.get('INJURED_ON_ACTIVE_ROSTER', []))}
- Weekly adds used: {adds_used} of {max_adds or 'n/a'} (remaining: {adds_remaining})

## 2. LIVE MATCHUP (current week, in progress)
{_json_block(context.get('live_comparison', {}))}

## 3. SEASON-LONG WEEKLY AVERAGES vs OPPONENT
{_history_block(context)}

## 4. ROSTER BY HEALTH AND SLOT
{_json_block(groups)}

## 5. FREE AGENTS AVAILABLE (pre-filtered: healthy, available, ranked)
{_json_block(context.get('waiver_candidates', []))}

## 6. REQUIRED REASONING ("step1_reasoning")
Work through these before writing the report:

STEP 1 -- {"Category landscape: label every category Safe / Key / Swing / Lost with its +/- delta." if league_type == "9cat" else "Point-volume analysis: total points gap, games remaining, and per-game scoring by player."}
STEP 2 -- Risk analysis: compare the LIVE gap against the SEASONAL average gap per \
category and state whether we are over-performing, under-performing, or tracking the trend.
STEP 3 -- Role classification for every roster player (Core Anchor / Solid Asset / \
Streamer / Specialist) using the rubric.
STEP 4 -- Rank ALL roster players from most to least valuable for THIS WEEK ONLY, \
with a one-sentence justification each.
STEP 5 -- Drop priority, highest risk first, with the safeguard from the system \
rules applied.
STEP 6 -- Streaming and free-agent logic: a primary stream target for today plus a \
ranked add list, bounded by remaining adds and open spots.

Now produce the report sections A through H exactly as specified."""
