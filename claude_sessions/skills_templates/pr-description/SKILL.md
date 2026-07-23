---
name: pr-description
description: Use when opening or updating a pull request. Produces a reviewer-friendly PR title and description from the branch's changes.
---

# Pull-request description writer

Summarize a branch's changes so a reviewer understands them fast.

## Steps

1. Compare the branch to its base (`git diff <base>...HEAD`, `git log <base>..HEAD`).
2. Write a title: `type: concise outcome` (imperative, ≤ 70 chars).
3. Fill this structure:

   ```
   ## What
   <1-3 sentences: the change and its purpose>

   ## Why
   <the problem / motivation, link issues if known>

   ## How
   <key implementation decisions a reviewer should notice>

   ## Testing
   <what you ran / added; how to verify>

   ## Risk
   <blast radius, migrations, rollback — or "low, isolated">
   ```

## Rules

- Lead with impact, not a file-by-file list.
- Call out anything needing reviewer attention (schema changes, config, security).
- Omit a section only when it's genuinely empty; never pad.

<!-- claudectl starter skill. Inspired by PR/changelog skills across
     ComposioHQ/awesome-claude-skills and the anthropics code-review plugin. -->
