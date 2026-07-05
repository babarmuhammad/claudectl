---
description: "claudectl memory: Claude/(root)"
globs: "**"
---
# Claude/(root) — claudectl root module is a Windows terminal UI workspace launcher for Claude Code that provides persistent project memory, session management, an architecture graph, MCP awareness, and per-project launch configuration.
- claudectl (module) — Windows terminal UI workspace manager layering persistent memory and launch control on top of Claude Code.
- launcher stub (component) — claude-sessions.py entry script that applies the saved theme, dispatches to run() or launch_from_choice(), and persists crash tracebacks to a temp log.
- semantic memory (service) — Claude-built knowledge layer with token-budgeted injection: a ≤250-token micro-index, per-module detail, and an optional ≤600-token per-prompt subgraph hook.
- CLAUDE.md index (model) — Bounded ≤250-token always-on context block kept small via consolidation and rollups.
- path-scoped rules (model) — .claude/rules/ files holding per-module knowledge loaded only when Claude touches matching paths.
- prompt hook (component) — Optional hook that injects only the prompt-relevant knowledge subgraph within a token budget.
- architecture graph (component) — Animated, expandable HTML dependency graph spanning Python, C/C++, C#, and JS/TS from project level down to single files.
- session workspace (service) — Browse, search, tag, fork, resume, and archive Claude Code sessions across all projects.
- lesson learning (service) — Distills durable fixes, decisions, and preferences from transcripts with human review, relevance injection, and staleness decay.
- health checks (service) — Pre-launch diagnostics plus context-loss insurance after /compact, permission-fatigue reduction, and daily usage tracking.
- token-burn advisor (service) — Nudges routine work off expensive models and pairs with Plan→Execute to run cheap models for execution.
Relations: claudectl contains launcher stub; launcher stub contains crash logger; claudectl contains semantic memory; semantic memory contains CLAUDE.md index; semantic memory contains path-scoped rules; semantic memory uses prompt hook; claudectl contains architecture graph; claudectl contains session workspace; claudectl contains lesson learning; claudectl contains health checks; claudectl contains token-burn advisor; claudectl contains adaptive agents; lesson learning uses semantic memory
