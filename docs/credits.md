---
description: >-
  The projects and ideas claudectl is built on — the Claude Code ecosystem work behind its
  memory, review, skills, model routing and starter templates.
---

# Credits & inspiration

claudectl is built on ideas from the wider Claude Code ecosystem. With thanks:

- **[microsoft/markitdown](https://github.com/microsoft/markitdown)** — document→markdown token-efficiency thinking (doc ingestion is on the roadmap).
- **[anthropics/claude-code](https://github.com/anthropics/claude-code)** `code-review` plugin — the confidence-scoring + high-threshold + CLAUDE.md-compliance review pattern behind `claudectl review`.
- **[thedotmack/claude-mem](https://github.com/thedotmack/claude-mem)** — the session-observation → summary → `SessionStart` injection pattern behind **Recent-work memory**.
- **[anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official)** — the Skills (`SKILL.md`) extension model and plugin structure.
- **[diegosouzapw/OmniRoute](https://github.com/diegosouzapw/OmniRoute)** (MIT) — self-hosted free-tier model proxy; originally the inspiration for **Economy model routing**, now also the backend behind **Settings → Free execution**, which routes the *execute* half of [Plan → Execute](plan-execute.md) to OmniRoute's free tier while planning stays on your real Anthropic account.
- **[olsenbrands/fable-foreman](https://github.com/olsenbrands/fable-foreman)** (MIT, Jordan Olsen) — the Claude Code skill + worker/verifier subagent pattern for delegating execution to cheaper models under a frontier model's plan. Installable from **⚙ Skills → Install from GitHub**.
- **[claudemarketplaces.com](https://claudemarketplaces.com/)** — skill/plugin discovery; the `caveman` token-compression skill inspired the bundled `token-economy` starter.
- **[VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents)** — the community catalog claudectl's [agent library](agent-library.md) mirrors — 150+ agents in 10 categories. All credit for the agents goes to their original authors.
- **[topoteretes/cognee](https://github.com/topoteretes/cognee)** and **[Aider's repo-map](https://aider.chat/docs/repomap.html)** — graph memory and retrieval budgeting, both reimplemented from scratch in pure stdlib for [project memory](memory.md).
- **[Ponytail](https://github.com/DietrichGebert/ponytail)** — the code-minimization rule behind one of the bundled [hooks](hooks.md).

Bundled starter skills under `claude_sessions/skills_templates/` are original write-ups
inspired by patterns in these community collections, each credited in-file:
[alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills),
[ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills),
[obra/superpowers](https://github.com/obra/superpowers),
[khalilbenaz/claude-skills-collection](https://github.com/khalilbenaz/claude-skills-collection).
They follow [Conventional Commits](https://www.conventionalcommits.org/) and
[Keep a Changelog](https://keepachangelog.com/) where relevant.

claudectl itself is MIT licensed.
