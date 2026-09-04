---
description: >-
  Bulk-install the awesome-claude-code-subagents catalog into claudectl's agent library, or
  add a single agent, without bloating every Claude Code session.
---

# Installing the agent library

The **⚙ Agents** screen reads `~/.claude/claudectl-agents/<category>/*.md`. To bulk-install
the [awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents)
catalog (150+ agents, mirrored by category), run this PowerShell snippet once:

!!! info "Credit"

    The agent catalog is created and maintained by
    **[VoltAgent](https://github.com/VoltAgent)** —
    [awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents).
    claudectl only mirrors it into the library; all credit for the agents goes to the
    original authors. Please refer to that repository for its license and contribution
    terms.

```powershell
$repo = 'https://api.github.com/repos/VoltAgent/awesome-claude-code-subagents/contents/categories'
$raw  = 'https://raw.githubusercontent.com/VoltAgent/awesome-claude-code-subagents/main/categories'
$lib  = "$env:USERPROFILE\.claude\claudectl-agents"
foreach ($cat in (Invoke-RestMethod $repo | Where-Object { $_.type -eq 'dir' }).name) {
    $dir = Join-Path $lib $cat
    New-Item -ItemType Directory -Force $dir | Out-Null
    foreach ($f in (Invoke-RestMethod "$repo/$cat") | Where-Object { $_.name -like '*.md' -and $_.name -ne 'README.md' }) {
        Invoke-WebRequest "$raw/$cat/$($f.name)" -OutFile (Join-Path $dir $f.name)
    }
    Write-Host "$cat done"
}
```

Install a **single** agent directly into the library (e.g. into `09-meta-orchestration`):

```bash
curl -sL https://raw.githubusercontent.com/VoltAgent/awesome-claude-code-subagents/main/categories/09-meta-orchestration/agent-installer.md \
  -o "$USERPROFILE/.claude/claudectl-agents/09-meta-orchestration/agent-installer.md"
```

These land in the library (not `~/.claude/agents/`), so they don't bloat every Claude
session — claudectl copies only the ones you select for a project into that project's
`.claude/agents/` (`g` in the sessions menu). See [Agents & skills](agents.md).

Third-party bundles are statically risk-scanned before install.
