---
name: changelog
description: Use when updating a CHANGELOG or preparing release notes. Turns merged changes into Keep a Changelog entries written for users.
---

# Changelog writer

Maintain a human-readable changelog that tells users what changed.

## Steps

1. Read the existing `CHANGELOG.md` (follow its style) or start one in **Keep a Changelog** format.
2. Gather changes since the last release (`git log <last-tag>..HEAD`, merged PRs).
3. Group under an `## [Unreleased]` (or new version) heading using these sections, omitting empty ones:
   `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.
4. Write each entry from the **user's** point of view — the effect, not the commit text — one line each.
5. On release, rename `Unreleased` to `## [x.y.z] - YYYY-MM-DD` and bump per semver.

## Rules

- User-facing impact only; skip internal refactors and test-only changes unless they affect users.
- Link issues/PRs where it helps.
- Newest version on top; keep entries terse and parallel in phrasing.

<!-- claudectl starter skill. Follows Keep a Changelog (keepachangelog.com) +
     Semantic Versioning; inspired by release skills in the ecosystem. -->
