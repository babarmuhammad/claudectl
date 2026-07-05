---
description: "claudectl memory: Claude/tests"
globs: "tests/**"
---
# Claude/tests — Test suite for claudectl covering the TUI main menu, session browser, agents manager, usage stats, memory knowledge graph, connections graph, and launch integration via a scripted-keyboard sandbox harness.
- harness (module) — Reusable TUI simulation harness providing key scripts, fake msvcrt input, stdout capture, and sandboxed config/session fixtures.
- Sandbox (component) — Fake config tree that redirects all module paths into tmp dirs and seeds synthetic projects and sessions.
- TuiScript (component) — Fake msvcrt fed from a scripted key list that raises OutOfKeys when exhausted to end a flow.
- CapturingStdout (component) — stdout stand-in that records rendered output and exposes ANSI-stripped plain text for assertions.
- make_jsonl (component) — Fixture writer producing synthetic session transcripts with usage data and AI titles.
- test_tui_main (module) — End-to-end tests of the main menu: project selection, quick-resume, type-to-filter, and help screen.
- test_tui_sessions (module) — Tests of the sessions menu: resume, new/continue/terminal, rename, archive/restore, and delete flows.
- test_tui_agents (module) — Tests of agent library listing, agents.json building, project agent sync, and language-based suggestions.
- test_tui_stats (module) — Tests of the usage dashboard, daily bucketing, cost estimation caching, and partial-scan behavior.
- test_memory (module) — Tests of the semantic memory graph: full and incremental refresh, temporal invalidation of deleted units, and module granularity.
- test_connections (module) — Tests of the architecture graph: hierarchy nodes, cross-language file dependencies, ranking, and cache signatures.
- test_features (module) — Unit tests of pure helpers: choice-line encoding, session stats, cost math, token formatting, and slugs.
Relations: harness contains Sandbox; harness contains TuiScript; harness contains CapturingStdout; harness contains make_jsonl; harness depends_on claude_sessions; test_tui_main uses harness; test_tui_sessions uses harness; test_tui_agents uses harness; test_tui_stats uses harness; test_memory uses harness; test_connections uses harness; test_launch_integration uses harness; test_tui_main calls claude_sessions; test_features calls claude_sessions; Sandbox uses make_jsonl
