# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub's private vulnerability reporting on this repository:
[Report a vulnerability](https://github.com/babarmuhammad/claudectl/security/advisories/new).

You should get an acknowledgement within a few days. If a fix is needed, it ships in
the next release and the advisory is published alongside it with credit, unless you
ask otherwise.

## Supported versions

claudectl releases from `main` and only the latest version on PyPI is supported.
There are no maintenance branches.

## What is in scope

claudectl runs locally, on your own machine, against files Claude Code already writes.
The parts worth reporting are:

- **The local HTTP server** behind the desktop GUI (`claude_sessions/gui.py`,
  `gui_api.py`). It binds loopback and is guarded by a `Host` allowlist, a rejection of
  browser fetch metadata, and a per-run token. A way past any of those is a real finding
  — including anything reachable through DNS rebinding.
- **The failover proxy** (`claude_sessions/failover.py`), which substitutes your
  OmniRoute key into requests it forwards.
- **Credential handling.** claudectl reads Claude Code's own state and never writes
  `.credentials.json`. Anything that leaks a token, an API key or a session id into a
  log, an error message, a generated file or an outbound request is in scope.
- **Path handling.** Project paths, config directories and transcript names come from
  disk and from Claude Code. A traversal out of an account's config directory is in scope.
- **Anything claudectl writes to a file another program parses** — `settings.json`
  above all, which belongs to Claude Code, not to us.

## What is not in scope

- Claude Code itself, or the Claude API. Report those to Anthropic.
- Third-party agents and skills installed through claudectl's library — those are the
  upstream projects' code and claudectl shows you the source before writing it.
- Anything that requires an attacker who already has local user-level access to your
  machine. At that point they can read the same files claudectl reads.
- Models generating wrong or unsafe suggestions. claudectl shows you every generated hook,
  agent and CLAUDE.md block before it writes one, and that review is what you are relying
  on.
