# Technical Development Guide

Technical reference for the Fantasy NBA Agent: a read-only Python CLI that pulls Yahoo Fantasy
basketball data, derives deterministic roster facts, and asks an LLM for a weekly plan.

## Repository Overview

The repository is one runnable Python application under `src/`; there is no web, iOS, or secondary
subproject.

```text
fantasy/
├── README.md · requirements.txt · .gitignore
├── config.example.json          # template; copy to config.json (git-ignored)
├── .env.example                 # template; copy to .env (git-ignored)
├── auth/.gitkeep                # OAuth artefacts live here, never committed
├── docs/                        # README, business logic, ops, this guide
├── src/
│   ├── main.py                  # CLI entry point + graph assembly
│   ├── nodes.py                 # 5 LangGraph nodes + fetch helpers
│   ├── state.py                 # AgentState TypedDict
│   ├── analysis.py              # pure, side-effect-free scoring helpers
│   ├── schemas.py               # Pydantic models for LLM output
│   ├── prompts/strategy.py      # SYSTEM_PROMPT + build_human_prompt (__init__ re-exports)
│   └── utils/                   # config_loader · llm · yahoo_api · stats_parser · scheduler
└── tests/                       # unittest suite + sys.path bootstrap
```

Supported invocations, both from the repository root:

```bash
python -m src.main --profile my_account [--verbose]
python src/main.py --profile my_account [--verbose]
```

Third-party imports are limited to `yfpy>=17.0.0`, `requests`, `python-dotenv`, `langgraph`,
`langchain-core`, `langchain-openai`, `pandas`, `tabulate`, and `pydantic>=2.0.0`.

## Architecture

A five-node LangGraph `StateGraph` compiled at runtime. Upstream of the graph is setup; downstream is
console output. There are exactly two data sources (Yahoo Fantasy API, ESPN scoreboard) and one model
endpoint.

```text
                     python src/main.py [--profile P] [--verbose]
                                     │
                        configure_logging() · load_config(P)   .env > config.json > defaults
                                     │                         ConfigError -> exit 2
                        prepare_auth_dir(P) -> auth/<P>/       legacy auth/token.json migrated
                        load_access_token() -> dict | None     None when auth/<P>/.env exists
                        _build_query() -> YahooFantasySportsQuery ──► Yahoo OAuth
                        detect_game_key_and_week() -> (key, week) ◄─ 2 REST calls; fallback 466/1
                        query.game_id = game_key
                                     │ initial AgentState
                        StateGraph(AgentState).compile()
                                     │
  ┌──────────────────────────────────▼───────────────────────────────────────────────┐
  │ [1] fetch_data_node        league settings · adds used · roster · live matchup    │
  │                            · history (me + opp) · free agents (R_PO + OR, merged) │
  │                            · ESPN "who plays today"                               │
  │ [2] optimize_roster_node   greedy lineup assignment; PRINTS ONLY, writes nothing  │
  │ [3] analyze_injuries_node  rule-based IL/injury notes -> decisions_list           │
  │ [4] llm_strategy_node      build_strategy_context -> build_human_prompt           │
  │                            -> StrategyLLM.invoke(SYSTEM_PROMPT, human) ──► model  │
  │                            -> extract_json -> TransactionPlan -> state            │
  │ [5] format_json_output_node  plan · AI analysis · token usage + estimated cost    │
  └──────────────────────────────────┬───────────────────────────────────────────────┘
                                     ▼
                                    END
```

Edges are strictly linear (`fetch → optimize_roster → analyze_injuries → llm_strategy → format_output →
END`) with no conditional routing, so the graph is a readable pipeline: LangGraph contributes state
merging and a trace, not control flow, and a gate or retry edge can be added without touching node
bodies. Ordering note: `optimize_roster_node` runs before `analyze_injuries_node` and neither mutates
the roster, so injury notes cannot influence the lineup assignment in the same run.

## Module Reference

### `src/main.py`

Entry point and composition root: process concerns (arguments, logging, exit codes, auth layout) plus
graph assembly. No analysis logic lives here.

| Function | Responsibility |
| --- | --- |
| `configure_logging(verbose)` | One `basicConfig`; `DEBUG` when verbose else `INFO`. Forces `urllib3`, `httpx`, `openai`, `httpcore` to `WARNING`. |
| `prepare_auth_dir(profile_name)` / `load_access_token(profile_dir)` | Creates `auth/<profile>/` and copies a legacy `auth/token.json` there once. Returns the cached token dict, or `None` when the file is absent/malformed or when `auth/<profile>/.env` exists (yfpy 17 writes refreshed tokens there, so stale JSON must not win). |
| `_build_query(...)` | yfpy 17 keyword API: `yahoo_consumer_key`, `yahoo_consumer_secret`, `yahoo_access_token_json`, `env_var_fallback=True`, `env_file_location=auth_dir`, `save_token_data_to_env_file=True`, `browser_callback=True`, `offline=False`. |
| `detect_game_key_and_week(query, league_id, game_code)` | Two `query.oauth.session.get` calls (`format=json`) on `/game/{game_code}` and `/league/{game_key}.l.{league_id}/settings`; returns `(game_key, current_week)`. |
| `build_graph()` / `run_fantasy_app(profile_name)` | Register five nodes and wire linear edges; then load config, resolve auth, build the query and identifiers, build the initial `AgentState`, invoke the graph, return the final state. |
| `main(argv)` | argparse (`--profile`, `--verbose`). Exit codes: `0` success, `2` `ConfigError`, `1` other exception, `130` interrupt. |

Design decisions: auth is **per profile**, so switching `--profile` cannot reuse another account's
token; **`query.game_id` is assigned manually** after resolution because `yahoo_api` reads it to build
`player_key`/`team_key` strings (`_build_query` is deliberately called with `game_id=None`); failures
**degrade rather than abort** (key `466`, week `1`, warning logged); exit code `2` always means "fix
your configuration".

### `src/nodes.py`

The only module that touches both I/O helpers and the LLM. Each node receives `AgentState` and returns
a partial dict that LangGraph merges.

| Node | Writes |
| --- | --- |
| `fetch_data_node` | `roster`, `opp_id`, `live_matchup_stats`, `il_slots_available`, `roster_rules`, `weekly_adds_used`, `fa_today`, `historical_summary`, `debug_logs` |
| `optimize_roster_node` / `analyze_injuries_node` | `debug_logs` (prints the plan) / `decisions_list`, `debug_logs` |
| `llm_strategy_node` | `llm_analysis`, `proposed_transactions`, `token_usage`, `llm_error`, `debug_logs` |
| `format_json_output_node` | none (returns state unchanged after printing) |

Constants: `DEFAULT_ROSTER_RULES`, `MODEL_COST_PER_MILLION`. Private helpers:
`_fetch_roster_rules(query, league_key)` (reads `max_weekly_adds`, separates `IL`/`IL+`/standard slots;
zeros on failure), `_fetch_weekly_adds_used(query, league_key, team_id)` (reads `roster_adds.value`;
`0` on failure), `_validate_transactions(raw_plan)` (accepts only a `dict`, validates as
`TransactionPlan`, returns `[]` on failure).

Notable details:

- **Free-agent merge.** The pool is fetched twice (`sort_type="R_PO"` and `"OR"`), deduplicated on
  `player_id` (falling back to the decorated `Player` name), and sorted by `% Owned` descending; each
  sort surfaces different players.
- **Lineup assignment is a three-pass greedy fill.** Candidates need `has_game_today == 1`,
  `is_editable == 1`, `Status == "OK"`, ranked by raw `PTS`. Pass 1 fills specialists (`PG`, `SG`,
  `SF`, `PF`, two `C`), pass 2 the flexible `G`/`F` (`G` takes `PG`/`SG`, `F` takes `SF`/`PF`), pass 3
  two `Util` slots; bench marks unassigned editable players at roster index `< 13` as `BN`.
- **Injury status sets differ.** This node reacts only to `INJ` and `O`, while
  `analysis.INJURED_STATUSES` also covers `IL`, `GTD`, `SSPD`, `SUSP`.
- **LLM failure is data, not an exception.** A missing key or `LLMError` populates `llm_error` and
  leaves `proposed_transactions` empty, so the report still renders.
- Imports use `try: from .x import ... except ImportError: from x import ...`, so the module loads both
  as `src.nodes` and as a top-level `nodes` module.

### `src/state.py`

`AgentState(TypedDict, total=False)` is the single state contract. `total=False` is what lets each node
return only the keys it changed for LangGraph to merge. Fields group by origin: inputs (`query`,
`league_key`, `league_id`, `team_id`, `league_type`, `league_metadata`, `llm`, `today_str`), fetched
data (`roster`, `historical_summary`, `live_matchup_stats`, `fa_today`, `fa_tomorrow`), derived rules
(`opp_id`, `il_slots_available`, `roster_rules`, `weekly_adds_used`), outputs (`decisions_list`,
`llm_analysis`, `llm_error`, `proposed_transactions`, `token_usage`, `debug_logs`).

`query` is typed `Any` deliberately: it holds a live `YahooFantasySportsQuery` with an OAuth session
that is neither serialisable nor checkpointable — the main reason the graph is compiled without a
checkpointer. Two fields are inert: `fa_tomorrow` is initialised to `[]` and never populated, and
`today_str` is set but never read (nodes re-derive the date locally).

### `src/analysis.py`

Pure, side-effect-free transformations over data already fetched: no network, no printing, no state
writes. That is what makes the scoring rules unit-testable.

| Function | Contract |
| --- | --- |
| `is_injured(player)` | `True` when `Status`/`status` is in `INJURED_STATUSES` or contains `INJ`, `Out`, `GTD`, `SSPD`; defaults to `"OK"`. |
| `average_history(history)` | Weekly totals → per-week averages for `NINE_CAT_METRICS`; `FGM/A`/`FTM/A` average makes and attempts separately, then re-render as `"m/a"`, preserving shooting volume. `{}` for empty input. |
| `compare_history(...)` / `compare_live(...)` | Per-metric `{Me, Opp, Diff, Status}` over `HISTORY_METRICS` (string volume metrics become `Status: "INFO"`); the live comparison returns `{}` with fewer than two rows or no opponent row, and falls back to the first row when the id matches nothing. |
| `categorize_roster(roster)` | Buckets `HEALTHY_ACTIVE_ROSTER`, `HEALTHY_STUCK_IN_IL`, `INJURED_ON_ACTIVE_ROSTER`, `INJURED_IN_IL_SLOTS`; each entry a trimmed projection (name, ids, status, ranks, slot, injury note, game-today, per-game stats). |
| `compute_capacity(groups, roster_rules)` | Derives `open_standard` and `empty_il`, clamped at zero so a stale snapshot cannot yield negative capacity. |
| `compact_waiver_pool(free_agents, limit=25)` | Top-`limit` free agents reduced to the fields the model reasons about (`_compact_waiver_candidate`). |
| `build_strategy_context(state)` | Assembles the prompt payload: league type, rules, capacity, live comparison, history comparison and week counts, roster grouping, adds used, compacted pool. |

Constants: `NINE_CAT_METRICS`, `HISTORY_METRICS` (nine categories plus `FGM/A`, `FTM/A`),
`INVERSE_CATEGORIES` (`{"TO"}` — lower is better), `INJURED_STATUSES`. `_verdict(diff, metric)` is the
only place inverse-category logic lives.

### `src/schemas.py`

Pydantic v2 models for the model's structured output:

- `Transaction` — `type` constrained to `Literal["add", "drop", "activate", "move"]`, `player` required
  and non-empty, optional `drop`, `player_id`, `drop_id`, `instruction`, `reason`; `extra="ignore"`
  tolerates volunteered keys. Two `mode="before"` validators replace scattered node cleanup:
  `_blank_to_none` maps `""`, `"null"`, `"none"`, `"n/a"`, `"na"`, `"-"` to `None`; `_strip_name` trims
  names.
- `TransactionPlan` — `transactions: List[Transaction]` defaulting to `[]`, so `{}` or a missing key is
  a valid empty plan. The strategy node validates entry-by-entry rather than via `TransactionPlan`, so
  one malformed entry cannot void an otherwise usable plan; `TransactionPlan` remains the whole-plan
  contract used by tests. `TransactionType` is exported for callers branching on action type.

### `src/prompts/strategy.py`

Prompt construction only — no I/O, no state access. `SYSTEM_PROMPT` holds role, seven hard constraints,
a four-step decision priority, a classification rubric, style rules, the output contract, and ordered
sections `A) Matchup Win Plan` … `H) Automation JSON` (`REPORT_SECTIONS`); section H must be one fenced
`json` block matching `_TRANSACTION_JSON_SCHEMA`. `build_human_prompt(context, current_date)` renders
the weekly payload: objective, capacity block, live comparison, season-long comparison with week
counts, roster-by-health-slot grouping, compacted free agents, and a six-step reasoning checklist.
`_LEAGUE_OBJECTIVES` / `_LEAGUE_CONTEXT` hold `"9cat"` and `"points"` variants (unknown types fall back
to `9cat`): `9cat` targets category-by-category analysis and prioritises *Swing* categories; `points`
targets point volume and states that category balance is irrelevant.

Design decision: the prompt is split along the system/human boundary — stable rules and the contract in
the system message, volatile weekly data in the human message — so a few hundred tokens of rules do
not compete with a large JSON payload for attention. The f-string escapes its braces (`{{`/`}}`)
around the JSON example, and `test_system_prompt_has_no_unrendered_placeholders` fails if that
regresses.

### `src/utils/config_loader.py`

Fixed precedence: environment variables (optionally seeded from `.env`) > `config.json` > defaults.
`load_config(profile_name=None, config_path=None)` returns a flat dict (`profile_name`, `client_id`,
`client_secret`, `league_id`, `team_id`, `game_code`, `league_type`) plus a nested `llm` block
(`api_key`, `base_url`, `model`, `temperature`, `max_tokens`, `timeout_seconds`, `max_retries`).

- `ConfigError` covers malformed JSON, a non-object top level, an unknown profile (listing available
  names), an invalid `league_type`, and each missing identifier (naming the matching env var).
- Env aliases: `YAHOO_CONSUMER_KEY`/`YAHOO_CLIENT_ID`, `YAHOO_CONSUMER_SECRET`/`YAHOO_CLIENT_SECRET`,
  `YAHOO_LEAGUE_ID`, `YAHOO_TEAM_ID`, `YAHOO_GAME_CODE`, `LEAGUE_TYPE`,
  `LLM_API_KEY`/`OPENROUTER_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`.
- `_merge_llm(config)` starts from `DEFAULT_LLM`, overlays `config["llm"]`, then lets env win for
  `api_key`, `base_url`, and `model` only; `temperature`, `max_tokens`, `timeout_seconds`, and
  `max_retries` are file/default-only. Legacy `gemini_api_key` and `llm_api_key` are honoured as
  API-key fallbacks.
- Pure-env mode: with no `config.json` (or no `profiles`), the profile is `{}` and everything resolves
  from the environment; a profile mismatch only errors when profiles exist.
- `_load_dotenv_once()` calls `load_dotenv(PROJECT_ROOT/".env", override=False)`, so a real env var
  beats the file and a missing `python-dotenv` still leaves env-only mode working. `PROJECT_ROOT`
  derives from `__file__` (`parents[2]`), so `.env` and `auth/` are found regardless of the working
  directory.

### `src/utils/llm.py`

The only module that talks to a model provider: a thin wrapper over `langchain_openai.ChatOpenAI`
against an OpenAI-compatible endpoint (OpenRouter by default).

| Symbol | Responsibility |
| --- | --- |
| `StrategyLLM(...)` / `.invoke(system_prompt, human_prompt)` | Constructor raises `LLMError` immediately when `api_key` is falsy and sends `HTTP-Referer`/`X-Title` headers. `invoke` sends `SystemMessage` + `HumanMessage`, flattens list-shaped content blocks, and returns `LLMResponse`; any provider exception is re-raised as `LLMError` with the original type in the message. |
| `LLMResponse` | Dataclass: `text`, `usage: Dict[str, int]`, `model`. |
| `extract_json(text)` / `_parse_first_object(candidate)` | Try fenced blocks **newest first**, then the whole string, returning the first parseable object or `None` (so a section-H answer beats an echoed schema example); the scanner is string-aware and balanced-brace, advancing to the next `{` after a failed parse so one malformed object cannot hide a valid later one. |
| `_normalise_usage(raw)` | Maps LangChain `usage_metadata` (`input_tokens`/`output_tokens`/`total_tokens`) onto `prompt_tokens`/`completion_tokens`/`total_tokens`. |
| `build_llm(llm_config)` | Builds a `StrategyLLM` from a resolved `config["llm"]` block, defaulting the model to `google/gemini-2.0-flash-001`. |

`max_retries` is handed to `ChatOpenAI`, which retries 429/5xx with exponential backoff; there is no
custom retry loop. Using LangChain instead of raw `requests` also buys standardised token accounting
and message objects.

### `src/utils/yahoo_api.py`

Raw Yahoo Fantasy v2 REST calls through `query.oauth.session`, so OAuth signing and token refresh stay
inside yfpy. Functions return plain lists/dicts and also print pandas/tabulate tables for interactive
use.

| Function | Behaviour |
| --- | --- |
| `get_team_roster(query, league_id, team_id_num, team_name=None)` | Two-phase: roster keys + selected positions, then a batched season-stats call. Derives `player_id`, `eligible_positions`, `is_editable`, `has_game_today` (ESPN schedule, native flag as fallback), `Season Rank`/`Current Rank`, `Status`, per-game averages (`MPG`, `FGM/A`, `FTM/A`, counting stats). |
| `get_free_agents_by_date(query, league_key, target_date_str=None, sort_type="R_PO")` | Pages the FA list in three batches of 25, then fetches season stats in chunks of 25. Skips waivers (`status == "W"`) and anything not `OK`, filters to teams playing on the target date, attaches `% Owned`, ranks, per-game averages, `playing_today`, and appends ` (REB+)`/` (BLK+)` to names. |
| `get_historical_team_summary(...)` / `get_live_matchup(...)` | Walk `/team/{key}/matchups`; history keeps `postevent` weeks strictly before `current_week` (newest first) and parses `team_stats`; the live call returns `(opponent_id, rows)` for the requested week or `(None, [])`. |
| `update_roster_positions(...)` / `execute_transaction(...)` | Build the XML roster payload for `{player_id: position}` (`PUT`) and add/drop/activate payloads (`POST`); internal `move` transactions delegate to `update_roster_positions` with `{player_id: "BN", drop_id: "IL"}`. Both return `bool`. |

Design decisions: **`query.game_id` is the key source of truth** (callers pass numeric
`league_id`/`team_id`; keys compose as `f"{game_key}.l.{league_id}.t.{team_id}"`, with `"466"`
substituted defensively when `query.game_id` is implausible); **broad exception handling with empty
fallbacks** — every fetcher returns `[]`/`(None, [])`; **ESPN schedule beats the native flag** when
available, since Yahoo's `is_playing_today` is less reliable across date boundaries; and **the write
helpers exist but are unused** — both are functional yet no node or entry point calls them.

### `src/utils/stats_parser.py`

Normalises Yahoo's nested, list-of-dicts JSON into flat dicts keyed by readable names.

- `STAT_MAPPING` — `stat_id` → name (`5`→`FG%`, `8`→`FT%`, `10`→`3PTM`, `12`→`PTS`, `13`/`15`→`REB`,
  `16`→`AST`, `17`→`STL`, `18`→`BLK`, `19`→`TO`, `0`→`GP`, `2`→`MIN`, `28`→`MIN_AVG`,
  `9004003`→`FGM/A`, `9007006`→`FTM/A`).
- `robust_flatten(item)` — recursive flatten that *preserves* a whitelist of nested keys (`stats`,
  `player_stats`, `teams`, `matchups`, `name`, `percent_owned`, `status`, `eligible_positions`,
  `player_id`, `ranks`, …) instead of dissolving them, and drops `is_keeper`/`is_flex`.
- `parse_raw_stats_list(stats_data)` — accepts a list or a `{"0": {...}, "count": n}` dict, maps ids,
  converts `mm:ss` to minutes, coerces `"-"`/`""` to `0.0`, and synthesises `FGM/A`/`FTM/A` from stat
  ids `4`/`3` and `7`/`6` when composite ids are absent.
- `get_mapped_status(p_flat)` — prefers a multi-character `status` code, else maps `status_full` text
  to `INJ`, `O`, `GTD`, `SSPD`, or `OK`.

Per-field `try/except` blocks mean one malformed stat never discards the rest of a row.

### `src/utils/scheduler.py`

`get_active_nba_teams(target_date=None)` queries the ESPN NBA scoreboard (with `?dates=` when a date is
given, hyphens stripped) and returns team abbreviations, translating ESPN's to Yahoo's (`GS`→`GSW`,
`NY`→`NYK`, `NO`→`NOP`, `SA`→`SAS`, `UTAH`→`UTA`, `PHO`→`PHX`, `BK`→`BKN`, `CH`→`CHA`). Timeout is
10 s; failure prints a warning and returns `[]`, which callers read as "schedule unknown".

### `tests/`

`unittest`, not pytest. `tests/__init__.py` puts `src/` at the front of `sys.path`, so tests import
modules exactly as the application does (`import analysis`, `from utils.llm import ...`).

## Configuration and Secrets

Precedence, highest first: environment (real env wins; `.env` only fills gaps via `override=False`) >
`config.json` root (`profiles.<name>` plus the top-level `llm` block) > built-in defaults.

| Setting | Env var(s) | Default |
| --- | --- | --- |
| Yahoo consumer key | `YAHOO_CONSUMER_KEY`, `YAHOO_CLIENT_ID` | none (required) |
| Yahoo consumer secret | `YAHOO_CONSUMER_SECRET`, `YAHOO_CLIENT_SECRET` | none (required) |
| League / team id | `YAHOO_LEAGUE_ID`, `YAHOO_TEAM_ID` | none (required) |
| Game code | `YAHOO_GAME_CODE` | `nba` |
| League type | `LEAGUE_TYPE` | `9cat` (valid: `9cat`, `points`) |
| LLM API key | `LLM_API_KEY`, `OPENROUTER_API_KEY` | none (warns; strategy step skipped) |
| LLM base URL / model | `LLM_BASE_URL`, `LLM_MODEL` | OpenRouter / `google/gemini-2.0-flash-001` |
| LLM temperature / max tokens / timeout / retries | — | `0.2` / `4000` / `90` / `3` |

`config.example.json` shows the shape: `default_profile`, a top-level `llm` block, and a `profiles` map
whose entries hold `league_id`, `team_id`, `game_code`, `league_type` (Yahoo credentials may live there
too or come from env).

- `config.json`, `.env`, and everything under `auth/` are git-ignored (except `auth/.gitkeep` and
  `.env.example`); nothing secret is tracked.
- OAuth state is per profile: `auth/<profile>/token.json` (legacy/cached) and `auth/<profile>/.env`
  (yfpy 17's refreshed token store). A legacy `auth/token.json` is migrated once then left alone, and
  `load_access_token` returns `None` once the per-profile `.env` exists, so a stale JSON token is never
  replayed.
- Configuration resolves once, before any network call, so a missing identifier fails fast with an
  actionable message and exit code `2`.

## Data Flow

1. **Arguments and logging.** `--profile` defaults to `config.json`'s `default_profile`, then
   `"default"`; `--verbose` selects `DEBUG`. `load_config` resolves the flat config dict, and a
   `ConfigError` exits `2`.
2. **Auth and query.** `auth/<profile>/` is created, a legacy token migrated, the cached token read or
   suppressed, and one `YahooFantasySportsQuery` built with the per-profile env file location.
3. **Identifiers.** Two REST calls yield `game_key` and `current_week`; `query.game_id` is set and
   `league_key = f"{game_key}.l.{league_id}"`.
4. **Graph invocation** with an explicit initial `AgentState` (empty collections, zeroed `token_usage`,
   `roster_rules` seeded as `{"il": 0, "il_plus": 0, "max_adds": 7, "standard_slots": 13}`).
5. **Fetch.** Settings, adds used, roster, live matchup, both history summaries, and the merged
   free-agent pool land in state; ESPN is consulted for who plays today.
6. **Optimise and analyse.** A read-only greedy lineup prints (only `debug_logs` changes); rule-based
   injury notes append to `decisions_list`.
7. **Strategy.** `build_strategy_context` → `build_human_prompt` → `StrategyLLM.invoke` →
   `extract_json` → `_validate_transactions` → `proposed_transactions` (as dicts), plus `llm_analysis`,
   `token_usage`, `llm_error`.
8. **Report and return.** Header, any LLM error, transactions with reasons, full AI analysis, token
   usage with estimated cost; `run_fantasy_app` returns the final state and `main` maps the outcome to
   an exit code.

Failure isolation is explicit at every stage: unresolved identifiers degrade to defaults, Yahoo fetches
degrade to empty collections, LLM failures become `llm_error` plus an empty plan. The only fatal
condition is an unusable configuration. Today's transaction lifecycle ends at the print:

```text
LLM JSON block
  -> extract_json()             newest fenced block, else first {..} object, or None
  -> _validate_transactions()   per-entry Pydantic: type enum, non-empty player, blank -> None
  -> state["proposed_transactions"]
  -> format_json_output_node    "[PROPOSED TRANSACTIONS -- manual review required]"
  x  no execution step: update_roster_positions / execute_transaction are never called
```

## Core Implementation Patterns

**Why `analysis.py` is separate from `nodes.py`.** Nodes mix I/O, printing, and state merging; scoring
is pure arithmetic over already-fetched dicts. Keeping them apart means 21 of 96 tests exercise real
decision logic with no Yahoo session, API key, or HTTP, and gives one authoritative definition of
inverse-category handling, capacity arithmetic, and injury-status membership instead of duplicating
them per node and in the prompt text.

**Why `prompts/` is a module, not string literals.** The contract (sections A–H, JSON shape, hard
constraints) is product logic that needs review and test assertions: tests verify every declared
section appears, the drop safeguard is stated, no placeholder is unrendered, and both league objectives
render. A module also keeps the system/human split explicit — stable rules in one constant, volatile
data in one builder.

**Why Pydantic validation before anything acts on the plan.** The model returns prose with an embedded
JSON block. `extract_json` guarantees only that *some* object parsed; `TransactionPlan` guarantees the
shape — an allowed `type`, a non-blank `player` — and normalises the many spellings of "no value".
Extra keys are ignored so a chatty model does not break the run. Failure returns an empty plan and logs
a warning, so a hallucinated payload never reaches the printing path as a half-valid instruction. Since
execution is the obvious next capability, this boundary is the one thing that must stay strict.

**Why env-first configuration.** The repository is safe to clone and run: no secret needs a tracked
file. Env > file > default also matches how the runtime is operated (a shell or CI injects env), and
pure-env mode makes `config.json` optional — a convenience for multi-profile setups, not a requirement.
`ConfigError` names the exact env var, turning the most common first-run failure into a one-line fix.

**Why LangGraph.** The pipeline needs ordered stages, a shared typed state, and a per-run trace, all of
which LangGraph supplies without a hand-rolled orchestrator; it also makes intended extensions cheap (a
human-approval gate, a conditional retry edge after `llm_strategy`, checkpointing). No checkpointer is
used today because `AgentState` carries a live OAuth session.

## Testing

```bash
python3 -m unittest discover -s tests -t .
```

Status: **96 tests, all passing** (~4.5 s, dominated by the four subprocess CLI checks; no network, no
credentials, no on-disk fixtures).

| File | Tests | Coverage |
| --- | --- | --- |
| `tests/test_analysis.py` | 21 | Empty/malformed history, per-week averaging, makes/attempts preservation, turnover inversion, tie/no-data classification, live-comparison fallbacks, roster grouping, IL capacity arithmetic including the non-negative clamp, waiver-pool trimming and limits, context key assembly. |
| `tests/test_config_loader.py` | 12 | Pure-env mode with no file, actionable missing-credential errors, profile reads, env-beats-file precedence, unknown-profile listing, invalid `league_type`, legacy `gemini_api_key`, malformed JSON, LLM defaults. |
| `tests/test_strategy_chain.py` | 25 | `extract_json` (fenced, bare, prose-wrapped, nested braces in strings, broken JSON, invalid-then-valid, **last-fence-wins**), `TransactionPlan` validation (blank→None, unknown type rejection, extra keys, default empty), `StrategyLLM` construction and missing-key error without network, prompt rendering for both league types, unlimited adds, and the no-baseline / asymmetric-sample branches. |
| `tests/test_nodes_helpers.py` | 11 | `_fetch_roster_rules` (IL vs IL+ split, defaults on failure, missing settings block, zero add-limit preserved, capacity backfill) and `_validate_transactions` (per-entry validation, schema-echo rejection, blank player, non-dict/non-list inputs). |
| `tests/test_lineup.py` | 10 | Slot layout built from league settings (including 3-centre and no-Util leagues), bench vs active distinction, default fallback, and `optimize_roster_node` start/bench output for a non-standard shape. |
| `tests/test_entrypoint.py` | 9 | Package-mode imports for every module, graph node set, and both CLI invocation styles plus the config-error exit code and season-detection failures including a 403. |
| `tests/test_graph_integration.py` | 8 | The compiled workflow end to end with a faked Yahoo fetch and faked LLM: node ordering, state merging, prompt content, transaction extraction, token propagation, printed report, and graceful degradation on a missing key or a provider error. |

Conventions: plain `unittest`, one class per behaviour area, no network, and no mocking beyond
`mock.patch.env`-style environment patches, `tempfile` for config files, and two module-level patches in
the integration test (`fetch_data_node`, `build_llm`). `tests/__init__.py` owns the `sys.path` setup, so a
new test file only needs `import tests`. Still uncovered: `utils/yahoo_api.py` (all raw REST wrappers),
`utils/scheduler.py`, and `utils/stats_parser.py` — exercised only by live runs. `stats_parser` is the
highest-value next target: it is pure, and every downstream number depends on it.

## Known Technical Risks and Limitations

**Lineup optimisation and execution are read-only.** This is the headline limitation, not a defect.
`optimize_roster_node` computes and prints a lineup; `update_roster_positions` and
`execute_transaction` exist and were fixed to send well-formed XML with correct headers and status
checks, but the graph and CLI never call them. Every output is a recommendation for a human to apply in
the Yahoo UI. Enabling execution needs an explicit opt-in flag, a dry-run mode, and live sandbox
validation of Yahoo's roster/transaction semantics.

**Roster rules and season identifiers are partly hardcoded.** `_fetch_roster_rules` now distinguishes the
two failure modes: a *successful* read keeps the league's real values (including `max_adds: 0`, meaning
no cap), while a *failed* read returns `DEFAULT_ROSTER_RULES` instead of zeros — reporting "0 slots /
unlimited adds" during an API outage would actively mislead the prompt. `il_slots_available` still counts
`il` but not `il_plus`, understating capacity in IL+ leagues, though the prompt uses `roster_rules`
directly and is unaffected. Both `optimize_roster_node` and `analyze_injuries_node` now read
`roster_rules["standard_slots"]` rather than a literal `13`. The season key is
resolved from Yahoo on every run; if that fails there is **no silent guess** — the run aborts with an
actionable error unless `YAHOO_GAME_ID` pins the season explicitly.

**Free-agent intake is bounded and names are decorated.** `get_free_agents_by_date` fetches three
batches of 25 per sort, so at most 75 players per sort are considered before health and games-today
filtering; deep-league streaming targets can be invisible, and the prompt's "pre-filtered: healthy,
available, ranked" description is accurate but not exhaustive. Rows also carry ` (REB+)`/` (BLK+)`
markers in `Player`, so any name-based join would break (rows are keyed by `player_id`).

**Yahoo and ESPN failures are swallowed.** Every fetcher returns empty data on exception, so an auth
problem, rate limit, or upstream schema change can produce a confident-looking plan built on empty
rosters; only the warning log distinguishes that from a genuinely empty league. There is no retry,
backoff, or explicit timeout on Yahoo calls (ESPN uses a bare 10 s `requests.get`), and the LLM client
is the only component with retries.

**LLM output risks.** Two layers now guard the parse. `extract_json` tries fenced blocks **newest first**,
so if a model echoes the section-H schema example earlier in its prose the later, authoritative block
wins; and `_validate_transactions` validates **entry by entry**, so a single placeholder transaction
(e.g. a literal `"add | drop | activate | move"` type) is dropped with a warning instead of voiding a
usable plan. Residual risk remains because "no valid transactions" and "no transactions proposed" are
indistinguishable in the output — a malformed plan still renders as a clean empty plan. Injury timelines
and availability are model assertions over possibly stale data. The cost figure is approximate:
`MODEL_COST_PER_MILLION` knows three Gemini ids, defaults to `0.10`, and ignores cache/tiered pricing.

**Season boundaries are the weakest state.** At week 1 there are no completed weeks, so
`average_history` returns `{}` and `compare_history` returns `{}`; the prompt then prints
`NO SEASON BASELINE AVAILABLE` instead of a table of zeros. Earlier this was handled by
comparing against implicit zeros, which classified every category as **LOST with Diff 0.0** and
told the model it was behind everywhere before a game had been played. Two rules now hold:
a zero delta is `TIED`, not `LOST`, and a metric present on only one side is `NO_DATA`, never
compared against a phantom zero. Roster stats are also all zero until games are played, so the
greedy lineup tie-break degenerates to list order in week 1 — harmless, but it means early-season
lineup output carries little signal. The September hazard that remains is `detect_game_key_and_week`'s
`466` fallback.

**Dead declarations and no checkpointer.** `state.fa_tomorrow` and `state.today_str` are declared and
initialised but never read. Because `AgentState` holds a live `YahooFantasySportsQuery`, the graph cannot
be serialised or resumed, so a failure means a full re-run and re-fetch.

## Local Debugging

```bash
python src/main.py --profile my_account --verbose
```

`--verbose` sets `DEBUG` for application loggers while keeping urllib3/httpx/openai/httpcore at
`WARNING`. Each node logs a start line and a summary (counts, opponent id, week), and
`state["debug_logs"]` accumulates a per-stage trace that the report prints. Check configuration first —
nothing downstream works until it resolves:

```bash
python -c "from src.utils.config_loader import load_config; c=load_config(); print({k:v for k,v in c.items() if k!='llm'}); print({k:('***' if k=='api_key' and v else v) for k,v in c['llm'].items()})"
```

| Symptom | Likely cause | Check |
| --- | --- | --- |
| `ConfigError: Missing 'client_secret'` | `.env` absent or key blank | `ls .env`, re-read the dump above |
| Exit code `2` naming a profile | `--profile` matches no `profiles` key | The error lists available profiles |
| Empty roster/free agents, plan still printed | Yahoo call failed and was swallowed | `--verbose`; look for `Could not ...` / `Error getting ...` |
| `[LLM ERROR] No LLM API key` | `LLM_API_KEY`/`OPENROUTER_API_KEY` absent | The `llm` dump above |
| `[LLM ERROR] LLM request failed: ...` | Provider auth, quota, or timeout | The exception type is preserved in the message |
| `[PROPOSED TRANSACTIONS] none` unexpectedly | JSON block missing or failed validation | Grep for `no parseable JSON block` / `failed validation` |
| Wrong week or no matchup | Week resolution fell back to `1` | Look for `Could not resolve current week` |

Pure-logic probes, with no Yahoo or model calls:

```python
from src.analysis import categorize_roster, compute_capacity, compare_live
roster = [{"Player": "A", "Status": "OK", "selected_position": "PG"},
          {"Player": "B", "Status": "INJ", "selected_position": "IL"}]
print(compute_capacity(categorize_roster(roster), {"il": 1, "il_plus": 0, "standard_slots": 13}))
print(compare_live([{"team_id": "1", "PTS": 100}, {"team_id": "2", "PTS": 90}], "1"))
```

```bash
python -c "from src.prompts.strategy import SYSTEM_PROMPT; print(len(SYSTEM_PROMPT.split()), 'words')"
python -c "from src.utils.scheduler import get_active_nba_teams; print(sorted(get_active_nba_teams('2026-02-15')))"
ls -la auth/*/     # a .env file means the refreshed token wins over token.json
```

Invariants when editing: nodes stay side-effect-light (fetch, print, merge only); `analysis.py` stays
network-free; every new state key is added to `src/state.py` **and** seeded in `run_fantasy_app`'s
initial dict, because an unseeded key read directly (rather than via `state.get(...)`) fails only at
runtime.
