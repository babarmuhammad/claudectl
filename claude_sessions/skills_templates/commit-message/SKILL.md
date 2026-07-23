---
name: commit-message
description: Use when writing a git commit message. Turns staged changes into a clear Conventional Commits message with an accurate summary and body.
---

# Commit message writer

Write a commit message that explains **what changed and why**, from the staged diff.

## Steps

1. Inspect the staged changes (`git diff --staged`). If nothing is staged, say so and stop.
2. Pick a type: `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `build`, `ci`, or `chore`.
3. Write the subject: `type(scope): summary` — imperative mood, ≤ 72 chars, no trailing period.
4. Add a body only when the change isn't self-evident: what changed, and the reason/impact. Wrap at ~72 cols.
5. Note breaking changes with a `BREAKING CHANGE:` footer.

## Rules

- Describe intent, not the mechanical diff ("prevent double-submit", not "add if-check").
- One logical change per commit — if the diff spans unrelated concerns, suggest splitting.
- Never invent changes you can't see in the diff.

<!-- claudectl starter skill. Conventional Commits pattern (conventionalcommits.org);
     inspired by community commit skills in alirezarezvani/claude-skills. -->
