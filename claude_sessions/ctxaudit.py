"""Context Weight Audit — what Claude auto-loads at session start, in tokens.

Everything counted here is paid on EVERY message of every session for this
project (CLAUDE.md and memory files ride in the context on each turn), so this
screen is the single place to see and shrink the per-turn token floor:
CLAUDE.md (split into its machine blocks vs manual content), the global
CLAUDE.md, .claude/rules (lazy when glob-scoped), the per-project system
prompt, SessionStart hook injections, and MCP server overhead.
"""

import os
import re

from . import config as _c
from . import render
from .config import C_DIM, C_RESET, C_OK, C_WARN
from .memory import tokens_estimate

MCP_TOKENS_PER_SERVER = 800     # rough: tool schemas / listing per server
BIG_ITEM_TOKENS = 1000
CLAUDE_MD_LINES_WARN = 200
GLOBAL_TOKENS_WARN = 500
SYSPROMPT_TOKENS_WARN = 500

COMPACT_HEADING = '# Compact instructions'


def split_blocks(text):
    """Split CLAUDE.md text into its sentinel machine blocks and the manual
    rest. Returns {'autogen','sessions','memory','manual'} ('' when absent)."""
    manual = text or ''
    out = {}
    for key, (start, end) in (
            ('autogen',  (_c._AUTOGEN_START, _c._AUTOGEN_END)),
            ('sessions', (_c._SESSIONS_START, _c._SESSIONS_END)),
            ('memory',   (_c._MEMORY_START, _c._MEMORY_END))):
        if start in manual and end in manual:
            i, j = manual.index(start), manual.index(end) + len(end)
            out[key] = manual[i:j]
            manual = manual[:i] + manual[j:]
        else:
            out[key] = ''
    out['manual'] = manual
    return out


def _read(path):
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception:
        return ''


def _rule_is_lazy(text):
    """A rule file with globs:/paths: frontmatter only loads when Claude
    touches matching files — zero always-on cost."""
    lines = (text or '').splitlines()
    if not lines or lines[0].strip() != '---':
        return False
    for ln in lines[1:12]:
        if ln.strip() == '---':
            break
        if re.match(r'^(globs|paths)\s*:', ln.strip()):
            return True
    return False


def audit_items(project_path, proj_folder, settings=None):
    """[{'label','tokens','lazy','warnings','path'}] — one row per auto-loaded
    surface. tokens=None means unknowable statically (shown as ~?)."""
    if settings is None:
        from .config import load_settings
        settings = load_settings()
    items = []

    def add(label, tokens, path=None, lazy=False, warnings=None):
        items.append({'label': label, 'tokens': tokens, 'lazy': lazy,
                      'warnings': warnings or [], 'path': path})

    # ── project CLAUDE.md, split into blocks ──
    md_path = os.path.join(project_path, 'CLAUDE.md') if project_path else ''
    md = _read(md_path) if md_path else ''
    if md:
        blocks = split_blocks(md)
        cap = settings.get('claude_md_sessions_cap', 10)
        manual_warn = []
        if len(md.splitlines()) > CLAUDE_MD_LINES_WARN:
            manual_warn.append(f"CLAUDE.md > {CLAUDE_MD_LINES_WARN} lines — compress it (c)")
        if COMPACT_HEADING.lower() not in md.lower():
            manual_warn.append("no '# Compact instructions' section — add one (i)")
        add('CLAUDE.md · manual content', tokens_estimate(blocks['manual']),
            md_path, warnings=manual_warn)
        if blocks['autogen']:
            add('CLAUDE.md · autogen (repos/commits)', tokens_estimate(blocks['autogen']),
                md_path)
        if blocks['sessions']:
            n_entries = sum(1 for l in blocks['sessions'].splitlines()
                            if l.strip().startswith('- '))
            w = []
            if cap and n_entries > cap:
                w.append(f"{n_entries} session entries (cap {cap}) — prune (p)")
            add(f'CLAUDE.md · session topics ({n_entries})',
                tokens_estimate(blocks['sessions']), md_path, warnings=w)
        if blocks['memory']:
            add('CLAUDE.md · memory digest', tokens_estimate(blocks['memory']), md_path)
    else:
        add('CLAUDE.md', 0, md_path or None,
            warnings=['missing — press c in the sessions menu to scaffold'])

    # ── global CLAUDE.md (every session of EVERY project) ──
    g = _read(_c.global_claude_md)
    if g:
        t = tokens_estimate(g)
        add('global ~/.claude/CLAUDE.md', t, _c.global_claude_md,
            warnings=([f'> {GLOBAL_TOKENS_WARN} tok — loads in EVERY project']
                      if t > GLOBAL_TOKENS_WARN else []))

    # ── .claude/rules/*.md ──
    rules_dir = os.path.join(project_path, '.claude', 'rules') if project_path else ''
    if rules_dir and os.path.isdir(rules_dir):
        for nm in sorted(os.listdir(rules_dir)):
            if not nm.endswith('.md'):
                continue
            txt = _read(os.path.join(rules_dir, nm))
            add(f'rule {nm}', tokens_estimate(txt), os.path.join(rules_dir, nm),
                lazy=_rule_is_lazy(txt))

    # ── per-project system prompt ──
    sp = os.path.join(proj_folder, 'system-prompt.txt') if proj_folder else ''
    if sp and os.path.isfile(sp):
        t = tokens_estimate(_read(sp))
        add('system-prompt.txt (--system-prompt-file)', t, sp,
            warnings=([f'> {SYSPROMPT_TOKENS_WARN} tok'] if t > SYSPROMPT_TOKENS_WARN else []))

    # ── SessionStart hook injections ──
    try:
        from . import hooks as hooks_mod
        for e in (hooks_mod._load().get('hooks', {}) or {}).get('SessionStart', []):
            for h in e.get('hooks', []) if isinstance(e, dict) else []:
                cmd = h.get('command', '')
                if 'minimalcode_hook.py' in cmd:
                    from .minimalcode_hook import _RULE
                    add('hook minimal-code (SessionStart)', tokens_estimate(_RULE))
                elif 'concise_hook.py' in cmd:
                    from .concise_hook import _RULE
                    add('hook concise-output (SessionStart)', tokens_estimate(_RULE))
                elif cmd:
                    add(f'hook SessionStart: {cmd[:40]}', None,
                        warnings=['injection size unknown'])
    except Exception:
        pass

    # ── MCP servers ──
    try:
        from . import mcp as mcp_mod
        n = len(mcp_mod.mcp_servers) if getattr(mcp_mod, '_mcp_ready', False) else 0
        if n:
            add(f'MCP servers ({n}) — rough estimate (tool schemas)',
                n * MCP_TOKENS_PER_SERVER)
    except Exception:
        pass

    for it in items:
        if (not it['lazy'] and it['tokens'] and it['tokens'] > BIG_ITEM_TOKENS
                and not it['warnings']):
            it['warnings'].append(f'> {BIG_ITEM_TOKENS} tok on every turn')
    return items


def audit_total(items):
    """Estimated always-on tokens (lazy/unknown rows excluded)."""
    return sum(i['tokens'] for i in items if not i['lazy'] and i['tokens'])


def append_compact_section(project_path):
    """Add the default '# Compact instructions' section to CLAUDE.md (steers
    Claude Code's auto-compaction). Returns True if written."""
    md_path = os.path.join(project_path, 'CLAUDE.md')
    md = _read(md_path)
    if not md or COMPACT_HEADING.lower() in md.lower():
        return False
    section = (f"\n{COMPACT_HEADING}\n\n"
               "When compacting, preserve: the current task and its state, recent "
               "code changes with file paths, test results, and decisions made. "
               "Drop: exploration dead-ends, old tool output, and resolved errors.\n")
    try:
        with open(md_path, 'a', encoding='utf-8') as f:
            f.write(section)
        return True
    except Exception:
        return False


def audit_screen(project_path, proj_folder, project_name):
    from .ui import wait_event, flash, confirm
    while True:
        items = audit_items(project_path, proj_folder)
        total = audit_total(items)
        frame = [render.header('CLAUDECTL', project_name, 'CONTEXT WEIGHT'), '',
                 f"  {C_DIM}Estimated tokens auto-loaded on every turn of a session "
                 f"here (chars/4):{C_RESET}", '']
        for it in items:
            tok = '   ~?' if it['tokens'] is None else f"~{it['tokens']:>4}"
            tag = f"  {C_DIM}[lazy — loads on file touch]{C_RESET}" if it['lazy'] else ''
            frame.append(f"  {C_DIM if it['lazy'] else ''}{tok} tok  "
                         f"{it['label']}{C_RESET}{tag}")
            for w in it['warnings']:
                frame.append(f"             {C_WARN}▲ {w}{C_RESET}")
        frame += ['', render.hline(),
                  f"  {C_OK}total always-on ≈ {total} tok / message{C_RESET}"
                  f"  {C_DIM}(+ system prompt & tools from Claude Code itself){C_RESET}",
                  render.hline(), '',
                  render.hint_keys([('p', 'prune sessions/autogen'),
                                    ('c', 'compress CLAUDE.md (AI)'),
                                    ('i', 'add compact instructions'),
                                    ('d', 'deny rules'), ('r', 'refresh'),
                                    ('ESC', 'back')])]
        render.render_frame(frame)
        ev = wait_event()
        if ev[0] == 'esc':
            return
        if ev[0] != 'char':
            continue
        ch = ev[1]
        if ch == 'r':
            continue
        if ch == 'p':
            if confirm("Rebuild AUTOGEN + prune session topics to the configured cap?"):
                from .claude_md import prune_claude_md
                res = prune_claude_md(project_path, proj_folder)
                if res:
                    flash(f"Pruned: ~{res[0]} → ~{res[1]} tok", ok=True, secs=1.6)
                else:
                    flash("Nothing to prune (no CLAUDE.md?)", ok=False, secs=1.4)
        elif ch == 'c':
            from .claude_md import ai_compress_claude_md
            ai_compress_claude_md(project_path, proj_folder)
        elif ch == 'i':
            if append_compact_section(project_path):
                flash("Compact instructions added to CLAUDE.md", ok=True, secs=1.4)
            else:
                flash("Already present (or no CLAUDE.md)", ok=False, secs=1.4)
        elif ch == 'd':
            from .denygen import deny_rules_screen
            deny_rules_screen(project_path, project_name)
