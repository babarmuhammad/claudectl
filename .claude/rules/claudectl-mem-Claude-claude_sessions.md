---
description: "claudectl memory: Claude/claude_sessions"
globs: "**"
---
# Claude/claude_sessions — claude_sessions is the core package of claudectl, a Windows terminal UI workspace manager for Claude Code that discovers projects, browses/parses session transcripts, manages subagents and CLAUDE.md memory, builds a semantic knowledge graph, and estimates usage costs.
- main (component) — Entry point that handles scriptable CLI subcommands (workspace status, recall), discovers projects, and launches the TUI.
- config (component) — Central configuration: paths, colors/themes, settings load/save, claude.exe resolution, model and cost constants, logging.
- sessions (component) — Parses session jsonl transcripts into cached stats (preview, title, token usage per model, timestamps, branch).
- session_menu (component) — Sessions browser TUI supporting rename, tag, archive/move, delete, plus lesson-scan badges and background memory refresh triggers.
- agents (component) — Manages Claude Code subagent .md definitions with YAML frontmatter: browse category library, scaffold, AI-generate, edit, delete.
- claude_md (component) — Maintains CLAUDE.md files: writes sentinel memory blocks, validates AI-generated content, preserves machine-maintained blocks, resolves memory-file hierarchy with @imports.
- memory (service) — Claude-powered semantic knowledge graph of entities/relations extracted from source, persisted under .claudectl/memory/ with incremental hash-based refresh and background threads.
- connections (component) — Builds an interactive HTML project architecture graph from directory hierarchy and cross-language dependency edges, cached and progressively disclosed.
Relations: main uses config; main uses sessions; main calls session_menu; main uses ui; main uses render; main calls recall; session_menu uses sessions; session_menu calls claude_md; session_menu calls memory; session_menu uses SessionTranscript; sessions uses SessionTranscript; stats depends_on sessions; stats uses config; memory contains KnowledgeGraph; memory uses config; recall uses KnowledgeGraph; connections uses claude_md; connections uses render; claude_md uses sessions; claude_md uses config; agents contains AgentDefinition; agents uses ui; agents uses config; ui depends_on render; claude_md mentions memory; recall mentions memory
