---
description: >-
  Plan with an accurate model, execute with a cheap or free one — claudectl's Plan to
  Execute flow, plus the full OmniRoute setup, troubleshooting and standalone free-tier
  sessions.
---

# Plan → Execute & OmniRoute

`⇧X` in the TUI; its own **Plan → Execute** project tab in the GUI.

Plan with an accurate model, execute with a cheaper — or completely free — one, for the
same result. claudectl plans the task headlessly with `plan_model` (default Opus 5, effort
picked per task), shows you the plan to approve/reject, saves it to
`.claudectl/plan-latest.md`, then launches a **real, full interactive `claude` session** —
same account, agents, skills, system prompt, and `--add-dir` roots this project already has
— on `exec_model` (default Sonnet 5), seeded to read and execute that plan. Expensive
reasoning happens once; the build runs on the cheap tier.

## Free execution via OmniRoute

OmniRoute is one of several backends claudectl can route a session to — see
[Model providers](providers.md) for local models, OpenRouter and self-hosted servers, and
for the list of what a backend swap costs. Point the execute half at a local
[OmniRoute](https://github.com/diegosouzapw/OmniRoute) proxy instead of your Anthropic
account, and it runs on OmniRoute's aggregated free-tier providers. Left on *Auto* (the
default), OmniRoute itself scores every currently-healthy free model per request
(health/quota/cost/latency/task-fit) and transparently falls back to the next-best one if
the current one is rate-limited or exhausted — no manual model juggling, and claudectl
auto-starts OmniRoute in the background the moment you run a task through it, so there's no
terminal to babysit.

### Setup (one-time)

Connecting at least one provider happens in OmniRoute's own dashboard — claudectl never
touches that credential. The CLI commands for adding providers are broken on Windows
(confirmed upstream), so the dashboard is the only reliable path.

1. Install OmniRoute: `npm install -g omniroute` (PowerShell: run on its own line, or `;`-chain — no `&&`).
2. Set a dashboard password once: `omniroute setup --password <yours>`.
3. Start it (`omniroute`, or let claudectl auto-start it on first use) and open `http://localhost:20128` → log in → **Providers → Add Provider**, or go straight to **Free tiers**. Several are genuinely zero-signup (Pollinations, Puter, NVIDIA, OpenCode, FriendliAI, Coze, and more) — connect one or two. *(Note: OmniRoute's marketing claims ~90 free providers; what's actually reachable without a real signup is a smaller genuinely-keyless subset — worth checking the current list yourself in the dashboard. The CLI `omniroute providers add` commands crash on this platform — dashboard only for now.)*
4. In claudectl's GUI **Settings → Model provider**: set **Backend** to *OmniRoute*, leave the base URL at `http://localhost:20128`, click **Refresh** — the status dot shows provider(s) active once step 3 is done. The built-in connection self-check can report false negatives (confirmed: reports working no-auth connections as broken); use **Send a live test** for the real answer. Leave **Execute model** on *Auto*, Save.
5. Open a project's **Plan → Execute** tab, describe a task, pick **Execute via → OmniRoute**, approve the plan. First run starts OmniRoute for you if it isn't already running.

### Troubleshooting

- **Status dot shows "not running"** — OmniRoute auto-starts on first Plan→Execute run; click **Start now** on the Settings page to start it immediately, or run `omniroute` in a terminal.
- **"0 providers connected"** — open the dashboard at `http://localhost:20128`, log in (password from step 2), and add a provider under **Providers**. No providers = no free model to route to.
- **Live test fails** — use **Send a live test** on the Settings page; if it fails, the connection is genuinely broken. Try a different free provider in the dashboard (some providers are rate-limited or have exhausted daily quotas).
- **Self-check says connected but live test fails** — OmniRoute's own per-connection self-check can be wrong (confirmed). The live test is authoritative.

## OmniRoute standalone sessions

claudectl also supports launching a **standalone interactive `claude` session** through
OmniRoute, not just the Plan→Execute execute half. When you open a project in the TUI and
pick a model from the **OMNIROUTE** menu (appears only when OmniRoute is reachable on a
configured base URL), your session runs entirely on OmniRoute's free/cheap tier, with full
access to every Claude Code feature:

- **Agents & subagents** — all work. `CLAUDE_CODE_SUBAGENT_MODEL=claude-sonnet-5` is automatically set, so subagents always run on a capable model (Sonnet 5) even when the main session uses a free-tier model that may lack `tool_use` or have a small context window.
- **Skills** — load on demand, unchanged. Skills are client-side SKILL.md files discovered from `.claude/skills/`; the Sonnet 5 subagent model handles them correctly.
- **Per-project memory, hooks, MCP servers** — all client-side, model-agnostic. They load from `CLAUDE_CONFIG_DIR` and the project's `.claude/` as usual, unchanged.
- **Plan→Execute** — the plan-execute modal in the GUI has an **Execute via** toggle (Anthropic / OmniRoute). Selecting OmniRoute routes the execute half through OmniRoute (same agent/skill/memory guarantees). The Plan→Execute TUI path automatically picks the provider when `provider_exec_model` is configured.

### Caveats

- Anthropic usage tracking won't reflect OmniRoute spend (cost tracking is separate).
- Free-tier models often have small context windows (<16K tokens). Use the TUI's context-warning on `CLAUDE.md` + rules + plan over ~8K tokens.
- Some free models lack `tool_use`, which degrades agents, skills, and MCP tool calls. The Sonnet 5 subagent override covers the common case, but the main model's own capabilities remain the free model's.
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` is set automatically to block telemetry that free models might reject or that unnecessary calls to the Anthropic API may fail on.

When a free model dies mid-session rather than at launch, the
[failover proxy](statusline.md#model-failover) is what retries it against the next
candidate.
