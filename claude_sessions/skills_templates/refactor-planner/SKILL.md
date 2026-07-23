---
name: refactor-planner
description: Use before a non-trivial refactor. Produces a safe, staged plan that preserves behavior — plan first, edit second.
---

# Refactor planner

Plan a refactor so behavior is preserved and the change is reviewable.

## Steps

1. State the **goal** and the **behavior contract that must not change** (public API, outputs, side effects).
2. Map what's affected: the target code, its callers, and its tests.
3. Confirm a **safety net** exists — the tests that cover current behavior. If coverage is thin, add characterization tests *before* refactoring.
4. Break the work into **small, independently verifiable steps**, each keeping the suite green.
5. List **risks & rollback**: what could break, how you'd notice, how to revert.
6. Present the plan and get agreement before touching code.

## Rules

- Refactor means "same behavior, better structure" — never mix in feature changes.
- Prefer many small commits over one large one.
- If a step can't be verified, it's too big — split it.
- Stop and re-plan if reality diverges from the plan.

<!-- claudectl starter skill. Mirrors claudectl's own Plan→Execute workflow;
     inspired by planning skills in obra/superpowers. -->
