# Resume Bullets

These bullets follow the referenced resume style: project name, tech stack, compact implementation detail, and measurable or practical impact. Adjust metrics only if you can defend them with real usage data.

## Primary Version

Agentic Strategic Decision System (NBA Fantasy) | Python, LangGraph, LangChain, Pydantic, Yahoo Fantasy API, Gemini -- Constructed a state-driven multi-agent assistant to automate fantasy basketball roster analysis, integrating Yahoo league data, live matchup stats, injury signals, historical trends, and waiver-wire candidates into a repeatable decision workflow. Designed specialized LangGraph nodes for data synchronization, daily lineup optimization, IL management, LLM strategy generation, and structured transaction output, enabling explainable category-specific recommendations for competitive 9-category and points leagues. Implemented human-gated add/drop and roster-move logic with Yahoo XML transaction payload scaffolding, preserving manager control while creating a path toward safe automation. Hardened the LLM boundary with LangChain retries/timeouts and Pydantic schema validation so a malformed model response can never silently become a roster move.

## Shorter Variants

- Built a LangGraph-powered Yahoo Fantasy NBA assistant that aggregates roster, matchup, injury, schedule, and waiver-wire data to generate explainable weekly optimization plans for 9-category and points leagues.
- Designed modular decision nodes for data fetching, lineup optimization, IL analysis, LLM strategy reasoning, and transaction JSON generation, improving the speed and consistency of fantasy roster decisions.
- Integrated Yahoo Fantasy API, ESPN schedule data, and Gemini/OpenRouter analysis to rank waiver candidates, identify category gaps, and produce human-reviewed add/drop recommendations.

## More Technical Variants

- Engineered a profile-based Yahoo Fantasy automation workflow using `yfpy`, LangGraph `StateGraph`, and typed state propagation to coordinate roster ingestion, matchup analysis, free-agent search, and LLM-generated strategy output.
- Implemented robust Yahoo JSON parsing utilities to flatten nested API responses, normalize NBA stat IDs, map injury statuses, calculate per-game player averages, and reconcile ESPN/Yahoo team abbreviation differences for schedule-aware streaming.
- Developed a greedy lineup optimizer that assigns healthy, editable players with games today across specialized, flex, center, and utility slots while producing manual start/bench instructions.
- Built a LangChain-backed strategy engine, split into a stable system prompt of hard decision rules and a per-week data payload, that compares live and historical category gaps, classifies roster value tiers, enforces drop-protection rules, and returns transaction JSON validated against a Pydantic schema.
- Separated all scoring and comparison logic into a pure, side-effect-free module and covered it with a 60-test `unittest` suite (import modes, prompt rendering, JSON extraction edge cases, and a full mocked end-to-end graph run), making the pipeline verifiable without Yahoo or LLM credentials.

## Impact-Oriented Variants

- Reduced manual fantasy matchup research by consolidating roster health, weekly add limits, live category gaps, historical trends, and waiver-wire opportunities into a single CLI decision report.
- Improved roster decision safety by enforcing human review for add/drop recommendations and encoding safeguards around elite-player protection, GTD uncertainty, IL capacity, and weekly transaction limits.
- Created a reusable foundation for future fantasy automation with profile-based configuration, environment-first secrets handling, transaction payload builders, and documentation for setup, testing, deployment, and security hardening.

## If You Want One Polished Project Entry

Agentic Strategic Decision System (NBA Fantasy) | Python, LangGraph, Yahoo Fantasy API, Gemini -- Constructed a state-driven multi-agent system to automate roster management and waiver-wire strategy in competitive Yahoo Fantasy NBA leagues, integrating live matchup data, historical category trends, injury/IL signals, ESPN schedule context, and free-agent rankings. Designed modular reasoning nodes for API synchronization, lineup optimization, category-risk analysis, and LLM-based strategy generation, producing explainable weekly action plans and structured transaction JSON. Implemented human-gated automation safeguards around add/drop execution, high-value player protection, GTD uncertainty, and weekly transaction limits to balance aggressive streaming with long-term roster value.

