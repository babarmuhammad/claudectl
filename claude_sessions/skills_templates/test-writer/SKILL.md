---
name: test-writer
description: Use when adding tests for new or changed code. Writes focused, behavior-driven tests that match the project's existing framework and conventions.
---

# Test writer

Add tests that pin down behavior and would fail if the code regressed.

## Steps

1. Detect the test framework and layout already in the repo (pytest, jest, go test, etc.) — match it exactly. Never introduce a new framework.
2. Identify the unit under test and its meaningful behaviors, edge cases, and error paths.
3. Write tests that:
   - name the behavior (`test_rejects_expired_token`), not the function;
   - use the Arrange-Act-Assert shape;
   - cover the happy path, boundaries, and at least one failure mode;
   - avoid asserting on incidental implementation details.
4. Reuse existing fixtures/helpers instead of duplicating setup.
5. Run the suite and confirm the new tests pass (and fail when the behavior is broken).

## Rules

- A test that can't fail is worthless — assert real outcomes.
- Keep each test independent and deterministic (no shared mutable state, no real network/clock unless the project already does).
- Prefer a few sharp tests over many shallow ones.

<!-- claudectl starter skill. Inspired by testing skills in
     alirezarezvani/claude-skills and khalilbenaz/claude-skills-collection. -->
