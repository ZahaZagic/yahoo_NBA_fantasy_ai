# Operations, Testing, and Deployment

Operational reference for installing, configuring, testing, scheduling, and
securing the Fantasy NBA agent. Setup narrative and credential walkthroughs live
in the root [README](../README.md); this document covers the details an operator
needs after the repository is cloned.

> **Read-only by construction.** The LangGraph workflow fetches data, computes a
> plan, and prints it. `update_roster_positions` and `execute_transaction` exist in
> `src/utils/yahoo_api.py` but are not imported by `src/nodes.py` and are never
> invoked by the graph. No roster, lineup, or transaction write is performed.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.10+ | Project docs say 3.9+ and development used 3.10, but yfpy 17 declares `Requires-Python >=3.10`; on 3.9 pip resolves an older yfpy the code does not support. Use 3.10 or newer. |
| Yahoo account with a fantasy basketball team | Must be the account that owns the team. |
| Yahoo Developer app | Consumer key and secret; permission **Read** is sufficient. |
| LLM API key | Optional. Without it the strategy step is skipped and `llm_error` is set; data tables and the deterministic lineup plan still print. |
| Network access | Yahoo Fantasy Sports API, ESPN scoreboard API, and the configured LLM endpoint. |
| No database, broker, or web server | State is in-process; auth is on disk. |

The code targets the **yfpy 17** API. `requirements.txt` declares
`yfpy>=15.0.0`, so an already-installed 15.x/16.x is not upgraded automatically.
After installing, confirm:

```bash
python -m pip show yfpy
```

Upgrade if the reported version is below 17; older constructors do not accept
`env_file_location` or `save_token_data_to_env_file`.

## Local Setup

From the repository root:

```bash
git clone https://github.com/ZahaZagic/yahoo_NBA_fantasy_ai.git
cd fantasy

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cp .env.example .env
$EDITOR .env

# Optional: only needed for multiple profiles/leagues
cp config.example.json config.json

python src/main.py --help
python3 -m unittest discover -s tests -t .
```

`.venv/`, `.env`, `config.json`, and everything under `auth/` except `.gitkeep`
are git-ignored. No virtualenv or `pyproject.toml` is committed.

`.env` is loaded from the project root by `src/utils/config_loader.py`, so it is
found regardless of the current working directory. Values already present in the
process environment take precedence over `.env`.

## Obtaining Credentials

Short version only; the root README has the full walkthroughs.

1. Create a Yahoo app at <https://developer.yahoo.com/apps/create/> with:
   - Application Type: **Installed Application**
   - Redirect URI(s): `https://localhost:8080`
   - API Permissions: **Fantasy Sports -> Read**
2. Put the Client ID and Client Secret in `.env` as `YAHOO_CONSUMER_KEY` and
   `YAHOO_CONSUMER_SECRET`.
3. Put the numeric league and team IDs from the Yahoo URL into
   `YAHOO_LEAGUE_ID` and `YAHOO_TEAM_ID`.
4. Create an OpenRouter key at <https://openrouter.ai/keys> and set
   `LLM_API_KEY`. Any OpenAI-compatible endpoint works via `LLM_BASE_URL` and
   `LLM_MODEL`.

Do not reuse a consumer secret or LLM key that has ever been committed, pasted
into an issue, or shared in a screenshot. See [Security Review](#security-review).

## First Run and What to Expect

```bash
python src/main.py
# or
python src/main.py --profile my_account
python src/main.py --profile my_account --verbose
```

Sequence of events:

1. **Configuration resolution.** `.env` is loaded, then `config.json`, then
   built-in defaults. Missing credentials or an invalid `league_type` raise
   `ConfigError` and exit with status **2** before any network call.
2. **Auth directory.** `auth/<profile>/` is created. A legacy `auth/token.json`
   is copied to `auth/<profile>/token.json` on first run only.
3. **OAuth.** With no cached token, yfpy opens a browser window against Yahoo's
   consent screen and listens for the local callback. The token is written to
   `auth/<profile>/` (yfpy 17 stores it in `auth/<profile>/.env`). Subsequent
   runs reuse and refresh it without a browser.
4. **Season and week detection.** The current game key and league week are read
   from Yahoo. If either call fails, the run logs a warning and falls back to
   game key `466` (2025-26 NBA) and week `1` rather than aborting.
5. **Data collection.** Roster, live matchup, weekly history for both teams,
   league roster rules, weekly adds used, and up to 75 free agents are fetched.
   Tables for roster, matchup, and free agents print to stdout.
6. **Local analysis.** A greedy points-per-game lineup assignment is printed
   under `--- PROPOSED LINEUP (read-only) ---`, followed by IL/injury decisions.
7. **LLM strategy.** One completion is requested. On any provider or transport
   error the node records `llm_error`, prints `[LLM ERROR]`, and the run
   continues.
8. **Report.** `FANTASY NBA MATCHUP PLAN` prints proposed transactions (or
   "none"), the AI analysis, and token usage with an estimated cost.

Expected runtime is tens of seconds and a few dozen Yahoo requests on a normal
league; the first run additionally waits on interactive OAuth. Every run refetches
everything — there is no cache.

Exit codes from `src/main.py`:

| Code | Meaning |
| --- | --- |
| 0 | Run completed (including runs where the LLM failed). |
| 1 | Unhandled runtime failure; traceback is logged. |
| 2 | `ConfigError` — missing/invalid configuration. |
| 130 | Interrupted with Ctrl-C. |

## Configuration Reference

Resolution order, highest priority first:

1. Process environment variables.
2. `config.json` at the project root (optional).
3. Built-in defaults in `src/utils/config_loader.py`.

Credentials are required from either the environment or the selected profile.
Environment-variable mode works with no `config.json` at all.

### Environment variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `YAHOO_CONSUMER_KEY` | Yes | — | Yahoo app Client ID. Alias: `YAHOO_CLIENT_ID`. |
| `YAHOO_CONSUMER_SECRET` | Yes | — | Yahoo app Client Secret. Alias: `YAHOO_CLIENT_SECRET`. |
| `YAHOO_LEAGUE_ID` | Yes | — | Numeric league ID from the Yahoo URL. |
| `YAHOO_TEAM_ID` | Yes | — | Numeric team ID from the Yahoo URL. |
| `YAHOO_GAME_CODE` | No | `nba` | Yahoo sport code passed to yfpy. |
| `LEAGUE_TYPE` | No | `9cat` | Scoring model: `9cat` or `points`. |
| `LLM_API_KEY` | No | — | Provider key. Alias: `OPENROUTER_API_KEY`. Without it the strategy step is skipped. |
| `LLM_BASE_URL` | No | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint. |
| `LLM_MODEL` | No | `google/gemini-2.0-flash-001` | Model identifier sent to the endpoint. |

"Required" means required overall: a value supplied by the selected profile in
`config.json` satisfies it.

### `config.json` fields

`config.json` is optional and git-ignored. Copy `config.example.json` to create
it. All fields except `profiles` are optional.

| Field | Default | Purpose |
| --- | --- | --- |
| `default_profile` | `default` | Profile used when `--profile` is omitted. |
| `llm.base_url` | `https://openrouter.ai/api/v1` | Overridden by `LLM_BASE_URL`. |
| `llm.model` | `google/gemini-2.0-flash-001` | Overridden by `LLM_MODEL`. |
| `llm.temperature` | `0.2` | Sampling temperature. Config file only; no env var. |
| `llm.max_tokens` | `4000` | Completion token cap. |
| `llm.timeout_seconds` | `90` | Per-request HTTP timeout. |
| `llm.max_retries` | `3` | Retries with backoff on 429/5xx. |
| `profiles.<name>.league_id` | — | League ID for this profile. |
| `profiles.<name>.team_id` | — | Team ID for this profile. |
| `profiles.<name>.game_code` | `nba` | Sport code for this profile. |
| `profiles.<name>.league_type` | `9cat` | `9cat` or `points`. |
| `profiles.<name>.client_id` | — | Optional Yahoo key override; avoid storing it here. |
| `profiles.<name>.client_secret` | — | Optional Yahoo secret override; avoid storing it here. |

The `llm` block is global, not per profile. Legacy top-level `llm_api_key` and
`gemini_api_key` keys are still read as a fallback for the API key, but they
duplicate `.env`; do not use them in new configurations.

Unrecognised keys are ignored. Invalid JSON, an unknown profile, a missing
required field, or a `league_type` outside `{9cat, points}` raises `ConfigError`
with an actionable message.

### Auth storage

| Path | Purpose |
| --- | --- |
| `auth/<profile>/token.json` | Cached token read at startup if `auth/<profile>/.env` is absent. |
| `auth/<profile>/.env` | yfpy 17 token store, refreshed in place; when present, `token.json` is ignored. |
| `auth/token.json` | Legacy single-token layout, migrated once to `auth/<profile>/token.json`. |

All of these are bearer credentials. Treat the directory as secret material.

## Running Multiple Profiles and Leagues

Use one profile per league/team and select it with `--profile`:

```json
{
  "default_profile": "redraft",
  "profiles": {
    "redraft": {
      "league_id": "123456",
      "team_id": "1",
      "game_code": "nba",
      "league_type": "9cat"
    },
    "dynasty": {
      "league_id": "204411",
      "team_id": "4",
      "game_code": "nba",
      "league_type": "points"
    }
  }
}
```

```bash
python src/main.py --profile redraft
python src/main.py --profile dynasty
```

Each profile keeps its own OAuth state under `auth/<profile>/`, so authorization
is per league account. Credentials are shared from `.env` unless a profile
overrides `client_id`/`client_secret`. There is no batch mode: run once per
profile, for example in a shell loop or separate scheduled jobs. An unknown
profile name exits with status 2 and lists the available profiles.

## Testing

The suite uses the standard-library `unittest` runner. It requires no credentials,
makes no network calls, and completes in well under a second.

```bash
# Full suite (79 tests)
python3 -m unittest discover -s tests -t .

# One module
python3 -m unittest tests.test_analysis -v

# One test
python3 -m unittest tests.test_analysis.TestAverageHistory.test_empty_history_returns_empty -v
```

| Module | Tests | Covers |
| --- | --- | --- |
| `tests/test_config_loader.py` | 9 | Environment-only mode, config-file profile resolution, env-over-file precedence, unknown profile and malformed JSON errors, invalid `league_type`, legacy `gemini_api_key`, default LLM values. |
| `tests/test_strategy_chain.py` | 21 | JSON extraction from fenced/prose/nested responses, Pydantic transaction validation and normalisation, `StrategyLLM` construction and missing-key errors, prompt contract (required sections, constraints, league-type objective). |
| `tests/test_analysis.py` | 17 | History averaging and comparison, live-matchup comparison, roster grouping, injury status detection, IL/IL+ capacity, waiver-pool compaction, strategy-context assembly. |

`tests/__init__.py` inserts `src/` on `sys.path`, so test modules import
`analysis`, `schemas`, `utils.*`, and `prompts.*` directly instead of the `src.*`
package. Start new test modules with `import tests` to preserve that behaviour.

Not covered: `src/utils/yahoo_api.py` parsing and pagination, `src/utils/scheduler.py`
abbreviation mapping, `src/nodes.py` graph behaviour, and `src/main.py` OAuth and
CLI paths. These are the highest-value next additions and require mocked HTTP
sessions rather than live calls.

To add a test:

1. Create `tests/test_<area>.py` with `unittest.TestCase` subclasses; discovery
   picks it up automatically.
2. `import tests` before importing application modules.
3. Isolate environment and filesystem state: `mock.patch.dict(os.environ, ...)`,
   `tempfile.TemporaryDirectory()`, and pass `config_path` to `load_config`.
4. Mock Yahoo/LLM clients instead of calling them; the suite must stay offline and
   deterministic (no sleeps, no real keys, no ordering dependencies).
5. Prefer one behaviour per test and assert the message content of `ConfigError`
   where the guidance matters.

## Manual Verification and Smoke Checks

These checks need no Yahoo or LLM credentials.

```bash
# 1. Syntax/import check for every source file
python3 -m compileall -q src

# 2. Full offline test suite
python3 -m unittest discover -s tests -t .

# 3. CLI surface
python3 src/main.py --help

# 4. Graph wiring and read-only node set
python3 - <<'PY'
import sys
sys.path.insert(0, "src")
from main import build_graph

print(sorted(build_graph().get_graph().nodes))
PY
```

Expected node list: `['__end__', '__start__', 'analyze_injuries', 'fetch',
'format_output', 'llm_strategy', 'optimize_roster']`. No write helper appears in
the graph.

```bash
# 5. Configuration resolution with dummy values, no config.json
YAHOO_CONSUMER_KEY=dummy YAHOO_CONSUMER_SECRET=dummy \
YAHOO_LEAGUE_ID=123456 YAHOO_TEAM_ID=7 \
python3 - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "src")
from utils.config_loader import load_config

cfg = load_config(config_path=Path("/tmp/does-not-exist.json"))
print(cfg["profile_name"], cfg["league_id"], cfg["team_id"])
print(cfg["game_code"], cfg["league_type"], cfg["llm"]["model"])
PY
```

```bash
# 6. Missing-credential path (run in a clean clone with no .env)
env -u YAHOO_CONSUMER_KEY -u YAHOO_CONSUMER_SECRET \
    -u YAHOO_LEAGUE_ID -u YAHOO_TEAM_ID \
python3 - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "src")
from utils.config_loader import ConfigError, load_config

try:
    load_config(config_path=Path("/tmp/does-not-exist.json"))
except ConfigError as exc:
    print("ConfigError:", exc)
PY
```

If you already created `.env`, it supplies real credentials and this check will
succeed instead of raising; use a clean clone to exercise the failure path.

Live smoke test with real credentials:

```bash
python src/main.py --profile my_account
```

Confirm, in order:

- OAuth completes (first run) and later runs do not open a browser.
- The season week in the banner matches the current Yahoo week.
- Roster, matchup, and free-agent tables print.
- The lineup block is labelled `read-only`.
- Either an AI analysis appears or `[LLM ERROR]` explains why not.
- No roster change occurs in the Yahoo UI.
- `git status --short` shows no new `.env`, `config.json`, token, or report files.

## Deployment Options

> **Constraints for any deployment:** the workflow is read-only but requires a
> writable, persistent `auth/<profile>/` volume; the first authorization is
> interactive and needs a browser; one process handles one profile; output goes to
> stdout, not to files. There is no server mode.

### Local CLI (current best fit)

Run manually before lineup lock or whenever you want a weekly plan. Full manual
control, and OAuth works as designed.

### Scheduled local job (cron)

```bash
mkdir -p logs
crontab -e
# add:
# 0 8 * * * cd /opt/fantasy && .venv/bin/python src/main.py --profile my_account >> logs/run.log 2>&1
```

Notes:

- The first interactive authorization must be done once from a desktop session
  before scheduling; afterwards the token refreshes headlessly.
- `logs/` is git-ignored, as are `*.log` files. Rotate or truncate them yourself.
- The loader reads `.env` from the project root, so cron does not need to export
  the variables. Set `PATH` if `.venv/bin/python` is not used explicitly.
- Capture stdout: reports are printed, never written to disk by the application.

### systemd (user units)

`~/.config/systemd/user/fantasy-agent.service`:

```ini
[Unit]
Description=Fantasy NBA weekly plan
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=%h/fantasy
Environment=PYTHONUNBUFFERED=1
ExecStart=%h/fantasy/.venv/bin/python src/main.py --profile my_account
```

`~/.config/systemd/user/fantasy-agent.timer`:

```ini
[Unit]
Description=Run the Fantasy NBA agent daily

[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user start fantasy-agent.service
systemctl --user enable --now fantasy-agent.timer
journalctl --user -u fantasy-agent.service -n 100
```

Adjust `WorkingDirectory` and `ExecStart` if the checkout is not at `~/fantasy`.
Complete the interactive authorization first (see [First Run](#first-run-and-what-to-expect));
`systemctl --user start` is for verifying a run once a token exists. The user
running the service must own `auth/`. Do not add `ProtectHome` or a read-only home
to the unit unless `auth/` is mounted writable elsewhere.

### Docker sketch

The repository has no Dockerfile; this is a starting point, not a supported
artifact.

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
RUN useradd --create-home app \
    && mkdir -p /app/auth \
    && chown -R app:app /app
USER app

ENTRYPOINT ["python", "src/main.py"]
```

```bash
docker build -t fantasy-agent .

docker run --rm \
  --env-file .env \
  -v "$PWD/auth:/app/auth" \
  -v "$PWD/config.json:/app/config.json:ro" \
  fantasy-agent --profile my_account
```

Caveats: the container is headless, so the interactive OAuth callback will not
complete there. Authorize once on the host and seed the `auth/` volume (those
files are sensitive), or run the container with browser access and a forwarded
callback. The mounted `auth/` directory must be writable by the container user
(`app`, an unprivileged UID). Never bake `.env`, `config.json`, or tokens into the
image. The container needs no write access outside `/app/auth`.

### CI note

Tests need no credentials, so CI should run only the offline suite:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -r requirements.txt
      - run: python -m unittest discover -s tests -t .
```

Do not add live smoke tests or repository secrets to CI: pull requests from forks
do not receive secrets, and a live run performs dozens of Yahoo calls per job.
There is no workflow file or linter configuration in the repository yet.

## Observability and Logging

| Signal | Where it goes | Notes |
| --- | --- | --- |
| Progress and warnings | stderr via `logging` | Format `HH:MM:SS LEVEL name: message`. |
| Tables and final report | stdout via `print` | Roster, matchup, free agents, plan, transactions, token usage. |
| Default level | `INFO` | `configure_logging()` in `src/main.py`. |
| Debug level | `DEBUG` | Enabled by `--verbose`; `urllib3`, `httpx`, `openai`, and `httpcore` stay pinned to `WARNING`. |
| Per-node trace | `state["debug_logs"]` | Appended by each node (`fetch`, `injuries`, `lineup`, `strategy`); not printed. |

`--verbose` sets the root level to `DEBUG`, but the application does not emit
`DEBUG` records and the HTTP clients remain pinned to `WARNING`, so expect little
extra output. There are no log files, metrics, or telemetry. Redirect stdout and
stderr explicitly when you need durable records.

`run_fantasy_app()` returns the final state, so the debug trace is available
programmatically:

```python
import sys

sys.path.insert(0, "src")
from main import run_fantasy_app

state = run_fantasy_app("my_account")
for entry in state["debug_logs"]:
    print(entry)
```

The output node also builds a JSON-shaped report internally, but it prints the
human-readable sections and does not write the JSON anywhere; there is no
`--json` flag yet.

Degradations are logged, not raised:

- League settings unavailable: IL/IL+ slots and the weekly add limit fall back to
  `0`, and the prompt then treats the add limit as unknown/unlimited.
- Game key or current week unresolved: game key `466`, week `1`.
- ESPN schedule unavailable: free agents cannot be confirmed as playing and the
  pool can come back empty.
- LLM unavailable: `llm_error` is set, `[LLM ERROR]` prints, exit status stays 0.

### Token usage and cost

Token counts come from the provider's `usage_metadata`. The report prints prompt,
completion, and total tokens plus an estimate:

```text
[TOKEN USAGE]
 -> Prompt:     ...
 -> Completion: ...
 -> Total:      ...
 -> Est. cost:  $0.000000
```

The estimate uses `MODEL_COST_PER_MILLION` in `src/nodes.py`, which lists
`google/gemini-2.0-flash-001`, `google/gemini-2.0-flash`, and
`google/gemini-1.5-flash`. Unknown model names fall back to `$0.10` per million
tokens. Treat the figure as a rough, display-only estimate; it does not model
provider-specific input/output pricing.

## Security Review

### Incident summary

An earlier revision of this repository committed the Yahoo consumer key and
consumer secret, cached OAuth access/refresh tokens, and the LLM API key. The same
values were hardcoded in scratch scripts. Those scripts have been deleted, and
`.env`, `config.json`, and `auth/*` are now ignored. **The values remain in git
history and must be treated as compromised.**

Ignoring a file does not remove it from the index. Verify the current tree:

```bash
git check-ignore -v .env config.json auth/token.json
git ls-files | grep -E '(^|/)(\.env|config\.json|token\.json|private\.json)$'
```

The second command should print nothing. If it lists secret files, untrack exactly
those paths and commit:

```bash
git rm --cached <paths printed above>
git commit -m "chore: stop tracking local secrets"
```

### Rotate the affected credentials now

Yahoo:

1. Open the Yahoo Developer dashboard and **delete** the exposed app. This
   invalidates the consumer key/secret and every token issued through it.
2. Create a replacement app with **Fantasy Sports -> Read** and Redirect URI(s)
   `https://localhost:8080`.
3. Put the new key and secret in `.env`.
4. Delete `auth/<profile>/` (including both `token.json` and `.env`) and re-run to
   perform a fresh consent flow. Old tokens are invalid anyway.

LLM provider:

1. Revoke the exposed key in the provider dashboard.
2. Create a replacement key, update `.env`, and restart any scheduled jobs that
   read it.
3. Check provider usage and billing for unexpected activity.

Rotate first, purge second. Purging history does not make a leaked credential safe.

### Purge git history (optional, after rotation)

`git filter-repo` (recommended):

```bash
python -m pip install git-filter-repo
git filter-repo --invert-paths --path .env --path config.json --path auth
mkdir -p auth && touch auth/.gitkeep && git add auth/.gitkeep
```

BFG:

```bash
bfg --delete-files '{.env,config.json,token.json,private.json}'
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

Both rewrite history, require a force-push, and invalidate existing clones; alert
collaborators before running them. `git filter-repo` expects a clean working tree
and removes the `origin` remote, so run it on a fresh clone and re-add the remote
afterwards. Recreate `auth/.gitkeep` as shown. Keep `config.example.json` and
`.env.example`. If the repository has already been public, assume the values were
scraped regardless of history edits.

### Least privilege

The Yahoo app needs **Read** only. Everything in this repository is a GET-style
read; the unused write helpers would require **Read/Write** and a fresh
authorization. Do not grant write scope for a read-only workflow.

### File permissions

`auth/` tokens grant account access and yfpy rewrites `auth/<profile>/.env`:

```bash
chmod 700 auth
chmod 600 .env
chmod 600 config.json 2>/dev/null || true
find auth -type f -exec chmod 600 {} +
```

On a shared host, run the agent as a dedicated user and keep the checkout outside
world-readable paths. Do not back up or sync `auth/` to a shared drive.

### Day-to-day rules

- Never commit `.env`, `config.json`, or token files; never paste their contents
  into issues, pull requests, logs, or screenshots.
- Run the ignore check above after adding files; use `git add -n` before staging.
- Treat a lost laptop, a leaked backup, or a screen-shared terminal as credential
  exposure and rotate.
- CI needs no secrets; keep it that way.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `ConfigError: Missing 'client_id'...` / `'client_secret'` | `.env` absent, empty, or not in the project root; `python-dotenv` not installed | `cp .env.example .env`, fill values, `python -m pip install -r requirements.txt`. The loader always reads the repository-root `.env`. |
| `ConfigError: Profile 'x' not found` | `--profile` does not match any key in `config.json` | Use one of the listed profiles or fix `default_profile`. |
| `ConfigError: league_type must be one of ...` | Typo or unsupported format | Set `LEAGUE_TYPE` to `9cat` or `points`. |
| `ConfigError: config.json is not valid JSON` | Trailing comma, comment, or malformed file | Validate with `python -m json.tool config.json`. |
| Browser never opens or consent fails with a redirect/callback error | App is not an Installed Application, or its **Redirect URI(s)** is wrong | Set Redirect URI(s) to `https://localhost:8080` and retry from a desktop session. |
| Yahoo returns 401 / `token_rejected` after working previously | Refresh token expired or was revoked; consumer secret changed | Delete `auth/<profile>/` and re-run for a fresh consent flow; ensure `.env` holds the current key/secret. |
| `oauth_problem=signature_invalid` or 403 from Yahoo | Consumer key and secret do not belong together, or contain whitespace/quotes | Re-copy both from the Yahoo app page; avoid quotes in `.env`. |
| `TypeError: ... unexpected keyword argument 'env_file_location'` or `'save_token_data_to_env_file'` | Installed yfpy is older than 17 | `python -m pip install -U 'yfpy>=17.0.0'`; confirm with `pip show yfpy`. |
| `ModuleNotFoundError: No module named 'state'` (or `utils`) | A module was added to `src/` without the `try: from .x import ... / except ImportError: from x import ...` pattern | Add the same dual-import guard used by `src/nodes.py` and `src/utils/yahoo_api.py`. |
| `ModuleNotFoundError: langgraph` / `langchain_openai` | Virtualenv not activated or dependencies missing | Activate `.venv` and reinstall `requirements.txt`. |
| `[LLM ERROR] ... AuthenticationError` / HTTP 401 | Wrong key, revoked key, or key sent to the wrong `base_url` | Match `LLM_API_KEY` to the provider in `LLM_BASE_URL`; check for stray quotes. The run still exits 0. |
| `[LLM ERROR] ... 429` / `402` / rate limit or insufficient credit | Provider throttling or empty balance | 429/5xx are retried with backoff up to `max_retries`; add credit, wait, or switch `LLM_MODEL`. 402 is a billing failure. Non-fatal. |
| Free-agent pool is empty on a game day | ESPN scoreboard unreachable, or every candidate is injured/on waivers | Check ESPN reachability; the code skips status other than `OK` and `W` (waivers), and only scans the top 75. |
| No games today and no streaming adds proposed | ESPN returns no events; all free agents are filtered out | Expected on off-days and breaks. Re-run on a game day. |
| Plan says `NO SEASON BASELINE AVAILABLE` and ranks players with little justification | Week 1, or a league with no completed weeks: there is no history to compare against, and player stats are still zero | Expected. The agent states the gap explicitly rather than inventing a baseline; re-run after a few weeks have completed. |
| Week shown as 1 / game key 466 unexpectedly | Yahoo game or league-settings lookup failed; fallback used | Check connectivity and the `YAHOO_LEAGUE_ID`; the warning is logged. |
| Plan prints with `[LLM ERROR]` instead of AI analysis | No `LLM_API_KEY`, or the configured provider rejected the request | Set the key and re-run. The deterministic plan is still valid. |

## Roadmap

- Rotate the exposed Yahoo and LLM credentials and purge git history; add an
  ignore/tracked-secret check to CI.
- Add `--json` and `--output PATH` for automation; persist the report currently
  discarded by the output node.
- Cache Yahoo responses per league/day to cut run time and API calls.
- Add a projected-stats source and replace the greedy lineup heuristic with a
  real optimizer.
- Provide a deterministic fallback strategy so a failed LLM leaves a complete
  machine-readable plan.
- Add an explicit opt-in execution path behind a confirmation gate and an audit
  log; keep read-only as the default.
- Add a CI workflow that runs the offline suite on push and pull request.
