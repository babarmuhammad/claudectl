---
name: code-explainer
description: Use when asked to explain how code works, trace a flow, or onboard to an unfamiliar area of the codebase.
---

# Code explainer

Explain code so a competent engineer new to it can build a correct mental model.

## Steps

1. Start with the **purpose**: what this code is for, in one sentence.
2. Trace the **main path**: inputs → key steps → outputs, naming the functions/files (`path:line`) involved.
3. Surface the **non-obvious**: invariants, side effects, error handling, concurrency, and any surprising decisions or gotchas.
4. Note **how it connects** to the rest of the system (callers, data it touches).
5. End with anything that looks risky, dead, or worth revisiting — but keep speculation labeled.

## Rules

- Explain the *why*, not just the *what*; restating the code line-by-line adds no value.
- Use the code's real names and cite locations so claims are checkable.
- Say "I'm not sure" rather than guessing at behavior you can't see.
- Match depth to the question — a one-liner deserves a one-paragraph answer.

<!-- claudectl starter skill. Inspired by explain/onboarding skills across the
     Claude Code skills ecosystem (claudemarketplaces.com). -->
