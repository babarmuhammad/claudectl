---
title: Plan on an expensive model, execute on a free one
description: Run the expensive reasoning once, then hand the approved plan to a cheap or free model to build. How the two-model split works and where it breaks.
date: 2026-09-01
tags: [claude-code, cost, models, workflow]
author: Babar Muhammad Anas
faq:
  - q: Can I use a cheaper model to execute a plan written by a stronger one?
    a: Yes, and it is one of the largest cost levers available in Claude Code. Run a headless planning pass with the strong model, review and edit the plan, save it to a file, then launch a normal interactive session on a cheaper model seeded to read and execute that file. The expensive reasoning happens once; the mechanical work runs on the cheap tier.
  - q: Why pass the plan as a file instead of pasting it into the prompt?
    a: Because a long plan on the command line hits the Windows argv length limit and fails. Writing the plan to a path and giving the new session a one-line pointer to read it first keeps the command line short and lets the model pull the file in with its own tools, which also means it can re-read the plan later in the session.
  - q: What is OmniRoute and how does it relate to Claude Code?
    a: OmniRoute is a local proxy that aggregates free and cheap model providers behind one endpoint. Pointing the execute half of a plan-and-execute run at it means the build phase costs nothing against your Anthropic quota, while planning still happens on a strong Claude model. It is a third-party project, and connecting a provider happens in its own dashboard.
  - q: What breaks when you run Claude Code on a free-tier model?
    a: Two things, reliably. Free models often have small context windows — under 16K tokens — so a large CLAUDE.md plus rules plus a plan will not fit. And some lack tool use entirely, which degrades agents, skills and MCP calls; setting a capable subagent model covers the delegated work but not the main model's own limits.
  - q: Why does Claude Code hang instead of failing over when a model dies?
    a: Because Claude Code has no concept of failover. It sends each turn as a fresh request and, when one fails, retries the same request against the same model with backoff — so a model deregistered upstream, or a tool schema the provider rejects, makes the session look frozen indefinitely. A local proxy that rewrites the model on error is what turns that into a retry against the next candidate.
---

## The short answer

Most of the token cost of an agentic coding session is not the thinking, it is the doing — reading files, applying edits, running tests, fixing the typo, running them again. Those are different jobs with different model requirements. So split them: run one headless planning pass on an accurate model, review and edit the plan, save it to a file, then launch a *real* interactive session on a cheap or free model seeded to read and execute that file. Expensive reasoning happens once. The build runs on the cheap tier. The interesting engineering is entirely in the hand-off and in what breaks when the executing model is weak.

## The flow

`⇧X` in the terminal UI, or the **Plan → Execute** project tab in the GUI.

1. **Describe the task.** One prompt, as you would to any session.
2. **claudectl plans it headlessly** with `plan_model` — default Opus 5, reasoning effort picked per task. No interactive session, no back-and-forth; one pass producing a plan.
3. **You approve, edit or re-plan.** The plan appears in a monospace textarea for inline editing. "Re-plan" sends your feedback back for a regeneration. Every generated plan is auto-saved.
4. **The plan is written to `.claudectl/plan-latest.md`.**
5. **A real, full interactive `claude` session launches** on `exec_model` — default Sonnet 5 — with the same account, agents, skills, system prompt and `--add-dir` roots the project already has, seeded to read and execute that plan.

Point 5 is the part worth being precise about. The execute half is not a headless `-p` run and not a constrained sub-mode. It is the ordinary interactive session you would have started anyway, with one extra instruction. You can interrupt it, argue with it, and take over.

Optionally, **per-step approval** gates execution step by step rather than letting it run the whole plan.

## Why the plan is a pointer, not a paste

The obvious implementation is to put the plan in the launch prompt. It fails on Windows: a long plan on the command line runs past the argv length limit and the launch dies, or worse, truncates.

So the plan goes to a path and the session gets a short system-prompt line saying where it is and to read it first. Three benefits fall out of that:

- The command line stays short regardless of plan size.
- The model pulls the file in with its own tools, so it can **re-read** the plan mid-session when it loses the thread.
- The same mechanism serves the context hand-off feature, which writes a whole prior transcript to `.claudectl/injected-context.md` and hands over a pointer to it for exactly the same reason.

`.claudectl/` is machine-local. Add it to `.gitignore` if the project does not already ignore it.

## Free execution via OmniRoute

Cheap is Sonnet. Free is [OmniRoute](https://github.com/diegosouzapw/OmniRoute) — a local proxy aggregating free-tier providers behind one endpoint. Point the execute half at it and the build phase costs nothing against your Anthropic quota.

Left on **Auto** (the default), OmniRoute scores every currently-healthy free model per request on health, quota, cost, latency and task fit, and transparently falls back to the next-best one when the current one is rate-limited or exhausted. claudectl auto-starts it in the background the first time you route a task through it, so there is no terminal to babysit.

Setup, once:

```bash
npm install -g omniroute
omniroute setup --password <yours>
omniroute                       # or let claudectl start it
# → http://localhost:20128 → log in → Providers → Add Provider / Free tiers
```

Then in claudectl's **Settings → Free execution — OmniRoute**: leave the base URL at `http://localhost:20128`, click **Refresh**, leave **Execute model** on *Auto*, save. Open a project's **Plan → Execute** tab, describe a task, pick **Execute via → OmniRoute**, approve the plan.

Three honest caveats about that setup, all confirmed rather than theoretical:

- OmniRoute's marketing claims roughly 90 free providers. What is actually reachable **without a real signup** is a smaller genuinely-keyless subset — Pollinations, Puter, NVIDIA, OpenCode, FriendliAI, Coze and a few more. Check the current list in the dashboard yourself.
- The `omniroute providers add` CLI commands crash on Windows. The dashboard is the only reliable path, which is also why claudectl never touches that credential.
- OmniRoute's own per-connection self-check reports false negatives — it will call a working no-auth connection broken. Use **Send a live test** for the authoritative answer.

Beyond the plan-execute split, claudectl can launch a **standalone** interactive session through OmniRoute: open a project in the TUI and pick a model from the **OMNIROUTE** menu, which appears only when OmniRoute is reachable.

## What actually breaks on a free model

This is the section that decides whether the idea is useful to you.

**Small context windows.** Free-tier models frequently sit under 16K tokens. Your `CLAUDE.md` plus rules files plus the plan has to fit inside that with room for the work. claudectl warns when the total passes roughly 8K. This is where a bounded always-on index stops being a nice-to-have and becomes the thing that makes free execution possible at all — see [the 250-token index pattern](/blog/cut-claude-code-token-costs).

**Missing tool use.** Some free models have no `tool_use`, which degrades agents, skills and MCP calls. claudectl automatically sets `CLAUDE_CODE_SUBAGENT_MODEL=claude-sonnet-5`, so delegated work always runs on a capable model even when the main session does not. That covers the common case; the main model's own capabilities remain whatever the free model has.

**Telemetry the provider rejects.** `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` is set automatically, because non-essential calls to the Anthropic API will fail in this configuration.

**Cost tracking splits.** Anthropic usage tracking does not see OmniRoute spend. Your usage dashboard is telling the truth about the account and nothing about the free tier.

What *does* work unchanged: per-project memory, hooks, MCP servers and skills. All of them are client-side and model-agnostic — they load from `CLAUDE_CONFIG_DIR` and the project's `.claude/` exactly as usual.

## When the model dies mid-session

A free model failing at launch is a bad model choice. A free model failing on turn forty is a different problem, and Claude Code cannot solve it: it sends each turn as a fresh request and, when one fails, retries **the same request against the same model** with backoff. Nothing ever tries a different model, because Claude Code has no such concept. A model deregistered upstream, or a tool schema the backing provider rejects, makes the session look frozen forever.

claudectl's failover proxy sits between `claude.exe` and the OmniRoute upstream. It forwards bytes verbatim, and when a turn errors **before any response body byte has reached the client**, it rewrites the request's `model` field and tries the next candidate. That "before any byte" condition is the whole correctness argument — once the client has seen part of a response you cannot retry without duplicating output.

The elegant part is that no per-turn machinery was needed: because every turn *is* its own request, request-level retry already is per-turn failover.

```
claudectl --failover-serve [port]   # foreground
claudectl --failover-stop           # terminate the daemon in the lock file
```

It runs as a detached child, so closing claudectl does not leave every live session with connection-refused, and it binds `127.0.0.1` only. The routing log runs in its own console window unless you hide it — deliberately, because the original complaint was never "a model died", it was "I could not see that a model died".

**One security note, because this is the kind of thing that gets built carelessly.** An early version of this proxy was a second HTTP server on a fixed, source-published port with no guard at all, substituting the user's OmniRoute key into everything it forwarded. A single CORS-simple `fetch()` from any open browser tab would have spent that quota. "It's loopback" is not an authorisation boundary and neither is a custom header — under DNS rebinding an attacker's page becomes same-origin with your local server and can send any header it likes. The layering that works, cheapest check first: a `Host` allowlist (this is the rebinding defence, because a rebound request carries the attacker's hostname), rejection of anything carrying browser fetch metadata (`Origin`, `Referer`, `Sec-Fetch-*` — Claude Code's HTTP client sends none of them, every browser sends at least one), and a per-run secret compared with `hmac.compare_digest`. If you build a local proxy that spends money, apply all three.

## Telling afterwards which sessions ran free

Worth knowing if you analyse your own transcripts. OmniRoute records the *resolved provider* model in `message.model` under a bare name — `big-pickle`, `deepseek-v4-flash-free`, `mimo-auto` — **not** the slash-namespaced id you selected at launch. So looking for a `/` finds nothing. The reliable signal is exclusion: a model is a free-tier run if its id is not a Claude or Anthropic one (no `claude`/`anthropic` substring, and not a bare alias like `sonnet`/`opus`/`haiku`). claudectl tags those sessions in the session list.

The unavoidable caveat: Anthropic served *through* OmniRoute is indistinguishable from a direct Anthropic run by id alone.

## When to use it

Use the split when the task is well-specified and long — a refactor across twenty files, a migration, a mechanical API change. The plan is where the judgement is, the execution is typing.

Do not use it when you do not yet know what you want. A plan produced from a vague prompt is a confident wrong plan, and handing it to a cheap model gets you a lot of wrong work quickly. For exploratory work, one good model and a conversation is still the right shape.

Reference: [docs.claudectl.space/plan-execute](https://docs.claudectl.space/plan-execute/) and [the failover section](https://docs.claudectl.space/statusline/).
