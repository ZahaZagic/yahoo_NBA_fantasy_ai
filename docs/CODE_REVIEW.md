# Code Review and Remediation Log

A full review of the repository, followed by the changes applied to bring it to a
state suitable for open-source release. Findings are ordered by severity. Every
item marked **Fixed** was addressed in the restructuring pass; items marked
**Open** remain as follow-up work.

---

## 1. Executive summary

The project had a genuinely interesting core: a LangGraph workflow that turns a
Yahoo Fantasy Basketball league snapshot into an LLM-reasoned weekly win plan, with
correct handling of IL/IL+ capacity, weekly add limits, injury uncertainty and
star protection. That domain logic was the strongest part of the codebase.

It was, however, not runnable and not publishable:

- **Three overlapping implementations** existed, only one of which was wired to an
  entry point.
- The runnable path **could not start** against the installed `yfpy` (17.x): the
  constructor API it used had been removed.
- Real credentials — Yahoo consumer secret, OAuth refresh tokens, and an LLM API
  key — were **committed to git**, and also hardcoded in four scratch scripts.
- There was no `.gitignore`, no root `README`, no `requirements.txt`, and no tests.

After this pass the repository has one runnable implementation, one configuration
path, validated LLM output, a documented setup flow, and a 72-test suite.

## 2. Technology stack: a correction

The working assumption was "Yahoo Fantasy API + LangChain". That was only half
right, and the distinction mattered for the review:

| Layer | Before | After |
| --- | --- | --- |
| Yahoo Fantasy Sports API | `yfpy` + hand-rolled v2 REST calls | unchanged (still `yfpy` + REST) |
| Workflow orchestration | LangGraph | unchanged (LangGraph) |
| LLM access | **raw `requests.post` to OpenRouter** | **LangChain `ChatOpenAI`** |
| Output validation | `str.split("```json")` | Pydantic schema |

So the project was *LangGraph*-based, not *LangChain*-based. LangChain appeared
only in the stale `fantasy_bot/` copy, which used `langchain_google_genai` against a
deprecated `gemini-1.5-flash` model. The LLM key in `config.json` was labelled
`gemini_api_key` but was actually an OpenRouter key (`sk-or-v1-...`).

The LLM layer has now been migrated to LangChain, which is what makes retries,
timeouts and unified token accounting available rather than hand-rolled.

## 3. Critical findings

### C1. Credentials committed to version control — *Fixed*

Real secrets were tracked in git:

| File | Exposed |
| --- | --- |
| `config.json`, `src/config.json` | Yahoo `consumer_key`, `consumer_secret`, OpenRouter API key |
| `auth/token.json`, `src/auth/token.json`, `src/auth/my_account/token.json` | OAuth `access_token`, `refresh_token` |
| `auth/private.json` | consumer key + secret |
| `fantasy_bot/src/config.py` | consumer key + secret **hardcoded** |
| `debug_player_full.py`, `fetch_current_state.py`, `fetch_settings.py` | consumer key + secret **hardcoded** |

`fetch_settings.py` even carried a different, typo'd consumer key, which is a good
illustration of why copying credentials into scripts does not scale.

**Remediation applied:** untracked all secret-bearing files, added a `.gitignore`
covering `.env`, `config.json`, `auth/*`, token files and `*_token.json`, deleted
the four scripts with hardcoded credentials, and added `.env.example` /
`config.example.json` templates containing no real values. Configuration is now
env-first, so a fresh clone needs only a local `.env`.

**Still open:** the values remain in git *history*. Anyone with access to the
history — or to any fork or clone — still has them. Two options:

1. **Rotate.** Delete and recreate the Yahoo app; revoke and regenerate the LLM
   key. This is sufficient and strongly recommended.
2. **Also purge history** with `git filter-repo` (or BFG) and force-push. Only
   worth it if the repository was ever public or widely cloned.

Rotation is required regardless; purging history is defence in depth.

### C2. The application could not start against installed dependencies — *Fixed*

`src/main.py` constructed the Yahoo client with:

```python
YahooFantasySportsQuery(auth_dir=..., consumer_key=..., consumer_secret=...)
```

In `yfpy` 17 these parameters no longer exist:

```
TypeError: YahooFantasySportsQuery.__init__() got an unexpected keyword argument 'auth_dir'
```

The installed `yfpy` is 17.0.0, whose signature is
`(league_id, game_code, game_id, yahoo_consumer_key, yahoo_consumer_secret,
yahoo_access_token_json, env_var_fallback, env_file_location,
save_token_data_to_env_file, ...)`. There was also no `requirements.txt` pinning a
compatible version, so the failure was silent until runtime.

**Remediation applied:** `src/main.py` rewritten for the yfpy 17 API. Token
handling now:

- resolves a per-profile directory `auth/<profile>/`;
- migrates a legacy `auth/token.json` on first run;
- passes the cached token as `yahoo_access_token_json`, preferring a refreshed
  `.env` written by yfpy when one exists;
- persists refreshed tokens via `save_token_data_to_env_file`.

`requirements.txt` now requires `yfpy>=17.0.0`.

### C3. Wrong arguments passed to the roster-update call — *Fixed*

`execute_transaction` called:

```python
update_roster_positions(query, my_team_id, {pid: "BN", did: "IL"})
```

but the signature is `update_roster_positions(query, league_id, team_id,
position_map)`. The team ID was being passed as the league ID, the position map as
the team ID, and `position_map` was missing entirely — so internal IL "move"
transactions would have failed at runtime. The same mistake appeared in the
`activate` path.

**Remediation applied:** both call sites corrected. (Execution remains disabled by
design; see M8.)

## 4. High-severity findings

### H1. Three overlapping implementations — *Fixed*

| Track | State |
| --- | --- |
| `src/main.py` → `src/nodes.py` → `src/utils/` | the real, most-evolved workflow |
| `fantasy_bot/` | older LangChain prototype, less capable, hardcoded league |
| `src/core`, `src/agents`, `src/tools` | class-based scaffold, entirely placeholder, never imported by an entry point |

Keeping all three made the repository hard to reason about and impossible to
document accurately. The scaffold's `agent_analyze.py` was also actively
misleading: it computed team stats by multiplying per-game averages by a hardcoded
`4` games per week and averaged percentages naively.

**Remediation applied:** removed `fantasy_bot/`, `src/core/`, `src/agents/`,
`src/tools/`, plus committed `*.pyc`, `.idea/`, and 12 generated
`output*/verification*/final_*` text artifacts. All recoverable from git history.
What survives is the single runnable path, now refactored.

### H2. LLM call had no resilience and a dead error branch — *Fixed*

```python
resp = requests.post(..., timeout=50)
if resp.status_code == 200: ...
else: analysis = f"LLM API Error: ..."
except Exception as e: analysis = f"LLM Error: {e}"
except Exception as e: analysis = f"LLM Error: {e}"   # unreachable duplicate
```

No retries and no backoff, so a single 429 lost the whole analysis. A 50-second
timeout is aggressive for a prompt of that size. The duplicated `except` block is
legal Python but dead code.

**Remediation applied:** replaced with LangChain `ChatOpenAI` configured with
`max_retries` (exponential backoff), an explicit `timeout`, and a single
`LLMError` surfaced through the node. Failures are recorded on the state as
`llm_error` and the pipeline continues instead of printing a stack trace.

### H3. Fragile JSON extraction from the model response — *Fixed*

```python
json_str = full_text.split("```json")[1].split("```")[0].strip()
transactions = json.loads(json_str).get("transactions", [])
except: pass
```

This silently yielded an empty transaction list on any deviation — no fence, a
fence labelled `JSON`, prose before the block, or trailing commas. A silent empty
list is indistinguishable from "no move is needed", which is the worst possible
failure mode for a decision tool.

**Remediation applied:** `utils.llm.extract_json` scans for a *balanced* JSON object
with a string-aware brace matcher. Fenced blocks are tried **newest first**, because
the output contract places the real answer in the final block — so a model that echoes
the schema example mid-prose cannot shadow its own answer. Per-transaction
normalisation and validation is handled by `schemas.Transaction` / `TransactionPlan`,
which coerce `"null"`/`"N/A"`/`"-"` to `None` and reject unknown transaction types.
`_validate_transactions` validates **entry by entry**, so one malformed entry is
dropped with a warning rather than voiding an otherwise usable plan. Parse failures
are logged and recorded, not swallowed.

Residual risk: "no valid transactions" and "no transactions proposed" still render
identically in the output, so a silently malformed plan looks like a clean empty one.
The fix is to surface `llm_error`/a parse warning in the report body, not just in the
logs.

### H4. Prompt design was not production-shaped — *Fixed*

The prompt was a single ~600-line f-string merging role, rules and data. Problems:

- **Rule/data interleaving.** Hard constraints sat between large JSON blobs, which
  measurably degrades instruction adherence.
- **Duplicated context.** Each free agent appeared once as explicit fields and again
  as a nested `stats` dump; roster entries carried an `raw_stats` string that
  repeated fields already present.
- **Contradictory or unranked rules.** Activation, drop and GTD guidance were spread
  across three separate blocks with no stated precedence, so the model had to
  resolve conflicts itself.
- **No output contract.** Section names were listed but nothing declared the JSON
  schema, forbidden keys, or what to do when data was insufficient.
- **No anti-hallucination guard.** Nothing told the model to only use supplied
  `player_id` values — which is exactly how a model invents a player key.

See section 6 for what replaced it.

### H5. No repository hygiene for a public project — *Fixed*

Missing: `.gitignore`, root `README.md`, `requirements.txt`, example
configuration, and any notion of which files are generated. `requirements.txt`
existed only inside the deleted `fantasy_bot/`.

**Remediation applied:** all added. See `README.md` for the setup guide and
`docs/OPERATIONS_TESTING_DEPLOYMENT.md` for the configuration reference.

## 5. Medium-severity findings

| # | Finding | Status |
| --- | --- | --- |
| M1 | Bare `except:` / `except Exception` swallowing all errors across `nodes.py`, `yahoo_api.py`, `stats_parser.py` | **Fixed** — every bare `except:` removed; broad handlers now log context instead of passing silently. `yahoo_api.py` still degrades to `[]` on failure (see risk 2) |
| M2 | `from state import AgentState` relied on `src/` being `sys.path[0]`, so `python -m src.main` failed | **Fixed** — every module now has a package-relative import with a script-mode fallback |
| M3 | Free-agent pool fetched twice (6 listing + 6 batch requests) per run; no caching, ~20–30s and dozens of API calls | **Open** — documented; a response cache is the top performance win |
| M4 | Free agents deduplicated by decorated display name (`"Name (REB+)"`) rather than `player_id` | **Fixed** — dedupe on `player_id` with a name fallback |
| M5 | LLM model, base URL, timeout and retry count hardcoded in the node | **Fixed** — resolved from `config["llm"]` with env overrides |
| M6 | Cost estimate hardcoded to `$0.10/1M` tokens regardless of model | **Fixed** — per-model price map with a documented default |
| M7 | `debug_player_full.py` imported `LEAGUE_ID` from `utils.yahoo_api`, which no longer defines it — the script raised `ImportError` | **Fixed by deletion** — superseded by the main pipeline |
| M8 | Roster mutation was unreachable dead code (`update_roster_positions` call commented out) but still shipped, with a bug (C3) | **Fixed** — kept, corrected, and explicitly documented as not wired in. The tool is read-only by design |
| M9 | Unused imports (`pandas`, `tabulate` in `nodes.py`); inconsistent module conventions | **Fixed** |
| M10 | No tests | **Fixed** — 72 unit tests, no network required |
| M11 | No `LICENSE` | **Fixed** — GPL-3.0, chosen to match the GPL-3.0 `yfpy` dependency rather than the MIT/Apache default |

## 6. What the strategy prompt looks like now

The single blob became two messages with different jobs.

**System message** (stable across runs, ~4.3 KB) carries only rules:

- **Role** — advisor to one manager for one head-to-head matchup.
- **Seven hard constraints**, numbered, including: reason only from supplied data;
  never drop a Core Anchor; never move a player already in IL; respect the reported
  open spots and remaining adds exactly; preserve units; emit exactly one JSON
  block.
- **Decision priority**, stated as an ordered list so conflicts resolve
  deterministically — free capacity first, internal IL fix before external add, drop
  only when forced, stream last.
- **Classification rubric** — role bands by season rank, and the
  Safe/Key/Swing/Lost category labels with their meanings.
- **Style** — terse, no restating input, one sentence per ranking entry.
- **Output contract** — sections A–H, the exact JSON shape, and an explicit
  statement that an empty transaction list is valid and often correct.

**Human message** (per run, ~5 KB) carries only data: date, league format and
objective, capacity arithmetic, live comparison, season comparison with sample
sizes, roster grouped by health × slot, trimmed free agents, then six numbered
reasoning steps.

Net effects:

- Prompt payload roughly halved, because duplicated fields were removed and free
  agents are trimmed to the fields actually reasoned about.
- Rules no longer compete with JSON for attention.
- The model is told what "good" looks like at each step, and what to emit when the
  data is insufficient.
- Output is machine-validated, so a malformed plan cannot silently become an empty
  plan.

Both league formats get format-specific objectives: 9-cat is told a 5-4 win counts
the same as 9-0 and to prioritize flippable categories; points leagues are told
category balance is irrelevant and total volume is everything.

## 7. Remaining technical risks

1. **Secrets in git history** (C1) — rotate now; purge only if it was ever public.
2. **`utils/yahoo_api.py` still degrades silently.** Most functions catch broad
   exceptions and return `[]`, so a Yahoo outage looks like an empty roster. The
   pipeline continues, but the report may be confidently wrong. Returning a
   structured error or raising a typed exception would be better.
3. **No request cache.** Every run is a full refetch; `start=0/25/50` pagination is
   repeated even when results overlap.
4. **Greedy lineup optimiser.** Assignment is by points per game in three passes; it
   is not optimal and ignores matchups, projected minutes and back-to-backs.
5. **No projected-stat source.** The model reasons from season averages and the
   live box score, so it cannot account for rest days or role changes.
6. **`fa_tomorrow` is declared in state but never populated.** Dead field.
7. **Third-party dependency on the ESPN scoreboard API** for "who plays today".
   Undocumented endpoint with no SLA; a failure silently falls back to Yahoo's own
   flag.
8. **No CI workflow** running the test suite on push/PR.

## 8. Change summary

Removed: `fantasy_bot/`, `src/core/`, `src/agents/`, `src/tools/`, 3 root debug
scripts, 12 generated text artifacts, committed `__pycache__`/`.pyc`, `.idea/`,
and 6 secret-bearing JSON files from tracking.

Added: `README.md`, `LICENSE` (GPL-3.0), `requirements.txt`, `.gitignore`,
`.env.example`, `config.example.json`, `auth/.gitkeep`, `src/analysis.py`, `src/schemas.py`,
`src/prompts/`, `src/utils/llm.py`, `tests/` (72 tests), `docs/CODE_REVIEW.md`.

Rewritten: `src/main.py`, `src/nodes.py`, `src/state.py`,
`src/utils/config_loader.py`, `src/prompts/`, `src/utils/llm.py`, and all five
documents (the business-logic, technical, operations, resume, and index docs were
brought back in line with the actual tree).

Bug fixes: yfpy 17 compatibility (C2), `update_roster_positions` argument order
(C3), duplicate `except` (H2), fragile JSON parsing (H3), free-agent dedupe key
(M4), package-safe imports (M2), and four robustness gaps found while writing the
docs — two hardcoded roster-size assumptions in `analyze_injuries_node` /
`optimize_roster_node` that ignored `roster_rules["standard_slots"]`;
`_fetch_roster_rules` reporting `0` slots during an API outage instead of
defaulting; `extract_json` preferring the *first* fenced block and therefore
losing to an echoed schema example; and all-or-nothing transaction validation that
let one malformed entry void a whole plan.
