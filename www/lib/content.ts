/**
 * The apex pages, as data.
 *
 * The pages render this and /llms-full.txt is generated from it, so the plain-text
 * surface an LLM reads can never drift from the HTML a human reads. Adding a page
 * means adding a Doc here; nothing else has to be kept in sync by hand.
 */

export type Block =
  | { kind: 'p'; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'dl'; items: { t: string; d: string }[] }
  | { kind: 'code'; text: string; label?: string }
  | { kind: 'table'; head: string[]; rows: string[][] };

export type Section = {
  id: string;
  eyebrow?: string;
  heading: string;
  lead?: string;
  blocks: Block[];
};

export type Doc = {
  slug: string;
  title: string;
  /** <h1>; falls back to title */
  h1?: string;
  description: string;
  intro?: string;
  sections: Section[];
};

const p = (text: string): Block => ({ kind: 'p', text });
const ul = (items: string[]): Block => ({ kind: 'ul', items });
const dl = (items: { t: string; d: string }[]): Block => ({ kind: 'dl', items });
const code = (text: string, label?: string): Block => ({ kind: 'code', text, label });
const table = (head: string[], rows: string[][]): Block => ({ kind: 'table', head, rows });

/* ── / ─────────────────────────────────────────────────────────────────────── */

export const HOME: Doc = {
  slug: '',
  title: 'claudectl — the workspace layer for Claude Code',
  h1: 'The workspace layer for Claude Code',
  description:
    'claudectl gives Claude Code persistent per-project memory, a searchable session archive, an interactive architecture graph, MCP management and per-project launch control. Pure Python standard library, zero runtime dependencies, MIT.',
  intro:
    'Your projects stop being a stream of chats and start being workspaces — with memory, history and per-project launch control.',
  sections: [
    {
      id: 'hero',
      eyebrow: 'Station 01',
      heading: 'The workspace layer for Claude Code',
      lead:
        'Persistent project memory, every session you have ever had, and control over how the next one starts. A terminal UI and a desktop GUI over the same engine.',
      blocks: [
        code('pipx install claudectl', 'install'),
        ul([
          'Python 3.10 or newer, and the Claude Code CLI you already have.',
          'No API key — it uses your existing Claude Code authentication.',
          'Zero runtime dependencies: pure Python standard library.',
          'Windows, macOS and Linux, tested in CI on all three.',
        ]),
      ],
    },
    {
      id: 'problem',
      eyebrow: 'Station 02',
      heading: 'Excellent inside a session. Forgetful between them.',
      lead:
        'Every new session starts from nothing, old sessions are hard to find, and the only way to give the agent context is a CLAUDE.md that grows until it costs more than it is worth.',
      blocks: [
        p(
          'Claude Code stores each session as a JSONL transcript under your config directory and gives you no way to browse them. /resume reattaches you to something recent in the current directory; it does not search, does not span projects or accounts, and cannot answer "what did I do in this repo three weeks ago, and what did we decide".',
        ),
        p(
          'The context problem is worse, because it is recurring. CLAUDE.md rides in the model context on every single message, so its size is a permanent per-turn tax. The usual failure is not that the file is wrong — it is that it is too big to justify and too tedious to prune. A big project either starves the agent or floods it.',
        ),
        p(
          'claudectl sits in front of Claude Code and fixes both. Pick a project, see every session you have ever had in it, and launch with the model, effort, permissions and context you meant.',
        ),
      ],
    },
    {
      id: 'memory',
      eyebrow: 'Station 03',
      heading: 'Intelligent memory, not a memory dump',
      lead:
        'A semantic graph of your codebase, injected through three token-budgeted surfaces. The always-on cost is a bounded index that does not grow as the codebase does.',
      blocks: [
        table(
          ['Surface', 'What Claude sees', 'Cost'],
          [
            ['CLAUDE.md micro-index', 'repo one-liners, module names, a recall pointer', '≤250 tokens, every session'],
            ['.claude/rules/ per module', 'entities and relations, path-scoped', '0 until Claude touches those files'],
            ['UserPromptSubmit hook (opt-in)', 'the subgraph relevant to the prompt you just typed', '≤600 tokens per prompt, under a second, local'],
          ],
        ),
        dl([
          {
            t: 'Bounded and self-consolidating',
            d: 'Duplicate entities merge across modules and a global importance cap evicts the least-connected, so the always-on cost stays flat while accuracy rises. The memory gets leaner and sharper the more you build, not heavier.',
          },
          {
            t: 'Nothing shrinks without a way back',
            d: 'Pin an entity and the cap can never evict it. Fence a region of CLAUDE.md and AI compression never even sends it to the model. Prune names what it will drop before it drops it. Every replacement is snapshotted and restorable, and restoring is itself snapshotted.',
          },
          {
            t: 'Temporal facts',
            d: 'When you migrate Flask to FastAPI the old fact is invalidated with a timestamp rather than deleted — kept as history, never injected. Memory tracks what is true now and what changed, instead of drifting stale.',
          },
          {
            t: 'It learns from every session',
            d: 'Durable lessons — error to fix pairs, decisions, preferences — are distilled from transcripts, reviewed, injected when relevant and decayed when unused. Conventions that recur across your repos are promoted to your user-level CLAUDE.md, so a rule learned once is remembered everywhere.',
          },
          {
            t: 'Local, deterministic retrieval',
            d: 'Four rankers combined by Reciprocal Rank Fusion, which uses each ranker’s position rather than its score, so nothing needs calibrating. No embeddings, under half a second on 500 entities. The honest limit: this is lexical retrieval — a query sharing no vocabulary with the stored summary will miss.',
          },
        ]),
        p(
          'Graph memory is inspired by cognee and retrieval budgeting by Aider’s repo-map; both were reimplemented from scratch in pure stdlib.',
        ),
      ],
    },
    {
      id: 'workspace',
      eyebrow: 'Station 04',
      heading: 'A workspace, not a pile of chats',
      lead:
        'Every project Claude Code has ever opened, every session inside it, sorted by recency and searchable across the lot — across every account at once.',
      blocks: [
        dl([
          {
            t: 'Sessions',
            d: 'Browse, search, tag, fork, rename, resume, archive and export. Read any transcript in a pager with full-text search inside the conversation, and list the files a session actually changed, derived from its tool calls.',
          },
          {
            t: 'Usage',
            d: 'Tokens in, out and cached, plus estimated cost per project, per session, per day and per model — parsed from your own local transcripts, not from an API. Daily limit bars with reset times on the main screen.',
          },
          {
            t: 'Multiple accounts',
            d: 'Claude Code picks its account through CLAUDE_CONFIG_DIR. claudectl detects every configured account, merges their projects into one list, shows per-account usage side by side, and sets the variable for you at launch.',
          },
          {
            t: 'Launch control',
            d: 'Model, reasoning effort, permission mode, agent, extra PATH entries and additional context roots — chosen before each launch and remembered per project. Start a named session, or one in a fresh git worktree.',
          },
          {
            t: 'Context hand-off',
            d: 'Hand off any session to a new one, under any account, from the button on its row — for when the context window fills up, or an account hits its limit mid-task. The new chat starts with the old transcript on disk and is told to read it first.',
          },
        ]),
      ],
    },
    {
      id: 'tokens',
      eyebrow: 'Station 05',
      heading: 'Where the tokens go',
      lead:
        'CLAUDE.md and memory files ride in the context on every message. claudectl makes that cost visible, then cuts it.',
      blocks: [
        dl([
          {
            t: 'Context weight audit',
            d: 'One screen estimating the tokens auto-loaded on every turn for this project: CLAUDE.md broken into its blocks, the global CLAUDE.md, rules files marked lazy when they are glob-scoped, the system prompt, SessionStart hook injections and MCP servers — with a running always-on total and inline warnings.',
          },
          {
            t: 'Prune and compress',
            d: 'The session-topics log is capped to the most recent entries and rebuilt in place. AI compression rewrites the hand-written part into a lean lookup-table style, shows a before-and-after token count and a diff to approve, and preserves the machine-maintained blocks verbatim.',
          },
          {
            t: 'Deny heavy reads',
            d: 'Scans the project and writes permissions.deny rules for node_modules, dist and lockfiles into the project settings, so a stray read cannot pull thousands of tokens of generated content into context.',
          },
          {
            t: 'Cheaper models for the grunt work',
            d: 'claudectl’s own internal calls default to a cheap economy model. Plan → Execute runs the expensive model once for the plan and a cheap — or, through OmniRoute, a free — one for execution.',
          },
          {
            t: 'Measured, not asserted',
            d: 'Rule files were being loaded on every turn because they carried Cursor’s frontmatter key rather than Claude Code’s. Fixing it took this repository from 22,238 always-on tokens to 18,363 — 3,875 off every single turn.',
          },
        ]),
      ],
    },
    {
      id: 'get',
      eyebrow: 'Station 06',
      heading: 'Get claudectl',
      lead: 'Four ways in. All of them are free, MIT licensed and take one line.',
      blocks: [
        code('pipx install claudectl\nclaudectl', 'pipx — recommended'),
        code('pip install claudectl', 'pip'),
        code('/plugin marketplace add babarmuhammad/claudectl\n/plugin install claudectl@claudectl', 'Claude Code plugin'),
        p(
          'Or clone the repository and run python claude-sessions.py. There is nothing to build and nothing to install alongside it.',
        ),
      ],
    },
  ],
};

/* ── /features ─────────────────────────────────────────────────────────────── */

export const FEATURES: Doc = {
  slug: 'features',
  title: 'Features',
  description:
    'Everything claudectl does — sessions and search, project memory, the architecture graph, Plan to Execute, multiple accounts, MCP servers, agents, hooks, the status line and the desktop GUI.',
  intro:
    'Everything claudectl does, grouped. It is one engine behind two interfaces: a keyboard-first terminal UI and a desktop GUI with full parity.',
  sections: [
    {
      id: 'sessions',
      heading: 'Sessions & search',
      lead:
        'Claude Code’s flat pile of chats, turned into a browsable workspace.',
      blocks: [
        ul([
          'Session browser — every project Claude Code has ever opened and every session inside it, sorted by recency.',
          'Search — filter live as you type, or search the content of every session across every project and resume the hit.',
          'Transcript viewer and export — read any session in a pager with in-conversation search and a message-position counter; export to markdown.',
          'Rename, fork, continue, tag and archive, with archive being a restorable folder rather than a delete.',
          'Changed files — the files a session created or edited, derived from its tool calls.',
          'Session info — tokens, estimated cost, models used, git branch and duration.',
          'Quick-resume shortcuts on the main screen jump straight back into recent sessions across all projects.',
        ]),
      ],
    },
    {
      id: 'memory',
      heading: 'Project memory',
      lead:
        'The feature nothing else does: task-scoped, token-budgeted memory injection at the launcher.',
      blocks: [
        ul([
          'A semantic graph of the codebase built by summarising every repo and module incrementally, merged with the real dependency graph from the connections engine.',
          'Three injection surfaces with zero duplication: a ≤250-token always-on index, path-scoped rules files that cost nothing until Claude opens a matching file, and an optional per-prompt hook budgeted to ≤600 tokens.',
          'Auto-refresh that converges — each cycle extracts at most a configured number of modules and queues the rest; work is never marked done unless it was done.',
          'Lessons distilled from every session, human-reviewed, injected when relevant, decayed when stale.',
          'Cross-project conventions promoted into your user-level CLAUDE.md.',
          'Ask the project — grounded question answering over the graph, with only the relevant subgraph as context.',
          'Recent-work memory — a token-free one-line summary per session, injected as a digest at the next SessionStart.',
        ]),
      ],
    },
    {
      id: 'graph',
      heading: 'Architecture graph',
      lead:
        'A self-contained interactive HTML view of your project’s real dependency structure.',
      blocks: [
        p(
          'It opens at the repository level and expands down to single files, across Python, C, C++, C#, JavaScript and TypeScript. Nothing is uploaded and nothing is served — it is one HTML file written into the project, opened straight from the session browser.',
        ),
      ],
    },
    {
      id: 'plan-execute',
      heading: 'Plan → Execute',
      lead: 'Plan on an accurate model, execute on a cheap or free one.',
      blocks: [
        p(
          'A headless planning pass runs on the strong model. You approve or edit the plan, and the approved plan is handed to a cheaper model to carry out. Free execution routes the execute half through OmniRoute’s free tier while planning stays on your real account.',
        ),
      ],
    },
    {
      id: 'integration',
      heading: 'Claude Code integration',
      blocks: [
        dl([
          {
            t: 'Multiple accounts',
            d: 'Two or more accounts side by side, one row per project, cross-account hand-off and account-accurate memory. Session lists — live and archived — span every account, and an action puts a session back in the one it came from.',
          },
          {
            t: 'MCP servers',
            d: 'Add, remove and inspect servers, see which are actually connected to a project, and run an analysis that documents a server’s real tools into a re-updatable block in your CLAUDE.md.',
          },
          {
            t: 'Agents & skills',
            d: 'A category-organised agent library mirroring a 154-agent community catalog, per-project subagent selection, adaptive suggestions from local signals, and a skills manager.',
          },
          {
            t: 'Hooks',
            d: 'Nineteen ready-made Claude Code hook templates — formatting, safety guardrails, audit, context injection, token savers — plus AI-generated ones. Installing, disabling and removing all apply across every account.',
          },
          {
            t: 'Status line, failover & checkpoints',
            d: 'A cheap per-turn status line, a proxy that retries a dead model instead of hanging, and a strictly read-only view of Claude Code’s checkpoint store.',
          },
        ]),
      ],
    },
    {
      id: 'health',
      heading: 'Project health & auto-fixes',
      blocks: [
        ul([
          'Pre-launch health checks.',
          'Context-loss insurance after /compact — memory lives outside the transcript, so it is re-injected at the next launch regardless of what the conversation dropped.',
          'A permission-fatigue killer and a token-burn advisor.',
          'Daily usage tracking with an optional threshold badge.',
          'Code review of your working diff against your CLAUDE.md rules and learned lessons, reporting confidence-scored findings.',
        ]),
      ],
    },
    {
      id: 'gui',
      heading: 'The desktop GUI',
      lead: 'The whole thing as a desktop app, with full parity to the terminal UI.',
      blocks: [
        p(
          'A native window when PyQt6 is present, otherwise your browser, served over loopback only. 29 palettes, 7 skins and 4 themed worlds — a skin changes the shape of the app, not just its colours.',
        ),
      ],
    },
    {
      id: 'compared',
      heading: 'Compared with the alternatives',
      blocks: [
        table(
          ['', 'Bare Claude Code', 'Hand-maintained CLAUDE.md', 'claudectl'],
          [
            ['Browse past sessions', '/resume, current directory, recent only', 'No', 'Every session, project and account'],
            ['Search session content', 'No', 'No', 'Yes'],
            ['Tag / fork / archive', 'No', 'No', 'Yes'],
            ['Project context', 'You write it', 'You write and prune it', 'Maintained automatically, budgeted'],
            ['Cost as the project grows', 'Grows with your file', 'Grows with your file', 'Bounded index plus on-demand detail'],
            ['Multiple accounts', 'Set CLAUDE_CONFIG_DIR yourself', '—', 'Detected, merged, picked at launch'],
            ['Dependency graph', 'No', 'No', 'Interactive graph'],
            ['Extra runtime dependencies', '—', '—', 'None, stdlib only'],
          ],
        ),
      ],
    },
    {
      id: 'not',
      heading: 'What claudectl does not do',
      lead: 'Stated plainly, because a feature page that only lists strengths is not useful.',
      blocks: [
        ul([
          'It is not a Claude Code replacement. It configures and launches Claude Code; every actual coding turn is Claude Code doing the work.',
          'It is Windows-first. macOS and Linux are supported and tested in CI, but Windows gets the widest version matrix and by far the most real-world use.',
          'It does not host or proxy a model of its own. It uses your existing authentication and your existing quota.',
          'The memory features cost tokens to build. Extraction and lesson distillation are Claude calls, routed to a cheap model and run rarely, but not free. The saving is on the per-message context you stop paying for.',
          'It is a young project. Small user base, and the API surface still moves.',
          'If your CLAUDE.md is 40 lines and stays that way, you do not need this.',
        ]),
      ],
    },
  ],
};

/* ── /download ─────────────────────────────────────────────────────────────── */

export const DOWNLOAD: Doc = {
  slug: 'download',
  title: 'Download claudectl',
  description:
    'Every way to install claudectl — pipx, pip, a GitHub release, a git checkout or the Claude Code plugin. Python 3.10+, zero runtime dependencies, MIT licensed.',
  intro:
    'claudectl is a pure-Python package with no runtime dependencies. Any of these takes one line, and none of them needs an API key — it uses the Claude Code authentication you already have.',
  sections: [
    {
      id: 'pipx',
      heading: 'pipx — recommended',
      lead: 'Installs into its own environment and puts one command on your PATH.',
      blocks: [
        code('pipx install claudectl\nclaudectl'),
        p('Upgrade later with pipx upgrade claudectl, or from the Updates screen inside claudectl.'),
      ],
    },
    {
      id: 'pip',
      heading: 'pip',
      blocks: [
        code('pip install claudectl\nclaudectl'),
        p(
          'Use a virtual environment if you would rather not install into your system Python. The package installs one console script, claudectl.',
        ),
      ],
    },
    {
      id: 'plugin',
      heading: 'Claude Code plugin',
      lead: 'If you would rather stay inside the session.',
      blocks: [
        code('/plugin marketplace add babarmuhammad/claudectl\n/plugin install claudectl@claudectl'),
        p(
          'The plugin ships commands and skills. It deliberately ships no hooks: claudectl’s own hook manager already places the recall, worklog and guard hooks per account, and giving one settings.json entry two owners means installing both runs the same hook twice.',
        ),
      ],
    },
    {
      id: 'checkout',
      heading: 'From a git checkout',
      blocks: [
        code(
          'git clone https://github.com/babarmuhammad/claudectl.git\ncd claudectl\npython claude-sessions.py          # terminal UI\npython claude-sessions.py --gui    # desktop GUI',
        ),
        p(
          'There is nothing to build. A checkout is told to git pull when it updates itself, rather than having a release installed over it.',
        ),
      ],
    },
    {
      id: 'release',
      heading: 'What a release contains',
      blocks: [
        ul([
          'The claudectl package: the terminal UI, the desktop GUI, the memory engine, the connections engine and the hook, agent and skill managers.',
          'Bundled starter skills and 19 hook templates.',
          'The Claude Code plugin manifest, with commands and skills generated from the package so they cannot fall behind it.',
          'A signed source distribution and a wheel on PyPI. Versioning is semantic; the changelog follows Keep a Changelog.',
        ]),
      ],
    },
    {
      id: 'requirements',
      heading: 'Requirements',
      blocks: [
        table(
          ['', ''],
          [
            ['Python', '3.10 or newer'],
            ['Claude Code', 'The CLI, auto-detected on PATH or at ~/.local/bin/'],
            ['Runtime dependencies', 'None — Python standard library only'],
            ['Optional', 'PyQt6, for the native desktop window. Without it the GUI opens in your browser over loopback.'],
            ['Platforms', 'Windows, macOS and Linux; CI tests all three'],
            ['Licence', 'MIT'],
          ],
        ),
      ],
    },
  ],
};

/* ── /architecture ─────────────────────────────────────────────────────────── */

export const ARCHITECTURE: Doc = {
  slug: 'architecture',
  title: 'Architecture',
  description:
    'How claudectl is built — the connections engine, the memory graph, the job runner, the local HTTP API behind the GUI, and the interactive dependency graph it draws of your own project.',
  intro:
    'claudectl is one engine with two front ends. Everything below runs locally, on the standard library, against files Claude Code already writes.',
  sections: [
    {
      id: 'graph',
      heading: 'The architecture graph',
      lead:
        'Every module and its dependencies, expandable down to single files — across Python, C, C++, C# and JavaScript or TypeScript.',
      blocks: [
        p(
          'The connections engine parses your source, resolves imports into real cross-module edges and ranks each node by importance. The result is written as a single self-contained HTML file inside the project and opened from the session browser. Nothing is uploaded, and nothing is served.',
        ),
        p(
          'The same graph is what the memory layer merges its summaries into, which is why the memory of a module knows what that module depends on.',
        ),
      ],
    },
    {
      id: 'layers',
      heading: 'The layers',
      blocks: [
        dl([
          {
            t: 'Store and transcripts',
            d: 'One reader per file format, and the reader streams. Transcripts reach a hundred megabytes, so they are iterated rather than read, with an optional substring prefilter tested against the raw line so a caller that wants only the tool calls never pays for a parse it discards.',
          },
          {
            t: 'Memory',
            d: 'A semantic graph in the project, consolidated under an importance cap, with superseded facts invalidated rather than deleted. An absent file is an empty state; a file that exists and will not parse is a fault, moved aside and reported rather than silently returned as empty.',
          },
          {
            t: 'Jobs',
            d: 'Long Claude calls run inline as a banner that survives navigation, and escalate to a modal only when the job actually parks at an approval gate — decided by what the job did, not by a per-kind allowlist that would go stale.',
          },
          {
            t: 'The local HTTP API',
            d: 'The GUI is a single-page app over a loopback server. A custom request header is not an auth boundary and neither is loopback, so the guard is a Host allowlist, a rejection of browser fetch metadata, and a per-run secret compared in constant time.',
          },
          {
            t: 'Claude Code’s own state',
            d: 'settings.json and the plugin caches are read-modify-written atomically, never rewritten. claudectl shares those files with Claude Code, so a half-written one breaks your session, not just this tool.',
          },
        ]),
      ],
    },
    {
      id: 'principles',
      heading: 'Principles the code is held to',
      blocks: [
        ul([
          'Zero runtime dependencies. Everything is the Python standard library, so there is nothing to resolve, pin or break.',
          'Read Claude Code’s state, never build on its naming. Where the documentation and the disk disagree, the disk wins and the unknown is reported as unknown rather than guessed.',
          'Read-only where it matters. The checkpoint store is never written; restoring stays Claude Code’s job.',
          'A verification tool that runs nothing reports success, so every gate has a floor on how much it did and every one has been mutation-verified against the bug it exists to catch.',
          'The interface animates transform and opacity only. Anything that forces a GPU readback tears the compositor.',
        ]),
      ],
    },
  ],
};

/* ── /community ────────────────────────────────────────────────────────────── */

export const COMMUNITY: Doc = {
  slug: 'community',
  title: 'Community',
  description:
    'Where to report a claudectl bug, ask a question, propose a feature and read the code of conduct.',
  intro:
    'claudectl is a young project with a small user base. Bug reports are genuinely useful, especially from macOS and Linux, where there is less real-world use than on Windows.',
  sections: [
    {
      id: 'help',
      heading: 'Getting help',
      blocks: [
        dl([
          {
            t: 'Something is broken',
            d: 'Open an issue. Include your platform, Python version, the claudectl version and what you ran. If a screen faulted, the traceback matters more than the description.',
          },
          {
            t: 'A question, or an idea',
            d: 'Start a discussion. Ideas that begin as a discussion tend to arrive as better issues.',
          },
          {
            t: 'It behaves oddly rather than failing',
            d: 'The documentation site has a troubleshooting page covering the common causes — a Claude Code CLI that is not on PATH, an account whose config directory moved, and a GUI that opens in the browser because PyQt6 is not installed.',
          },
        ]),
      ],
    },
    {
      id: 'contributing',
      heading: 'Contributing',
      blocks: [
        p(
          'Pull requests are welcome. The test suite is the contract: it is large, it is fast, and every gate in it exists because something broke once. If you change behaviour, the test that would have caught the old bug should fail before your fix and pass after it.',
        ),
      ],
    },
    {
      id: 'conduct',
      heading: 'Code of conduct',
      blocks: [
        p(
          'The project follows the Contributor Covenant. It applies to the issue tracker, discussions and pull requests, and the full text is on this site.',
        ),
      ],
    },
  ],
};

/* ── /about ────────────────────────────────────────────────────────────────── */

export const ABOUT: Doc = {
  slug: 'about',
  title: 'About claudectl',
  description:
    'Who builds claudectl, the MIT licence it ships under, and the Claude Code ecosystem projects its memory, review, skills and model routing are built on.',
  intro:
    'claudectl is built and maintained by Babar Muhammad Anas. It is MIT licensed, free, and has no subscription and no API key of its own.',
  sections: [
    {
      id: 'why',
      heading: 'Why it exists',
      blocks: [
        p(
          'It started as a session picker and became a workspace layer, because every problem it fixed turned out to sit one level below the one before it. Browsing sessions needs a project list; a project list is only useful if the next session starts with the right context; context is only affordable if something bounds it. The memory graph is the end of that chain, not the beginning.',
        ),
      ],
    },
    {
      id: 'credits',
      heading: 'Credits & inspiration',
      lead:
        'claudectl is built on ideas from the wider Claude Code ecosystem. With thanks:',
      blocks: [
        dl([
          {
            t: 'cognee and Aider’s repo-map',
            d: 'Graph memory and retrieval budgeting, both reimplemented from scratch in pure stdlib for project memory.',
          },
          {
            t: 'Anthropic’s claude-code code-review plugin',
            d: 'The confidence-scoring, high-threshold, CLAUDE.md-compliance review pattern behind claudectl review.',
          },
          {
            t: 'thedotmack/claude-mem',
            d: 'The session-observation to summary to SessionStart injection pattern behind recent-work memory.',
          },
          {
            t: 'anthropics/claude-plugins-official',
            d: 'The Skills extension model and plugin structure.',
          },
          {
            t: 'diegosouzapw/OmniRoute',
            d: 'A self-hosted free-tier model proxy; the inspiration for economy model routing and now the backend behind free execution in Plan → Execute.',
          },
          {
            t: 'olsenbrands/fable-foreman',
            d: 'The skill plus worker and verifier subagent pattern for delegating execution to cheaper models under a frontier model’s plan.',
          },
          {
            t: 'VoltAgent/awesome-claude-code-subagents',
            d: 'The 154-agent catalog the agent library mirrors. All credit for the agents goes to their original authors.',
          },
          {
            t: 'microsoft/markitdown',
            d: 'Document-to-markdown token-efficiency thinking; document ingestion is on the roadmap.',
          },
          {
            t: 'claudemarketplaces.com and Ponytail',
            d: 'Skill and plugin discovery, and the code-minimization rule behind one of the bundled hooks.',
          },
        ]),
        p(
          'Bundled starter skills are original write-ups inspired by patterns in community collections, each credited in-file.',
        ),
      ],
    },
    {
      id: 'licence',
      heading: 'Licence',
      blocks: [
        p(
          'MIT. Use it, fork it, ship it. claudectl uses your existing Claude Code authentication and adds no subscription and no API key of its own.',
        ),
      ],
    },
  ],
};

/* ── /contributing (fallback when ../CONTRIBUTING.md is absent) ─────────────── */

export const CONTRIBUTING: Doc = {
  slug: 'contributing',
  title: 'Contributing to claudectl',
  description:
    'How to work on claudectl — the development setup, the test suite as a contract, the lint gate, and what makes a change land.',
  intro:
    'claudectl has no build step and no runtime dependencies, so getting from a clone to a running copy is one command.',
  sections: [
    {
      id: 'setup',
      heading: 'Setting up',
      blocks: [
        code(
          'git clone https://github.com/babarmuhammad/claudectl.git\ncd claudectl\npython claude-sessions.py          # terminal UI\npython claude-sessions.py --gui    # desktop GUI\npython -m pytest                   # the suite',
        ),
        p(
          'Python 3.10 or newer. pytest is the only development dependency; PyQt6 is optional and only affects whether the GUI opens in a native window or your browser.',
        ),
      ],
    },
    {
      id: 'tests',
      heading: 'The test suite is the contract',
      blocks: [
        ul([
          'Every gate exists because something broke once. If you change behaviour, write the test that would have caught the old bug: it should fail before your fix and pass after it.',
          'Mutation-verify a new gate. A test nobody has watched fail is not a gate — revert your fix and confirm it goes red.',
          'A tool whose only output is "it passed" needs a floor on how much it did. The smoke checks count what they ran and fail below it, because one that ran nothing once reported success for three runs.',
        ]),
      ],
    },
    {
      id: 'style',
      heading: 'Style and the lint gate',
      blocks: [
        p(
          'The ruff rule set is deliberately narrow: the bug-finding rules only. Enabling the full style set would land the gate red and get it switched off. Style changes are welcome as their own mechanical diff, not bundled into a behaviour change.',
        ),
        ul([
          'Zero runtime dependencies is a hard constraint, not a preference. A new import from outside the standard library will not be merged.',
          'Commits follow Conventional Commits, and the changelog follows Keep a Changelog.',
          'Anything writing to stdout from a hook or the status line must reconfigure the stream to UTF-8 — Claude Code captures stdout as a pipe, so CPython otherwise picks the locale codepage.',
        ]),
      ],
    },
    {
      id: 'pr',
      heading: 'Opening a pull request',
      blocks: [
        ul([
          'Describe the behaviour that changed and the test that proves it.',
          'Keep the diff to one concern. A refactor and a fix in one branch is two reviews in one.',
          'CI runs on Windows, macOS and Linux across the supported Python versions on every push. It must be green.',
        ]),
      ],
    },
  ],
};

/** Every apex page, in the order /llms.txt lists them. */
export const DOCS: Doc[] = [
  HOME, FEATURES, DOWNLOAD, ARCHITECTURE, COMMUNITY, ABOUT, CONTRIBUTING,
];

/* ── plain text, for /llms-full.txt ─────────────────────────────────────────── */

function blockText(b: Block): string {
  switch (b.kind) {
    case 'p':
      return b.text;
    case 'ul':
      return b.items.map((i) => `- ${i}`).join('\n');
    case 'dl':
      return b.items.map((i) => `- ${i.t}: ${i.d}`).join('\n');
    case 'code':
      return [b.label ? `${b.label}:` : null, '```', b.text, '```']
        .filter(Boolean)
        .join('\n');
    case 'table':
      return [
        `| ${b.head.join(' | ')} |`,
        `|${b.head.map(() => '---').join('|')}|`,
        ...b.rows.map((r) => `| ${r.join(' | ')} |`),
      ].join('\n');
  }
}

export function docText(doc: Doc): string {
  const out: string[] = [`# ${doc.h1 ?? doc.title}`];
  if (doc.intro) out.push(doc.intro);
  for (const s of doc.sections) {
    out.push(`## ${s.heading}`);
    if (s.lead) out.push(s.lead);
    for (const b of s.blocks) out.push(blockText(b));
  }
  return out.join('\n\n');
}
