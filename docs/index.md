---
title: claudectl documentation
description: >-
  The claudectl manual — installing it, the terminal UI, the desktop app, the Claude Code
  plugin, configuration, project memory, the architecture graph and what a turn costs.
jsonld: |
  {
    "@context": "https://schema.org",
    "@type": "TechArticle",
    "name": "claudectl documentation",
    "description": "The complete manual for claudectl, the workspace layer for Claude Code.",
    "url": "https://docs.claudectl.space/",
    "author": {"@type": "Person", "name": "Babar Muhammad Anas"},
    "about": {
      "@type": "SoftwareApplication",
      "@id": "https://claudectl.space/#software",
      "name": "claudectl",
      "alternateName": ["claudectl (Python)", "claudectl for Claude Code"],
      "identifier": "claudectl",
      "sameAs": [
        "https://github.com/babarmuhammad/claudectl",
        "https://pypi.org/project/claudectl/",
        "https://docs.claudectl.space/"
      ],
      "applicationCategory": "DeveloperApplication",
      "operatingSystem": "Windows, macOS, Linux",
      "softwareVersion": "1.9.0",
      "url": "https://claudectl.space/",
      "codeRepository": "https://github.com/babarmuhammad/claudectl",
      "programmingLanguage": "Python",
      "license": "https://opensource.org/licenses/MIT",
      "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}
    }
  }
---

# claudectl documentation

The manual for **claudectl**, the workspace layer for Claude Code. Everything it does, how
to configure it, and what each part costs you per turn.

[Getting started](getting-started.md){ .md-button .md-button--primary }
[Quickstart](quickstart.md){ .md-button }

New here? [Getting started](getting-started.md) explains what claudectl is and which of its
three surfaces you want. In a hurry? [Quickstart](quickstart.md) is install to first
session in five minutes.

!!! info "Looking for the Rust `claudectl`?"

    Two independent open-source projects use this name. **This** one is the Python
    workspace layer for Claude Code — `pipx install claudectl`, source at
    [github.com/babarmuhammad/claudectl](https://github.com/babarmuhammad/claudectl),
    published on [PyPI](https://pypi.org/project/claudectl/). The other is a Rust agent
    orchestrator by a different author, published on crates.io. They are unrelated, and
    neither is affiliated with Anthropic.

## Install & first run

| | |
|---|---|
| [Getting started](getting-started.md) | what it is, the three surfaces, where to go next |
| [Installation](installation.md) | pipx, pip, a checkout, the desktop window, Windows shortcuts |
| [Quickstart](quickstart.md) | the five-minute path from nothing to a first session |

## The interfaces

| | |
|---|---|
| [Command line](cli.md) | every command — `workspace status`, `recall`, `review`, `sync-accounts`, `statusline` |
| [Terminal UI](tui.md) | every screen, the loops, and the complete key map |
| [Desktop app](desktop.md) | the same workspace as a local app — 32 palettes, 8 skins, 4 worlds |
| [Claude Code plugin](plugin.md) | three slash commands and eight skills inside the session |

## Working with projects

| | |
|---|---|
| [Configuration](configuration.md) | every file claudectl reads and writes, and where |
| [Projects](projects.md) | health checks, auto-fixes, and whether generated context still matches the repo |
| [Sessions](sessions.md) | browse, search, tag, fork, resume, archive, export |
| [Project memory](memory.md) | the memory graph and its three token-budgeted injection surfaces |
| [Architecture graph](architecture.md) | the interactive dependency view, repo down to single files |
| [Usage & cost](usage.md) | what a turn costs across every surface, and how to cut it |
| [Troubleshooting](troubleshooting.md) | when something does not work |

## Reference

| | |
|---|---|
| [API reference](api.md) | the local HTTP API the desktop app is built on, generated from the route tables |
| [Download](https://claudectl.space/download/) | every way to get it, what a release contains, versioning |
| [Multiple accounts](accounts.md) · [Context hand-off](context-handoff.md) | more than one Claude account, and moving a session between them |
| [MCP servers](mcp.md) · [Agents & skills](agents.md) · [Hooks](hooks.md) | Claude Code integration |
| [Plan → Execute](plan-execute.md) · [Status line & failover](statusline.md) | model routing and what runs per turn |
| [Files, layout & encoding](reference.md) | CLAUDE.md generation and how project paths are encoded |
| [Project dashboard](dashboard.md) | release version, downloads, stars, test count, commit activity |

## Terminal or desktop, same engine

<div class="grid" markdown>

![Terminal UI](img/tui-main.png)

![Desktop app](img/gui-sessions.png)

</div>

Looking for the product pitch, the feature tour or the comparison instead?
That is on [claudectl.space](https://claudectl.space/).
