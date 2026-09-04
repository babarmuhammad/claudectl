"""Subagent management — browse, scaffold, AI-generate, edit, delete
Claude Code subagent definitions (`.claude/agents/*.md`).

Format: YAML-ish frontmatter between `---` fences (name, description,
tools, model) followed by the system-prompt body.
"""

import os
import re

from .config import W, get_claude_exe, open_in_editor
from .ui import (menu, text_input, flash, pause, confirm, multiselect,
                 pager, _cls)
from . import config as _c
from . import render

# Tools a subagent can be granted (Claude Code built-ins). '' frontmatter
# (omit the field) inherits all tools.
KNOWN_TOOLS = ['Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep',
               'WebFetch', 'WebSearch', 'Task', 'TodoWrite']


def user_agents_dir(cfgdir=None):
    """`config_dir` used to be imported by value here: single-account AND
    frozen at import, so an account switch never moved it. Resolve per call."""
    return os.path.join(_c.resolve_config_dir(cfgdir), 'agents')


def project_agents_dir(project_path):
    return os.path.join(project_path, '.claude', 'agents')


# ── frontmatter parse / write ────────────────────────────────

def parse_agent(path):
    """Return (meta: dict, body: str). Tolerates missing/!malformed frontmatter."""
    try:
        text = open(path, encoding='utf-8', errors='ignore').read()
    except Exception:
        return {}, ''
    meta, body = {}, text
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            fm = text[3:end].strip('\n')
            body = text[end + 4:].lstrip('\n')
            for line in fm.splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    meta[k.strip()] = v.strip()
    return meta, body


def write_agent(path, meta, body):
    """Write an agent .md with frontmatter. Returns True on success."""
    order = ['name', 'description', 'tools', 'model']
    keys = order + [k for k in meta if k not in order]
    fm = '\n'.join(f"{k}: {meta[k]}" for k in keys if meta.get(k))
    out = f"---\n{fm}\n---\n\n{body.rstrip()}\n"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(out)
        return True
    except Exception:
        return False


def list_agents(scope_dir):
    """[(name, description, model, path)] for *.md in scope_dir."""
    out = []
    if not scope_dir or not os.path.isdir(scope_dir):
        return out
    for f in sorted(os.listdir(scope_dir)):
        if not f.endswith('.md'):
            continue
        path = os.path.join(scope_dir, f)
        meta, _ = parse_agent(path)
        out.append((meta.get('name', f[:-3]), meta.get('description', ''),
                    meta.get('model', ''), path))
    return out


#: what an agent with no `category:` is filed under. A word, not an empty
#: string, because it is a heading the reader sees.
NO_CATEGORY = 'Uncategorised'


def category_of(path):
    """The agent's category, from its own frontmatter.

    The library gets its categories from the folder tree, but a user or project
    agent lives flat in `.claude/agents/` — Claude Code reads that one
    directory, so a subfolder per category would hide every agent in it. The
    category rides in the frontmatter instead, where an unknown key is simply
    ignored by Claude Code and preserved by `write_agent`.
    """
    meta, _ = parse_agent(path)
    return (meta.get('category') or '').strip() or NO_CATEGORY


def installed_categories():
    """Every category name actually in use by an installed agent, sorted.

    Offered beside the library's folder names when you file a new agent, so the
    second agent in a category you invented lands in the same one rather than
    in "Reviewers" beside "reviewers".
    """
    seen = set()
    for r in all_installed():
        c = r.get('category') or ''
        if c and c != NO_CATEGORY:
            seen.add(c)
    return sorted(seen)


def _slug(name):
    return re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-') or 'agent'


# ── agents menu ──────────────────────────────────────────────

def agents_menu(project_path=None):
    """Browse the category-organized agent library; create/edit/delete agents.
    project_path is used only for project context in AI generation."""
    while True:
        cats = list_categories()
        items = []
        for cat in cats:
            n = len(list_library_agents(cat))
            items.append((f"{cat}  {_c.C_DIM}({n}){_c.C_RESET}", f'cat:{cat}'))
        if not cats:
            items.append((f"{_c.C_DIM}(library empty){_c.C_RESET}", None))
        items += [(f"{'─' * W}", None),
                  ('＋  New agent (manual)', '__new__'),
                  ('✦  New agent (AI-generated)', '__ai__')]

        sel = menu(items, "AGENTS  /  library by category")
        if not sel:
            return
        if sel == '__new__':
            _new_agent_manual(project_path)
        elif sel == '__ai__':
            _new_agent_ai(project_path)
        elif sel.startswith('cat:'):
            _category_browse(sel[4:], project_path)


def _category_browse(category, project_path):
    while True:
        agents = list_library_agents(category)
        items = []
        for name, desc, model, path in agents:
            tail = f"  {_c.C_DIM}{render.trunc(desc, 44)}{_c.C_RESET}" if desc else ''
            mtag = f"  {_c.C_DIM}[{model}]{_c.C_RESET}" if model else ''
            items.append((f"{name}{mtag}{tail}", f'agent:{path}'))
        if not agents:
            items.append((f"{_c.C_DIM}(empty category){_c.C_RESET}", None))
        sel = menu(items, f"AGENTS  /  {category}")
        if not sel:
            return
        if sel.startswith('agent:'):
            _agent_detail(sel[6:])


def _pick_category():
    """Choose an existing category or create a new one. Returns dir path or None."""
    cats = list_categories()
    items = [(c, f'c:{c}') for c in cats]
    items += [(f"{'─' * W}", None), ('＋  New category', '__newcat__')]
    sel = menu(items, "CATEGORY")
    if not sel:
        return None
    if sel == '__newcat__':
        name = text_input("New category name (e.g. 99-custom):")
        if not name:
            return None
        cat = _slug(name)
        d = category_dir(cat)
        os.makedirs(d, exist_ok=True)
        return d
    return category_dir(sel[2:])


def _new_agent_manual(project_path):
    scope_dir = _pick_category()
    if not scope_dir:
        return
    name = text_input("Agent name (e.g. code-reviewer):")
    if not name:
        return
    desc = text_input("One-line description (when should Claude use it?):") or ''
    tools = multiselect([(t, t) for t in KNOWN_TOOLS],
                        "TOOLS (none selected = inherit all)")
    if tools is None:
        return
    _ids, _labels = _c.models()
    model = menu([(l, v) for l, v in zip(_labels, _ids)], "MODEL (default = inherit)")
    meta = {'name': name, 'description': desc}
    if tools:
        meta['tools'] = ', '.join(t for t in KNOWN_TOOLS if t in tools)
    if model:
        meta['model'] = model
    body = (f"You are {name}, a focused subagent.\n\n"
            f"{desc}\n\n"
            f"## Guidelines\n- \n")
    path = os.path.join(scope_dir, f"{_slug(name)}.md")
    if os.path.exists(path) and not confirm(f"'{os.path.basename(path)}' exists — overwrite?"):
        return
    if write_agent(path, meta, body):
        flash(f"Created {os.path.basename(path)}")
        open_in_editor(path)
    else:
        flash("Write failed", ok=False, secs=1.4)


# ── AI-generated agents ──────────────────────────────────────
#
# Split into three non-interactive pieces plus a thin TUI wrapper, which is the
# shape `skills.build_ai_prompt` / `skills.write_skill_raw` already uses and the
# reason the skill generator works from both surfaces.
#
# `_new_agent_ai` used to BE the GUI's handler, and it is an interactive TUI
# flow: `_pick_category()` opens a `menu()`, which is not one of the five
# primitives `gui_api._install_bridge` patches. A job thread reaching it blocks
# in `wait_event()` forever — the job stayed 'running' until the six-hour
# reaper, which is exactly what "AI generate agents doesn't work" was. Four more
# defects sat behind that one: `text_input` imported by value so the bridge's
# input queue was never read even past the hang; two prompts asked for against
# one field collected; the prompt passed on ARGV (the 32767-char CreateProcess
# limit `memory._claude_stdin` exists to avoid) with no cwd, no HEADLESS_MARK
# and no budget cap; and the file written into claudectl's own read-only
# library, where Claude Code does not look for installed agents.


def build_ai_prompt(name, role, project_path=None):
    """The authoring prompt. Pure — shared by the TUI flow and the GUI job so
    both produce identical output."""
    from .claude_md import _build_ai_context
    ctx = _build_ai_context(project_path, None) if project_path else ''
    return (
        f"Author a Claude Code subagent definition named '{name}'.\n"
        f"Purpose: {role}\n\n"
        + (f"PROJECT CONTEXT:\n{ctx}\n\n" if ctx else "")
        + "Output a markdown file with EXACTLY this shape and nothing else:\n"
        "---\n"
        f"name: {name}\n"
        "description: <one sentence, written so Claude knows WHEN to delegate to this agent>\n"
        "tools: <comma-separated subset of Read, Write, Edit, Bash, Glob, Grep, "
        "WebFetch, WebSearch, Task, TodoWrite — omit the line to inherit all>\n"
        "model: <one of haiku-4-5, sonnet-5, opus-5, fable-5 — omit to inherit>\n"
        "---\n\n"
        "<the system prompt body: role, focus, step-by-step approach, constraints>\n\n"
        "Do NOT create or write any files and do not use any tools — return the "
        "markdown text directly. No preamble, no code fences."
    )


def write_agent_raw(md, name, scope='user', project_path=None, category=''):
    """Write generated agent markdown where Claude Code actually reads it.

    `~/.claude/agents/` or `<project>/.claude/agents/`, FLAT — the same
    destination `gui_api.api_agent_create` uses for a hand-written agent, and
    the one `category_of` documents: Claude Code reads that one directory, so a
    subfolder per category would hide every agent inside it. The category rides
    in the frontmatter instead.

    Returns {'ok', 'path', 'name', 'error'}.
    """
    md = (md or '').strip()
    if not md:
        return {'ok': False, 'error': 'nothing to write', 'path': '', 'name': name}
    d = (project_agents_dir(project_path) if scope == 'project' and project_path
         else user_agents_dir())
    path = os.path.join(d, f'{_slug(name)}.md')
    if category.strip():
        meta, body = _parse_md(md)
        meta.setdefault('category', category.strip())
        if write_agent(path, meta, body):
            return {'ok': True, 'path': path, 'name': name, 'error': ''}
        return {'ok': False, 'error': 'write failed', 'path': path, 'name': name}
    try:
        os.makedirs(d, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(md if md.endswith('\n') else md + '\n')
        return {'ok': True, 'path': path, 'name': name, 'error': ''}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'path': path, 'name': name}


def _parse_md(text):
    """Frontmatter of a markdown STRING (parse_agent takes a path)."""
    meta, body = {}, text
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            body = text[end + 4:].lstrip('\n')
            for line in text[3:end].strip('\n').splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    meta[k.strip()] = v.strip()
    return meta, body


def generate_agent_ai(name, role='', scope='user', project_path=None, category=''):
    """Author an agent with Claude and write it. NON-INTERACTIVE — this is what
    the GUI job calls, so it must not touch a keyboard primitive.

    Goes through `memory._claude_stdin`, which is the one seam that supplies the
    stdin prompt, the cwd, the HEADLESS_MARK, `--max-turns` and the
    `--max-budget-usd` cap. Returns the same dict as `write_agent_raw`, so the
    job has something to report; the old flow returned None and the UI could not
    tell written from rejected from failed.
    """
    from . import memory
    name = (name or '').strip()
    if not name:
        return {'ok': False, 'error': 'no agent name given', 'path': '', 'name': ''}
    out = memory._claude_stdin(
        build_ai_prompt(name, role or name, project_path),
        project_path or None, timeout=180,
        crumbs=('CLAUDECTL', 'AGENTS', name),
        label=f'Authoring agent {name} with Claude...  (15-60s)')
    if not (out or '').strip():
        return {'ok': False, 'error': memory.why_failed('No output from Claude'),
                'path': '', 'name': name}
    return write_agent_raw(out, name, scope, project_path, category)


def _new_agent_ai(project_path):
    """TUI wrapper: collect the fields, generate, show the diff, write."""
    from .claude_md import _pager_confirm
    if not get_claude_exe():
        _cls(); print("\n  claude.exe not found.\n"); pause("  Press Enter..."); return
    name = text_input("Agent name (e.g. security-reviewer):")
    if not name:
        return
    role = text_input("What should this agent do? (one line):") or name
    scope = 'user'
    if project_path:
        scope = menu([('user  (every project)', 'user'),
                      ('this project only', 'project')], "SCOPE")
        if not scope:
            return
    category = text_input("Category (optional — claudectl's own filing):") or ''

    from . import memory
    md = memory._claude_stdin(
        build_ai_prompt(name, role, project_path), project_path or None,
        timeout=180, crumbs=('CLAUDECTL', 'AGENTS', name),
        label=f'Authoring agent {name} with Claude...  (15-60s)')
    content = (md or '').strip()
    if not content:
        flash(memory.why_failed(), ok=False, secs=2.4); return
    if not _pager_confirm(f"AGENT  /  {name}  — approve to write", content):
        _cls(); print("\n  Rejected — not written.\n"); pause("  Press Enter..."); return
    r = write_agent_raw(content, name, scope, project_path, category)
    if r['ok']:
        flash(f"Created {os.path.basename(r['path'])}")
        open_in_editor(r['path'])
    else:
        flash(f"Write failed: {r['error']}", ok=False, secs=1.6)


def view_agent_file(path):
    """Read-only pager over a library agent's raw .md (frontmatter + body)."""
    if not path or not os.path.isfile(path):
        flash("Agent file not found", ok=False, secs=1.2)
        return
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        flash(f"Read failed: {e}", ok=False, secs=1.4)
        return
    w = render.content_width()
    lines = []
    for raw in text.replace('\t', '    ').split('\n'):
        if not raw:
            lines.append('')
            continue
        while len(raw) > w - 4:
            cut = raw.rfind(' ', 0, w - 4)
            cut = cut if cut > 0 else w - 4
            lines.append(raw[:cut])
            raw = raw[cut:].lstrip()
        lines.append(raw)
    pager(('CLAUDECTL', os.path.basename(path), 'AGENT'), lines)


def _agent_detail(path):
    meta, body = parse_agent(path)
    items = [
        (f"Name   :  {meta.get('name', '?')}", None),
        (f"Tools  :  {meta.get('tools', '(all)')}", None),
        (f"Model  :  {meta.get('model', '(inherit)')}", None),
        (f"{'─' * W}", None),
        ('📝  Edit in editor', 'edit'),
        ('🗑  Delete', 'delete'),
    ]
    sel = menu(items, f"AGENT  /  {meta.get('name', os.path.basename(path))}")
    if sel == 'edit':
        open_in_editor(path)
    elif sel == 'delete':
        if confirm(f"Delete agent '{os.path.basename(path)}'?", danger=True):
            try:
                os.remove(path)
                flash("Agent deleted")
            except Exception as e:
                flash(f"Delete failed: {e}", ok=False, secs=1.4)


def list_all_agent_names(project_path=None):
    """Names available to --agent: project scope overrides user scope."""
    names = {}
    for n, _, _, _ in list_agents(user_agents_dir()):
        names[n] = 'user'
    if project_path:
        for n, _, _, _ in list_agents(project_agents_dir(project_path)):
            names[n] = 'project'
    return sorted(names)


# ── agent library (category-organized store, injected via --agents) ──

def library_dir():
    return _c.agents_library_dir


def list_categories():
    """Category subfolders in the library, sorted. Plus uncategorized loose files."""
    d = library_dir()
    cats = []
    if os.path.isdir(d):
        cats = sorted(n for n in os.listdir(d)
                      if os.path.isdir(os.path.join(d, n)))
    return cats


def category_dir(category):
    return os.path.join(library_dir(), category)


def list_library_agents(category):
    """[(name, description, model, path)] for agents in a category."""
    return list_agents(category_dir(category))


def all_library_agents():
    """[(category, name, description, path)] across every category."""
    out = []
    for cat in list_categories():
        for name, desc, model, path in list_library_agents(cat):
            out.append((cat, name, desc, path))
    return out


def find_library_agent(ref):
    """ref 'category/name' → path, or None."""
    if '/' not in ref:
        return None
    cat, name = ref.split('/', 1)
    p = os.path.join(category_dir(cat), f"{name}.md")
    return p if os.path.isfile(p) else None


_LANG_HINTS = {
    'C#': 'csharp dotnet aspnet',
    'C/C++': 'cpp c++ systems embedded',
    'C++': 'cpp c++ systems embedded',
    'Python': 'python fastapi django pytest',
    'JavaScript': 'javascript typescript node frontend react',
    'TypeScript': 'typescript javascript node frontend react',
    'Go': 'golang go',
    'Rust': 'rust',
    'Java': 'java spring kotlin',
    'Docs': 'documentation markdown',
}


def suggest_agents(project_path, proj_folder, top_k=8):
    """Rank library agents against the project's signals — languages (from the
    cached dependency graph), semantic-memory entities, and project name.
    Local scoring only (no Claude call). Returns [(ref, reason, score)]."""
    from .recall import _tokenize
    from . import memory as memory_mod

    signals = {}                                    # token -> weight
    def add(text, w):
        for t in _tokenize(text or ''):
            signals[t] = max(signals.get(t, 0), w)

    add(os.path.basename(project_path or ''), 2.0)
    try:
        from . import connections
        # cache ONLY — never build here (a fresh build parses thousands of
        # files and would freeze the agents screen)
        g = connections._load_cache(project_path, proj_folder) or {}
        for lang, _cnt in (g.get('meta', {}).get('languages') or [])[:4]:
            add(lang, 4.0)
            add(_LANG_HINTS.get(lang, ''), 4.0)
    except Exception:
        pass
    try:
        mem = memory_mod.load_memory(project_path, proj_folder)
        for e in mem.get('entities', [])[:60]:
            if e.get('type') != 'lesson':
                add(e.get('name', ''), 1.0)
                add(e.get('summary', ''), 0.5)
    except Exception:
        pass
    if not signals:
        return []

    scored = []
    for cat, name, desc, _path in all_library_agents():
        atok = _tokenize(name) | _tokenize(desc) | _tokenize(cat)
        hit = [t for t in atok if t in signals]
        score = sum(signals[t] for t in hit)
        if score < 4.0:                              # noise floor
            continue
        top = sorted(hit, key=lambda t: -signals[t])[:3]
        scored.append((f"{cat}/{name}", 'matches ' + ', '.join(top), score))
    scored.sort(key=lambda x: (-x[2], x[0]))
    return scored[:top_k]


def build_agents_json(refs):
    """Build the --agents JSON object from library refs ['cat/name', ...].
    Returns a compact JSON string ({name: {description, prompt, ...}})."""
    import json
    obj = {}
    for ref in refs:
        path = find_library_agent(ref)
        if not path:
            continue
        meta, body = parse_agent(path)
        name = meta.get('name') or ref.split('/', 1)[1]
        entry = {'description': meta.get('description', ''),
                 'prompt': body.strip()}
        if meta.get('tools'):
            entry['tools'] = [t.strip() for t in meta['tools'].split(',') if t.strip()]
        if meta.get('model'):
            entry['model'] = meta['model']
        obj[name] = entry
    return json.dumps(obj, ensure_ascii=False)


def write_agents_json_tempfile(refs):
    """Write the --agents JSON to a temp file; return its path (or '').
    Kept for completeness/tests — the launch path uses sync_project_agents
    instead, because inline --agents JSON overruns the Windows command line
    for real (multi-KB) agents."""
    import tempfile
    if not refs:
        return ''
    js = build_agents_json(refs)
    if js == '{}':
        return ''
    path = os.path.join(tempfile.gettempdir(), 'claudectl_agents.json')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(js)
        return path
    except Exception:
        return ''


_MANIFEST = '.claudectl-managed.json'


def sync_project_agents(project_path, refs, omniroute=False):
    """Make <project>/.claude/agents/ contain exactly the selected library
    agents. Claude auto-discovers them at launch — no command-line size limit.
    Only files claudectl previously placed (tracked in a manifest) are removed,
    so the user's own project agents are never touched. Returns count synced.

    When *omniroute* is truthy, the ``model:`` field is stripped from copied
    agent frontmatter so agents inherit the session model via
    ``CLAUDE_CODE_SUBAGENT_MODEL`` instead of trying to route to a bare
    Anthropic model id through OmniRoute (which would fail)."""
    import json, shutil, re
    if not project_path:
        return 0
    dest = os.path.join(project_path, '.claude', 'agents')
    manifest_path = os.path.join(dest, _MANIFEST)
    try:
        prev = json.load(open(manifest_path, encoding='utf-8'))
        if not isinstance(prev, list):
            prev = []
    except Exception:
        prev = []

    # desired filename -> source path
    desired = {}
    for ref in refs:
        src = find_library_agent(ref)
        if src:
            desired[os.path.basename(src)] = src

    if not desired and not prev:
        return 0
    os.makedirs(dest, exist_ok=True)

    # remove managed files no longer selected
    for fn in prev:
        if fn not in desired:
            fp = os.path.join(dest, fn)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass

    # copy selected (strip model: frontmatter when routing via OmniRoute
    # so agents inherit CLAUDE_CODE_SUBAGENT_MODEL instead of trying to
    # call a bare Anthropic model id through the OmniRoute proxy)
    written = []
    for fn, src in desired.items():
        try:
            dest_path = os.path.join(dest, fn)
            if omniroute:
                content = open(src, encoding='utf-8').read()
                # Remove model: line from YAML frontmatter between --- fences
                if content.startswith('---'):
                    end = content.find('\n---', 3)
                    if end != -1:
                        fm = content[3:end]
                        body = content[end + 4:]
                        fm_lines = [l for l in fm.splitlines()
                                    if not l.strip().startswith('model:')]
                        content = '---' + '\n'.join(fm_lines) + '\n---' + body
                with open(dest_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            else:
                shutil.copyfile(src, dest_path)
            written.append(fn)
        except Exception:
            pass

    try:
        if written:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(written, f)
        elif os.path.isfile(manifest_path):
            os.remove(manifest_path)
    except Exception:
        pass
    # Installing an agent is only half of getting it used — see the note above
    # write_routing_block. Refreshed here so the table can never describe a
    # selection that is no longer on disk.
    try:
        write_routing_block(project_path)
        write_agent_index(project_path)
    except Exception:
        pass
    return len(written)


# Inline --agents JSON rides the command line (Windows ~32KB cap). Past this
# many agents the launch can fail, so warn the user.
SAFE_AGENT_LIMIT = 10


# ── making the installed agents actually get used ────────────
#
# Copying agent files into <project>/.claude/agents/ makes them AVAILABLE.
# It does not make them used: Claude Code decides to delegate by matching the
# task against each agent's `description`, and library descriptions are written
# as catalogue entries ("Use this agent when building server-side APIs…"), which
# read as documentation rather than as a trigger. The result is the complaint
# this exists to answer — a project carrying ten agents that never fire.
#
# The lever is CLAUDE.md, because it is the one thing read on EVERY turn. A
# short delegation table there turns "these exist somewhere" into "for this kind
# of work, hand it to this one". It is written from the agents' own frontmatter,
# so it cannot drift from what is installed, and it is a sentinel block, so it
# is replaced rather than appended and disappears when the selection empties.

def _first_sentence(text, cap=150):
    t = ' '.join((text or '').split())
    # library descriptions open with "Use this agent when/for …" — that clause
    # IS the trigger, so keep it and drop the rest of the catalogue prose
    for stop in ('. ', '; '):
        if stop in t:
            t = t.split(stop)[0]
            break
    t = re.sub(r'(?i)^use (?:this|the) agent (?:when|for|to)\s*', '', t).strip()
    return (t[:cap - 1] + '…') if len(t) > cap else t


def routing_table(project_path):
    """[(name, trigger)] for the agents installed in this project, in the order
    Claude will see them."""
    dest = os.path.join(project_path, '.claude', 'agents')
    return [(name, _first_sentence(desc))
            for name, desc, _model, _path in list_agents(dest)]


#: what the per-turn nudge hook reads. A hook that fires on every prompt must
#: not open and parse every agent file, so `sync_project_agents` writes this one
#: small index instead — the cost rule this codebase learned from the recall
#: hook's counters and the worklog hook's transcript re-scan.
AGENT_INDEX = '.claudectl-agents.json'

_KW = re.compile(r'[a-z][a-z0-9+#._-]{2,}')
_KW_STOP = {'the', 'and', 'for', 'with', 'this', 'that', 'when', 'use', 'used',
            'using', 'agent', 'agents', 'code', 'project', 'file', 'files',
            'from', 'into', 'your', 'you', 'are', 'was', 'has', 'have', 'not',
            'but', 'can', 'all', 'any', 'run', 'make', 'need', 'want', 'like',
            'also', 'more', 'than', 'then', 'them', 'invoke', 'proactively'}


def keywords_for(name, description, cap=24):
    """The words that should make this agent come to mind.

    Taken from the description because that is what Claude Code itself matches
    on — the hook and the model are then looking at the same text, rather than
    at two ideas of what the agent is for."""
    words = [w for w in _KW.findall(('%s %s' % (name, description)).lower())
             if w not in _KW_STOP]
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:cap]


def write_agent_index(project_path):
    """Refresh `.claude/.claudectl-agents.json` from what is installed."""
    import json
    dest = os.path.join(project_path, '.claude', 'agents')
    rows = [{'name': name, 'keywords': keywords_for(name, desc)}
            for name, desc, _m, _p in list_agents(dest)]
    path = os.path.join(project_path, '.claude', AGENT_INDEX)
    if not rows:
        try:
            os.remove(path)
        except OSError:
            pass
        return 0
    _c.write_atomic(path, json.dumps({'agents': rows}, indent=1))
    return len(rows)


def write_routing_block(project_path):
    """Refresh the CLAUDECTL:AGENTS block in <project>/CLAUDE.md from what is
    actually installed. Removes it when no agents are. Returns the row count."""
    from .claude_md import upsert_block
    from .config import _AGENTS_START, _AGENTS_END, generated_note
    rows = routing_table(project_path)
    if not rows:
        upsert_block(project_path, _AGENTS_START, _AGENTS_END, '')
        return 0
    lines = '\n'.join('- **%s** — %s' % (n, t or 'see its own description')
                      for n, t in rows)
    section = (
        f"{_AGENTS_START}\n## Subagents available here (claudectl — auto-maintained)\n"
        + generated_note('the agents installed in .claude/agents/',
                         'the Agents page, or any change to those files') + "\n\n"
        "Delegate to one of these with the Agent tool when the work matches — "
        "prefer a specialist over doing it inline, and say which one you used.\n\n"
        f"{lines}\n{_AGENTS_END}\n")
    upsert_block(project_path, _AGENTS_START, _AGENTS_END, section)
    return len(rows)


def sharpen_prompt(rows):
    """The authoring prompt for rewriting descriptions into trigger form.

    Only the `description` is touched, because it is the ONLY field Claude Code
    matches a task against — rewriting the body would change what the agent does
    while leaving the reason it never gets picked exactly as it was."""
    listing = '\n'.join('- %s: %s' % (n, d or '(no description)') for n, d in rows)
    return (
        "Rewrite the `description` field of these Claude Code subagents so the "
        "router actually picks them.\n\n"
        "Claude Code chooses a subagent by matching the user's task against this "
        "one field. A description written as a job title ('Expert backend "
        "engineer') never matches anything; one written as a trigger does.\n\n"
        "For each agent below, output exactly one line:\n"
        "<name>|Use PROACTIVELY when <concrete trigger: the kind of task, the "
        "file types, the words a user would actually type>. Do not use for "
        "<the nearest thing it should NOT take>.\n\n"
        "Rules: one line per agent, same order, no numbering, no commentary, no "
        "code fences. Keep each under 220 characters. Preserve the agent's real "
        "purpose — you are sharpening how it is found, not changing what it is.\n\n"
        "AGENTS:\n" + listing)


def apply_descriptions_dir(agents_dir, new_by_name):
    """Write rewritten descriptions into one directory of agent files.

    Frontmatter only: `write_agent` re-emits the file from (meta, body), so the
    body is carried through byte-for-byte and a bad rewrite can only ever have
    damaged one field. Knows nothing about projects, so it serves a project's
    `.claude/agents`, an account's user-level agents and the library alike."""
    done = []
    for name, desc, _model, path in list_agents(agents_dir):
        new = (new_by_name.get(name) or '').strip()
        if not new or new == desc:
            continue
        meta, body = parse_agent(path)
        meta['description'] = new
        if write_agent(path, meta, body):
            done.append(name)
    return done


def apply_descriptions(project_path, new_by_name):
    """As above for a project, plus the two files that only a project has: the
    CLAUDE.md routing table and the nudge hook's index."""
    done = apply_descriptions_dir(project_agents_dir(project_path), new_by_name)
    if done:
        write_routing_block(project_path)
        write_agent_index(project_path)
    return done


def all_installed():
    """Every agent file claudectl can reach, across every scope.

    [{'scope','dir','project_path','account','name','desc','path'}] over each
    account's user-level agents, each project's `.claude/agents`, and the
    claudectl library — so sharpening the library means every FUTURE install is
    already sharp, not just the copies that exist today.

    Best-effort per source: one unreadable project must not hide the rest.
    """
    out = []

    def _add(scope, d, project_path='', account=''):
        if not d or not os.path.isdir(d):
            return
        for name, desc, _model, path in list_agents(d):
            out.append({'scope': scope, 'dir': d, 'project_path': project_path,
                        'account': account, 'name': name,
                        'desc': desc or '', 'path': path,
                        'category': category_of(path)})

    try:
        for acct, cfgdir in _c.all_config_dirs():
            _add('user', user_agents_dir(cfgdir), account=acct)
    except Exception:
        _add('user', user_agents_dir())
    try:
        from . import gui
        seen = set()
        for row in gui.list_projects():
            p = row.get('path')
            if not p or p in seen:
                continue
            seen.add(p)
            _add('project', project_agents_dir(p), project_path=p,
                 account=row.get('name', ''))
    except Exception:
        _c.log.exception('agents: project scan failed')
    try:
        for cat in list_categories():
            _add('library', os.path.join(library_dir(), cat))
    except Exception:
        pass
    return out


def sharpen_groups(rows):
    """Group installs by (name, description) — one prompt row, many writes.

    A library agent copied into twelve projects is the SAME description twelve
    times. Asking a model to rewrite it twelve times costs twelve times as much
    and invites twelve different answers to the same question."""
    groups = {}
    for r in rows:
        groups.setdefault((r['name'], r['desc']), []).append(r)
    return groups


def parse_sharpened(text):
    """`name|description` lines → {name: description}. Tolerant: a model that
    adds a stray blank line or a bullet must not lose the whole batch."""
    out = {}
    for line in (text or '').splitlines():
        line = line.strip().lstrip('-*0123456789. ').strip()
        if '|' not in line:
            continue
        name, _, desc = line.partition('|')
        name, desc = name.strip().strip('`'), desc.strip()
        if name and len(desc) > 10:
            out[name] = desc[:400]
    return out


def usage(cfgdir=None):
    """{agent_name: age_string} from Claude Code's own `agentLastUsed`.

    The honest measure of whether any of this works, and it costs nothing —
    `clientstate` already reads that file. An agent installed months ago and
    never used is the thing to remove, not to explain."""
    try:
        from . import clientstate
        return {r['name']: r.get('last_used', '')
                for r in clientstate.usage_rollup(cfgdir).get('agents') or []}
    except Exception:
        return {}


# ── per-session agent selection screen ───────────────────────

def select_session_agents(project_name, preselected=None, project_path=None,
                          proj_folder=None):
    """Category-grouped multi-select of library agents for a session.
    Returns a sorted list of 'category/name' refs, or None if cancelled.
    Empty list = explicitly none. When project_path is given, a 'Suggested
    for this project' section (local signal matching) appears on top."""
    chosen = set(preselected or [])
    cats = list_categories()
    if not cats:
        flash("No agents in library — install some first", ok=False, secs=1.4)
        return []
    suggested = []
    if project_path:
        try:
            suggested = suggest_agents(project_path, proj_folder)
        except Exception:
            suggested = []
    while True:
        items = []
        for ref, reason, _score in suggested:
            mark = '☑' if ref in chosen else '☐'
            items.append((f"{mark} ★ {ref}  {_c.C_DIM}{reason}{_c.C_RESET}", f'tog:{ref}'))
        if suggested:
            items.append((f"{'─' * W}", None))
        for cat in cats:
            n_total = len(list_library_agents(cat))
            n_sel = sum(1 for r in chosen if r.startswith(cat + '/'))
            tag = f"  {_c.C_OK}{n_sel} selected{_c.C_RESET}" if n_sel else ''
            items.append((f"{cat}  {_c.C_DIM}({n_total}){_c.C_RESET}{tag}", f'cat:{cat}'))
        over = len(chosen) > SAFE_AGENT_LIMIT
        done_label = f"✓  Done ({len(chosen)} agent(s) selected)"
        if over:
            done_label = (f"✓  Done ({_c.C_WARN}{len(chosen)} — over {SAFE_AGENT_LIMIT}, "
                          f"may slow startup{_c.C_RESET})")
        items += [(f"{'─' * W}", None),
                  (done_label, '__done__'),
                  ('✗  Clear all', '__clear__')]
        sel = menu(items, f"SESSION AGENTS  /  {project_name}")
        if sel is None:
            return sorted(chosen) if chosen else []
        if sel == '__done__':
            if over:
                flash(f"{len(chosen)} agents selected — over the safe limit of "
                      f"{SAFE_AGENT_LIMIT}; many subagents enlarge context and "
                      f"can slow Claude startup.",
                      ok=False, secs=2.2)
            return sorted(chosen)
        if sel == '__clear__':
            chosen = set()
        elif sel.startswith('tog:'):
            ref = sel[4:]
            chosen.symmetric_difference_update({ref})
        elif sel.startswith('cat:'):
            cat = sel[4:]
            agents = list_library_agents(cat)
            pre = {f"{cat}/{name}" for name, *_ in agents
                   if f"{cat}/{name}" in chosen}
            paths = {f"{cat}/{name}": path for name, desc, model, path in agents}
            picked = multiselect(
                [(f"{name}  {_c.C_DIM}{render.trunc(desc, 50)}{_c.C_RESET}", f"{cat}/{name}")
                 for name, desc, model, path in agents],
                f"{cat}", preselected=pre,
                view_fn=lambda ref, paths=paths: view_agent_file(paths.get(ref)))
            if picked is not None:
                # replace this category's selections with the new set
                chosen = {r for r in chosen if not r.startswith(cat + '/')} | picked
