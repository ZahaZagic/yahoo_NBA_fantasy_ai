# Documentation

Index of the project documentation. Start with the root [README](../README.md) to
install and run the agent; this folder covers domain rules, implementation detail,
operations, and review history.

## Guides

- [Root README](../README.md) — install, credential setup, quickstart, usage, configuration reference. Start here.
- [Project Business Logic](PROJECT_BUSINESS_LOGIC.md) — the problem, target user, and roster/IL/waiver decision rules. For domain and product readers.
- [Technical Development Guide](TECHNICAL_DEVELOPMENT_GUIDE.md) — module responsibilities, data flow through the graph, and implementation notes. For contributors.
- [Operations, Testing, and Deployment](OPERATIONS_TESTING_DEPLOYMENT.md) — prerequisites, full configuration tables, first run, tests, smoke checks, deployment, logging, and the security incident and rotation procedure. For operators.
- [Code Review and Remediation Log](CODE_REVIEW.md) — audit findings by severity, what was fixed, what remains open. For reviewers.
- [Resume Bullets](RESUME_BULLETS.md) — compact project descriptions for portfolio use.

## Current Project Shape

One runnable, read-only LangGraph workflow:
`fetch -> optimize_roster -> analyze_injuries -> llm_strategy -> format_output`.

- `src/main.py` — CLI (`--profile`, `--verbose`), per-profile auth directory and legacy token migration, Yahoo query setup, graph compilation and run.
- `src/nodes.py` — the five graph nodes, league-rules lookup, greedy lineup assignment, LLM call, printed report, and model cost map.
- `src/state.py` — `AgentState`, the `TypedDict` passed between nodes.
- `src/analysis.py` — pure helpers for injury status, history averaging/comparison, roster grouping, IL capacity, waiver compaction, prompt context.
- `src/schemas.py` — Pydantic models validating the LLM's transaction JSON.
- `src/prompts/strategy.py` — system prompt, required report sections, human-prompt builder.
- `src/utils/config_loader.py` — environment -> `config.json` -> defaults resolution and `ConfigError`.
- `src/utils/llm.py` — `ChatOpenAI` wrapper with timeout/retries and JSON extraction.
- `src/utils/yahoo_api.py` — Yahoo REST reads for roster, matchup, history, free agents; also holds the unused write helpers.
- `src/utils/stats_parser.py` — Yahoo stat-ID mapping, response flattening, injury-status normalisation.
- `src/utils/scheduler.py` — ESPN scoreboard lookup for teams playing on a date.
- `tests/` — 79 offline `unittest` tests; run `python3 -m unittest discover -s tests -t .`.

Earlier prototypes (`fantasy_bot/`, `src/core/`, `src/agents/`, `src/tools/`), root
debug scripts, committed bytecode, and `.idea/` were removed during the open-source
cleanup. They are recoverable from git history but are not part of the current tree.
