---
title: Model providers — local models, OpenRouter, self-hosted
description: >-
  Run claudectl sessions against a local Ollama, llama.cpp or vLLM server, OpenRouter,
  OmniRoute or a self-hosted box — what keeps working, what degrades, and what cannot work
  at all.
---

# Model providers

⚙ Settings → **Model provider**.

Point a session at something other than your Anthropic account. It stays a **real, full
`claude` session** with this project's usual agents, skills, hooks, MCP servers, slash
commands, output styles, CLAUDE.md and `/rewind` checkpoints — only the model endpoint
changes.

That is not a trick. Everything in the list above is client-side orchestration that never
touches the model API, so it survives a backend swap untouched, and Anthropic publishes the
wire contract a backend has to meet at
[code.claude.com/docs/en/llm-gateway-protocol](https://code.claude.com/docs/en/llm-gateway-protocol).

## The one requirement

**The backend must speak `POST /v1/messages`** — the Anthropic Messages API — and it must
stream. A gateway that buffers a whole response before relaying it stalls the client.

Backends that do, today:

| Backend | Notes |
|---|---|
| **Ollama** ≥ 0.14 | Native `/v1/messages`. No prompt caching and **no `tool_choice`**, which can make Claude Code pick the wrong tool and loop. Raise `num_ctx` — see below. |
| **vLLM** | Native `/v1/messages` and `count_tokens`. Does not return reasoning tokens yet. |
| **llama.cpp** (`llama-server`) | Native `/v1/messages`. Needs `--jinja` for tool use. |
| **OpenRouter** | Its Anthropic-format endpoint. Tool use and caching vary by model. |
| **OmniRoute** | See [Plan → Execute & OmniRoute](plan-execute.md). |
| A remote/self-hosted box | Any of the above over the network. Use a token. |

## OpenAI-shaped backends

An endpoint that only serves `/v1/chat/completions` — LM Studio, most bare local servers,
anything "OpenAI-compatible" — is reachable through claudectl's own **translating gateway**.
Turn it on in the same settings card: set the gateway target to the OpenAI-shaped URL and
claudectl runs a loopback proxy that speaks Anthropic Messages to `claude` and OpenAI Chat
Completions upstream.

What the translation cannot carry, and does not pretend to:

- **`cache_control`** has no equivalent. It is dropped and logged once per run, so you find
  out from a line in the proxy window rather than from a cost change you cannot explain.
- **Thinking blocks** are dropped from request history unconditionally — including on a
  session you resume after switching backends, which is the case that otherwise produces
  `Invalid signature in thinking block` long after the swap and looks unrelated to it.
- **`count_tokens`** is answered with the same `chars//4` estimate claudectl uses elsewhere.
  There is no dependency-free exact counter that works across providers.

Two operational notes that are not optional:

- **On vLLM, tool calling needs `--enable-auto-tool-choice --tool-call-parser <parser>`
  server-side.** "OpenAI-compatible" guarantees the envelope, never the tool behaviour, and
  without those flags the model answers in prose where a tool call was required.
- Claude Code aborts a stream that goes quiet for ~90s. The gateway sends keepalive pings
  for the **whole** stream, not just until the first token, because a local model doing
  grammar-constrained tool-call decoding goes silent in the middle of a turn.

## Setup

1. ⚙ Settings → **Model provider** → **Backend**:
   - **Anthropic (direct)** — the default. Nothing is overridden.
   - **Anthropic-shaped server** — a server you already run.
   - **OmniRoute** — claudectl starts the daemon for you.
2. **Base URL** — e.g. `http://localhost:11434` for Ollama.
3. **API key** — whatever that backend wants, or blank. Write-only: it is never sent back
   to the browser.
4. **Context window** — optional, and **not** probed: most `/v1/models` responses omit it,
   and a fabricated number is worse than none. Filling it in turns on a pre-launch warning.
5. Pick the model at launch. On OmniRoute you get a live menu; on a generic server there is
   no catalogue to read, so you type the model id.

claudectl checks the backend is reachable **before** the session opens. A launch that
succeeds and only dies once `claude` tries the model leaves you in a new console with no
path back to the setting that was wrong.

## claudectl's own calls

Separately from your sessions, claudectl makes its own headless `claude -p` calls — memory
extraction, lesson distillation, code review, and the CLAUDE.md / agent / skill / hook /
system-prompt generators. Those keep running on **Anthropic** even when this card points
everywhere else, because they run unattended (from a hook, from background threads) and quietly
moving them to another model — and another bill — is a surprise rather than a feature.

**Run claudectl's own calls here too** switches them over. They then ask for the model id above
rather than the economy model, because that is the only id the backend can resolve. If the backend
is unreachable the call fails and says so; it does not fall back to the account you routed away
from.

These are the cheapest calls claudectl makes and the best fit for a local model: short prompts,
no tool use, structured output. Extraction quality does drop on a small model — the memory graph
is only as good as what read the transcript.

## What does not survive the swap

Stated plainly, because a feature page that only lists what works is not useful. None of
these are claudectl bugs — they are properties of Claude Code's client or of Anthropic's
own infrastructure.

| | What happens | What claudectl does |
|---|---|---|
| **Subagents** | The Task tool's model parameter is a hard-coded `sonnet`/`opus`/`haiku` enum, and built-in agents carry literal model ids that 401 through any non-Anthropic endpoint. | Strips the `model:` field from the agent files it syncs, so your project's agents inherit the session's model. Claude Code's **built-in** agents cannot be fixed from outside. |
| **Prompt caching** | Absent on Ollama entirely; varies elsewhere. Every turn bills as uncached. | Nothing to do — `cache_control` is emitted by `claude` itself. |
| **Extended thinking** | A thinking block carries a signature that must round-trip byte-for-byte to the infrastructure that minted it. A backend that did not mint it cannot produce one, and Claude Code sends the field unconditionally, so an upstream that does not know it answers 400 — failing the whole turn, not just the thinking. | Sets `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` automatically. |
| **`web_search`** | Runs on Anthropic's own servers. Cannot work through a third party at all. | Use an MCP search server instead. |
| **MCP tool search** | Claude Code disables it on any non-first-party base URL. | Opt-in toggle — re-enabling only works if your upstream forwards `tool_reference` blocks. |
| **Cost tracking** | There are no published rates for a routed model. | Shows `n/a` rather than `$0.00` — a routed model is not necessarily free. |
| **Remote Control** | Disabled by Claude Code on a non-first-party base URL. | — |

## Local models: the part that is about the model, not the harness

Claude Code's own system prompt is **10k+ tokens before any of your project context**.
Ollama's default context is 4096. That is already over budget on an empty repo, which is
why the pre-launch warning counts the system prompt rather than just weighing your
CLAUDE.md.

- Raise the context window (`num_ctx` on Ollama). This matters more than model size.
- Only a few model families tool-call reliably enough for agentic work — Qwen3-Coder 30B+,
  GLM-4.7, Llama 3.3 70B, Devstral Small 2, gpt-oss. Below ~7B, or on a model that was
  never tool-tuned, malformed tool calls are the norm regardless of harness.
- **Q4_K_M is the floor for tool calling.** Q3 and below breaks the tool-call format before
  it visibly hurts chat quality, which makes it look like a claudectl bug when it is not.
- On vLLM, tool calling needs `--enable-auto-tool-choice --tool-call-parser` server-side.
  "OpenAI-compatible" guarantees the envelope, never the tool behaviour.

## A note on what this is

Anthropic documents the gateway protocol but states it **does not support** routing Claude
Code to non-Claude models through one. Nothing in the client enforces that; treat this
feature as unsupported by Anthropic, not endorsed by them.

Separately, and this one is not ambiguous: **claudectl never sends your Claude subscription
credentials anywhere but Anthropic.** The provider key is one you supply for this purpose,
stored in claudectl's own settings. Reusing Claude Code subscription OAuth tokens in another
tool is a Terms of Service violation that Anthropic has actively enforced against other
projects — claudectl does not do it, and will not gain a setting that does.
