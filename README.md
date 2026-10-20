# Fantasy NBA Agent

An LLM-powered decision agent for Yahoo Fantasy Basketball. It pulls your roster,
live matchup, season history and the free-agent pool through the Yahoo Fantasy
Sports API, then uses a LangGraph workflow plus an LLM strategy chain to produce a
weekly win plan: who to start, who to sit, who to drop, and who to stream.

The agent is **read-only by design**. It recommends; you decide. No roster moves
are executed automatically.

---

## Table of Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Features](#features)
- [Requirements](#requirements)
- [Quickstart](#quickstart)
- [Getting your Yahoo Fantasy API credentials](#getting-your-yahoo-fantasy-api-credentials)
- [Finding your league ID and team ID](#finding-your-league-id-and-team-id)
- [Getting an LLM API key](#getting-an-llm-api-key)
- [Configuration reference](#configuration-reference)
- [Usage](#usage)
- [Understanding the output](#understanding-the-output)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [Limitations and roadmap](#limitations-and-roadmap)

---

## What it does

Given a league and team, the agent answers one question each week:

> "What is the highest-probability set of roster moves to win *this* matchup?"

To do that it:

1. Fetches your roster with season averages, injury status and current slots.
2. Fetches the live head-to-head matchup and your opponent's roster.
3. Rebuilds your season-long weekly averages and compares them to the opponent's.
4. Detects injured players parked in active slots and healthy players stuck in IL.
5. Filters the free-agent pool to players who are actually available and playing today.
6. Computes your real roster capacity (open standard slots, empty IL/IL+ slots,
   remaining weekly adds).
7. Sends that snapshot to an LLM with a strict decision rubric and gets back a
   ranked, justified plan plus a machine-readable transaction list.
8. Validates the transaction list against a schema, then prints the report and the
   token cost.

It supports both scoring formats:

| League type | Objective |
| --- | --- |
| `9cat` | Win the head-to-head category count (FG%, FT%, 3PTM, PTS, REB, AST, STL, BLK, TO) |
| `points` | Maximize total fantasy points scored during the week |

## How it works

```
                    ┌──────────────────────────────┐
                    │  Yahoo Fantasy Sports API    │
                    │  (yfpy + raw v2 endpoints)   │
                    └──────────────┬───────────────┘
                                   │
             ┌─────────────────────┼─────────────────────┐
             │                     │                     │
        roster + stats        matchup + history     free agents
             │                     │                     │
             └─────────────────────┼─────────────────────┘
                                   ▼
   ┌─────────────────────── LangGraph workflow ────────────────────────┐
   │                                                                  │
   │  1. fetch            collect roster, matchup, history, FAs,      │
   │                      league rules, weekly adds used              │
   │            │                                                     │
   │  2. optimize_roster  greedy lineup assignment (read-only)         │
   │            │                                                     │
   │  3. analyze_injuries IL / IL+ capacity and injury conflicts       │
   │            │                                                     │
   │  4. llm_strategy     analysis.py -> prompts/strategy.py ->        │
   │                      LangChain ChatOpenAI -> JSON -> Pydantic     │
   │            │                                                     │
   │  5. format_output    render report + token cost                   │
   │                                                                  │
   └──────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
                       Console report + transaction plan
```

A few design notes:

- **`src/analysis.py` is pure.** All scoring and comparison logic is a
  side-effect-free transformation of a state snapshot, so it is unit-testable
  without Yahoo or an LLM.
- **The prompt is split into system + user turns.** Hard constraints live in the
  system message; the week's data is in the user message. Rules no longer compete
  with the JSON blob for the model's attention.
- **LLM output is untrusted.** The JSON block is parsed with a string-aware brace
  matcher and validated against a Pydantic schema before it can influence any
  recommendation.
- **LLM failure is non-fatal.** A missing key or a provider error is recorded on
  the state; the rest of the pipeline still produces a report.

## Features

- 9-cat and points league support with format-specific reasoning objectives.
- Correct IL/IL+ capacity accounting, including IL+ slots.
- GTD / INJ / Out handling with an explicit "uncertain" policy.
- Weekly add-limit awareness to prevent churn.
- Star protection: players below a season-rank threshold are marked untouchable.
- Free-agent pool filtered to healthy, available players who play today, merged
  from an ownership-sorted and a rank-sorted Yahoo query.
- ESPN scoreboard integration to determine who actually plays today.
- Structured transaction output validated by Pydantic.
- Token usage and estimated per-run cost.
- Per-profile, per-league configuration.
- 72 unit tests, no network required.

## Requirements

- Python 3.10+ (required by `yfpy` 17; developed and tested on 3.10)
- A Yahoo account that manages a fantasy basketball team
- A Yahoo Developer app (free) for API credentials
- An LLM API key (OpenRouter by default; Gemini models work out of the box)

## Quickstart

```bash
# 1. Clone and enter the project
git clone https://github.com/ZahaZagic/yahoo_NBA_fantasy_ai.git
cd fantasy

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your local secrets file
cp .env.example .env
$EDITOR .env                     # fill in the values

# 5. Run it
python -m src.main
```

On the very first run there is no cached OAuth token, so yfpy opens a browser
window asking you to allow the app access to your Yahoo fantasy data. Click
**Allow**, copy the verification code Yahoo shows you, and paste it into the
terminal prompt. The resulting token is written to `auth/<profile>/.env` and
reused (and refreshed) automatically on later runs.

> `.env` and `config.json` are git-ignored. Never commit them.

## Getting your Yahoo Fantasy API credentials

You need a **Consumer Key** and **Consumer Secret**. Both come from a free Yahoo
Developer app.

1. Sign in at <https://developer.yahoo.com/apps/create/> with **the same Yahoo
   account that owns your fantasy team**. A different account will not be able to
   read your league.
2. Fill in the form:

   | Field | Value |
   | --- | --- |
   | Application Name | `fantasy-nba-agent` (any name works) |
   | Application Type | **Installed Application** |
   | Description | optional |
   | Home Page URL | optional, can be left blank |
   | Redirect URI(s) | `https://localhost:8080` |

   > **Redirect URI matters.** Yahoo requires this exact value for the
   > out-of-band verification flow yfpy uses. If it is missing or malformed, the
   > authorization page will refuse the request.

3. Under **API Permissions**, check **Fantasy Sports** and select **Read**.
   Read-only is enough for everything this project does. Choose **Read/Write**
   only if you plan to extend the project into executing transactions, and be
   aware that write access requires re-authorization.
4. Accept the terms and click **Create App**.
5. Copy the generated **Client ID (Consumer Key)** and **Client Secret (Consumer
   Secret)** into your `.env`:

   ```bash
   YAHOO_CONSUMER_KEY=your_client_id_here
   YAHOO_CONSUMER_SECRET=your_client_secret_here
   ```

> Treat the consumer secret like a password. It is a long-lived credential. If it
> is ever exposed, delete the app in the Yahoo Developer dashboard and create a
> new one.

## Finding your league ID and team ID

**League ID** — open your league in a browser. The URL looks like:

```
https://basketball.fantasysports.yahoo.com/nba/123456
                                               ^^^^^^ league ID
```

**Team ID** — open your own team page. The URL looks like:

```
https://basketball.fantasysports.yahoo.com/nba/123456/1
                                                      ^^ team ID
```

Put both in `.env`:

```bash
YAHOO_LEAGUE_ID=123456
YAHOO_TEAM_ID=1
```

## Getting an LLM API key

### Option A — OpenRouter (default)

OpenRouter proxies Gemini and many other models behind one OpenAI-compatible API.

1. Sign in at <https://openrouter.ai/>.
2. Create a key at <https://openrouter.ai/keys> — it starts with `sk-or-v1-`.
3. Add credit if required by your chosen model.
4. Set it in `.env`:

   ```bash
   LLM_API_KEY=sk-or-v1-your_key_here
   LLM_BASE_URL=https://openrouter.ai/api/v1
   LLM_MODEL=google/gemini-2.0-flash-001
   ```

The default model is deliberately cheap: a typical run is a few thousand tokens,
well under one cent.

### Option B — Google AI Studio (direct Gemini)

Google exposes an OpenAI-compatible endpoint, so no code change is needed:

```bash
LLM_API_KEY=your_google_ai_studio_key
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemini-2.0-flash
```

Get a key from <https://aistudio.google.com/app/apikey>.

## Configuration reference

Resolution order is **environment variables → `config.json` → built-in defaults**.
You can therefore run with only a `.env` file and no `config.json` at all.

### Environment variables

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `YAHOO_CONSUMER_KEY` | yes | – | Yahoo app Client ID |
| `YAHOO_CONSUMER_SECRET` | yes | – | Yahoo app Client Secret |
| `YAHOO_LEAGUE_ID` | yes | – | Numeric league ID |
| `YAHOO_TEAM_ID` | yes | – | Numeric team ID |
| `YAHOO_GAME_CODE` | no | `nba` | Yahoo sport code |
| `LEAGUE_TYPE` | no | `9cat` | `9cat` or `points` |
| `LLM_API_KEY` | recommended | – | Provider key; also accepts `OPENROUTER_API_KEY` |
| `LLM_BASE_URL` | no | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint |
| `LLM_MODEL` | no | `google/gemini-2.0-flash-001` | Model identifier |

### `config.json` (optional, multi-profile)

Copy `config.example.json` to `config.json` when you want to track several leagues
or teams:

```json
{
  "default_profile": "my_account",
  "llm": {
    "base_url": "https://openrouter.ai/api/v1",
    "model": "google/gemini-2.0-flash-001",
    "temperature": 0.2,
    "max_tokens": 4000,
    "timeout_seconds": 90,
    "max_retries": 3
  },
  "profiles": {
    "my_account": {
      "league_id": "123456",
      "team_id": "1",
      "game_code": "nba",
      "league_type": "9cat"
    }
  }
}
```

Credentials are still read from the environment; keeping them out of `config.json`
means the file is safe to share or keep in a dotfiles repo.

## Usage

```bash
# Default profile
python -m src.main

# Named profile from config.json
python -m src.main --profile my_account

# Verbose (debug-level logging)
python -m src.main --verbose
```

`python src/main.py` also works. Running as a module (`-m src.main`) is preferred
because it keeps package imports consistent.

## Understanding the output

The run prints, in order:

1. **Proposed lineup** — the greedy assignment of eligible players to
   PG/SG/G/SF/PF/F/C/Util/BN. Read-only; nothing is applied to Yahoo.
2. **System decisions** — mechanical IL/IR conflicts (e.g. "injured player on an
   active slot", "healthy player stuck in IL").
3. **Proposed transactions** — the validated transaction list. Each entry has a
   type (`add`, `drop`, `activate`, `move`), the player, an optional drop/move
   counterpart, and a reason.
4. **AI analysis** — the narrative report, sections A–H:
   - A) Matchup Win Plan
   - B) Injury Status & Return Timelines
   - C) Category / Points Risk Analysis
   - D) Player Value Ranking
   - E) Drop Priority
   - F) Streaming Rule
   - G) Add Priority
   - H) Automation JSON (schema-validated)
5. **Token usage and estimated cost.**

An empty transaction list is a valid and common result — it means no move was
justified.

## Project structure

```
fantasy/
├── README.md                     # this file
├── requirements.txt
├── config.example.json           # API-key-free configuration template
├── .env.example                  # secrets template
├── auth/                         # OAuth tokens per profile (git-ignored)
│   └── .gitkeep
├── src/
│   ├── main.py                   # entry point: config, auth, graph wiring
│   ├── nodes.py                  # the five LangGraph nodes
│   ├── state.py                  # AgentState TypedDict
│   ├── analysis.py               # pure scoring/comparison helpers
│   ├── schemas.py                # Pydantic validation of LLM output
│   ├── prompts/
│   │   └── strategy.py           # system prompt + human prompt builder
│   └── utils/
│       ├── config_loader.py      # env-first configuration resolution
│       ├── llm.py                # LangChain LLM client + JSON extraction
│       ├── yahoo_api.py          # Yahoo Fantasy v2 REST wrappers
│       ├── stats_parser.py       # Yahoo stat flattening and mapping
│       └── scheduler.py          # ESPN "who plays today" lookup
├── tests/                        # unittest suite, no network needed
└── docs/                         # architecture, operations, review notes
```

## Testing

The suite needs no credentials or network access:

```bash
python3 -m unittest discover -s tests -t .
```

| Module | Covers |
| --- | --- |
| `tests/test_analysis.py` | history averaging, live/history comparison, roster grouping, capacity maths, waiver trimming |
| `tests/test_config_loader.py` | env-only mode, config-file mode, precedence, validation errors, legacy key names |
| `tests/test_strategy_chain.py` | JSON extraction edge cases, Pydantic validation, prompt rendering for both league types |
| `tests/test_nodes_helpers.py` | league-settings parsing and its failure fallback, per-entry transaction validation |
| `tests/test_entrypoint.py` | both invocation modes, graph node wiring, config-error exit code |
| `tests/test_graph_integration.py` | full compiled workflow with a faked Yahoo fetch and faked LLM, plus graceful degradation when the LLM is unavailable |

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `ConfigError: Missing 'client_secret'` | `.env` not created or not filled in. Copy `.env.example` to `.env`. |
| `redirect_uri_mismatch` during first run | The Yahoo app's **Redirect URI(s)** field is not `https://localhost:8080`. Fix it in the Yahoo dashboard and retry. |
| Authorization succeeds but the prompt waits forever | The verification code from the browser was not pasted into the terminal. Re-run and paste it at the `Enter verifier` prompt. |
| `401` / `invalid_token` from Yahoo | Cached token is stale. Delete `auth/<profile>/` and re-authorize. |
| `LLM request failed: ... 401` | Wrong `LLM_API_KEY`, or a Gemini key sent to the OpenRouter URL (or vice versa). Check `LLM_BASE_URL` matches the key type. |
| `LLM request failed: ... 429` | Rate limited. Retries are automatic; if it persists, lower run frequency or raise your tier. |
| `Proposed transactions: none` | Working as intended — no move was justified, or the free-agent pool was empty. |
| Empty free-agent tables | Everyone is rostered, or the league is in the offseason / no games today. |
| `TypeError: unexpected keyword argument 'auth_dir'` | An old yfpy is installed. Upgrade: `pip install -U 'yfpy>=17'`. |

## Security

This project reads private league data and requires two long-lived credentials.

- **Never commit `.env`, `config.json`, or `auth/`.** They are already in
  `.gitignore`; keep it that way.
- The Yahoo consumer secret and the LLM API key are long-lived. If either was ever
  committed or shared, **rotate it**: delete and recreate the Yahoo app, and
  revoke/regenerate the LLM key.
- Request only **Read** permission on the Yahoo app unless you intend to extend the
  project to execute transactions.
- Tokens under `auth/` grant league access. Restrict permissions
  (`chmod 700 auth`) if the machine is shared.

See [docs/OPERATIONS_TESTING_DEPLOYMENT.md](docs/OPERATIONS_TESTING_DEPLOYMENT.md)
for the full security review.

## Limitations and roadmap

Current limitations, all deliberate:

- **Read-only.** Lineup changes and transactions are printed, never executed.
  (`utils/yahoo_api.update_roster_positions` and `execute_transaction` exist and
  are argument-correct, but are not wired into the graph.)
- Lineup optimisation uses a greedy heuristic over points per game, not a true
  optimiser over projected minutes and schedule.
- No projected-stat source; the model reasons from season averages and the live
  matchup.
- No caching layer — every run refetches everything, which costs roughly 20–30
  seconds and a few dozen API calls.
- No web UI or scheduler; runs are manual or driven by an external cron.

Natural next steps: a projected-stats source, an ILP-based lineup optimiser, a
response cache keyed by league/day, a `--json` output mode for automation, and an
explicit opt-in execution path behind a confirmation gate.

## License

[GNU General Public License v3.0](LICENSE) (GPL-3.0).

This project depends on [`yfpy`](https://github.com/uberfastman/yfpy), which is
itself GPL-3.0. Because the code imports and distributes against it, GPL-3.0 is
the license under which this work can be shared. In practice that means:

- You may use, modify, and redistribute this project, including commercially.
- If you distribute a modified version, you must also make its source available
  under GPL-3.0.
- Running it privately for your own leagues imposes no obligations.

If you need a permissive license, you would have to replace the `yfpy` dependency
with your own Yahoo API client first.
