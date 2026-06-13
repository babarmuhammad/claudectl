"""Subagent management — browse, scaffold, AI-generate, edit, delete
Claude Code subagent definitions (`.claude/agents/*.md`).

Format: YAML-ish frontmatter between `---` fences (name, description,
tools, model) followed by the system-prompt body.
"""

import os
import re

from .config import W, get_claude_exe, open_in_editor, config_dir, MODELS, MODEL_LABELS
from .ui import (menu, text_input, flash, pause, confirm, multiselect,
                 run_with_progress, pager, _cls)
from . import config as _c
from . import render

# Tools a subagent can be granted (Claude Code built-ins). '' frontmatter
# (omit the field) inherits all tools.
KNOWN_TOOLS = ['Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep',
               'WebFetch', 'WebSearch', 'Task', 'TodoWrite']


def user_agents_dir():
    return os.path.join(config_dir, 'agents')


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
    model = menu([(l, v) for l, v in zip(MODEL_LABELS, MODELS)], "MODEL (default = inherit)")
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


def _new_agent_ai(project_path):
    scope_dir = _pick_category()
    if not scope_dir:
        return
    claude = get_claude_exe()
    if not claude:
        _cls(); print("\n  claude.exe not found.\n"); pause("  Press Enter..."); return
    name = text_input("Agent name (e.g. security-reviewer):")
    if not name:
        return
    role = text_input("What should this agent do? (one line):") or name

    from .claude_md import _build_ai_context, _pager_confirm
    ctx = _build_ai_context(project_path, None) if project_path else ''
    prompt = (
        f"Author a Claude Code subagent definition named '{name}'.\n"
        f"Purpose: {role}\n\n"
        + (f"PROJECT CONTEXT:\n{ctx}\n\n" if ctx else "")
        + "Output a markdown file with EXACTLY this shape and nothing else:\n"
        "---\n"
        f"name: {name}\n"
        "description: <one sentence, written so Claude knows WHEN to delegate to this agent>\n"
        "tools: <comma-separated subset of Read, Write, Edit, Bash, Glob, Grep, "
        "WebFetch, WebSearch, Task, TodoWrite — omit the line to inherit all>\n"
        "model: <one of haiku-4-5, sonnet-4-6, opus-4-8, fable-5 — omit to inherit>\n"
        "---\n\n"
        "<the system prompt body: role, focus, step-by-step approach, constraints>\n\n"
        "Do NOT create or write any files and do not use any tools — return the "
        "markdown text directly. No preamble, no code fences."
    )
    out, cancelled = run_with_progress(
        [claude, '--print', prompt, '--disallowedTools', 'Write,Edit,NotebookEdit,Bash'],
        ('CLAUDECTL', 'AGENTS', name), f'Authoring agent {name} with Claude...  (15-60s)',
        timeout=120)
    if cancelled:
        flash("Cancelled", ok=False); return
    content = (out or '').strip()
    if not content:
        flash("No output from Claude", ok=False, secs=1.4); return
    if not _pager_confirm(f"AGENT  /  {name}  — approve to write", content):
        _cls(); print("\n  Rejected — not written.\n"); pause("  Press Enter..."); return
    path = os.path.join(scope_dir, f"{_slug(name)}.md")
    try:
        os.makedirs(scope_dir, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content if content.endswith('\n') else content + '\n')
        flash(f"Created {os.path.basename(path)}")
        open_in_editor(path)
    except Exception as e:
        flash(f"Write failed: {e}", ok=False, secs=1.6)


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


def sync_project_agents(project_path, refs):
    """Make <project>/.claude/agents/ contain exactly the selected library
    agents. Claude auto-discovers them at launch — no command-line size limit.
    Only files claudectl previously placed (tracked in a manifest) are removed,
    so the user's own project agents are never touched. Returns count synced."""
    import json, shutil
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

    # copy selected
    written = []
    for fn, src in desired.items():
        try:
            shutil.copyfile(src, os.path.join(dest, fn))
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
    return len(written)


# Inline --agents JSON rides the command line (Windows ~32KB cap). Past this
# many agents the launch can fail, so warn the user.
SAFE_AGENT_LIMIT = 10


# ── per-session agent selection screen ───────────────────────

def select_session_agents(project_name, preselected=None):
    """Category-grouped multi-select of library agents for a session.
    Returns a sorted list of 'category/name' refs, or None if cancelled.
    Empty list = explicitly none."""
    chosen = set(preselected or [])
    cats = list_categories()
    if not cats:
        flash("No agents in library — install some first", ok=False, secs=1.4)
        return []
    while True:
        items = []
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
                view_fn=lambda ref: view_agent_file(paths.get(ref)))
            if picked is not None:
                # replace this category's selections with the new set
                chosen = {r for r in chosen if not r.startswith(cat + '/')} | picked
