"""Transcript viewing, export, and session metadata panel."""

import os
import re
import json
import textwrap
from datetime import datetime

from .config import C_RESET, C_DIM, C_BOLD, C_ACCENT, C_OK
from .config import open_in_editor
from .sessions import load_name
from . import render


# ── extraction ───────────────────────────────────────────────

def iter_transcript(jsonl_path):
    """Conversation messages: [{'role','text','ts'}]. Text blocks only —
    tool calls, tool results, thinking blocks and API errors are dropped."""
    try:
        with open(jsonl_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return []

    out = []
    for line in lines:
        ls = line.strip()
        if not ls:
            continue
        try:
            obj = json.loads(ls)
        except Exception:
            continue
        if obj.get('isApiErrorMessage'):
            continue
        msg  = obj.get('message') or {}
        role = obj.get('role') or msg.get('role', '')
        if role not in ('user', 'assistant'):
            continue
        content = obj.get('content') or msg.get('content', '')
        texts = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    t = block.get('text', '').strip()
                    if t:
                        texts.append(t)
        elif isinstance(content, str) and content.strip():
            texts.append(content.strip())
        text = '\n'.join(texts)
        if not text:
            continue
        # light noise filter — keep real conversation, drop harness chatter
        if text.startswith('<') or text.startswith('Caveat:'):
            continue
        out.append({'role': role, 'text': text, 'ts': obj.get('timestamp', '')})
    return out


# ── metadata ─────────────────────────────────────────────────

def _fmt_ts(epoch):
    if not epoch:
        return '?'
    try:
        return datetime.fromtimestamp(epoch).strftime('%d %b %Y %H:%M')
    except Exception:
        return '?'


def _fmt_duration(first, last):
    if not first or not last or last <= first:
        return ''
    mins = int((last - first) / 60)
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h {mins % 60}m"


def metadata_lines(stats, name, sid, plain=False):
    """Session info lines for the metadata panel / export header.
    plain=True strips colors (markdown export)."""
    from .stats import estimate_cost, _sum_usage, fmt_tok
    d  = '' if plain else C_DIM
    b  = '' if plain else C_BOLD
    r  = '' if plain else C_RESET
    u  = _sum_usage(stats)
    cost, exact = estimate_cost(stats.get('usage_by_model'))
    dur = _fmt_duration(stats.get('first_ts'), stats.get('last_ts'))
    lines = [
        f"{d}Name     :{r} {b}{name or stats.get('title') or '(unnamed)'}{r}",
        f"{d}Session  :{r} {sid}",
        f"{d}Model(s) :{r} {', '.join(stats.get('models') or []) or '?'}",
        f"{d}Branch   :{r} {stats.get('branch') or '?'}    {d}CWD:{r} {stats.get('cwd') or '?'}",
        f"{d}Activity :{r} {_fmt_ts(stats.get('first_ts'))} → {_fmt_ts(stats.get('last_ts'))}"
        + (f"  ({dur})" if dur else ''),
        f"{d}Messages :{r} {stats.get('count', 0)}"
        + (f"    {d}API errors:{r} {stats['api_errors']}" if stats.get('api_errors') else ''),
        f"{d}Tokens   :{r} in {fmt_tok(u['in'])}  out {fmt_tok(u['out'])}"
        f"  cache-r {fmt_tok(u['cache_read'])}  cache-w {fmt_tok(u['cache_create'])}"
        f"    {d}est. cost:{r} {'~' if not exact else ''}${cost:.2f}",
    ]
    return lines


def show_metadata(proj_folder, sid, project_name):
    """Standalone metadata panel (hotkey 'i')."""
    from .stats import get_session_stats_cached
    from .ui import pager
    jsonl = os.path.join(proj_folder, f"{sid}.jsonl")
    stats = get_session_stats_cached(jsonl)
    name  = load_name(proj_folder, sid)
    pager(('CLAUDECTL', project_name, 'SESSION INFO'), metadata_lines(stats, name, sid))


# ── viewer ───────────────────────────────────────────────────

def view_transcript(proj_folder, sid, project_name, project_path):
    """Pager over the session conversation. 'i' toggles metadata header,
    'e' exports to markdown."""
    from .stats import get_session_stats_cached
    from .ui import pager, flash

    jsonl = os.path.join(proj_folder, f"{sid}.jsonl")
    msgs  = iter_transcript(jsonl)
    stats = get_session_stats_cached(jsonl)
    name  = load_name(proj_folder, sid)

    width = max(40, render.content_width() - 6)
    lines = []
    marks = []   # line index of each message start → pager position counter
    for m in msgs:
        ts = ''
        if m['ts']:
            ep = None
            try:
                ep = datetime.fromisoformat(m['ts'].replace('Z', '+00:00')).timestamp()
            except Exception:
                pass
            if ep:
                ts = f"  {C_DIM}{datetime.fromtimestamp(ep).strftime('%H:%M')}{C_RESET}"
        marks.append(len(lines))
        if m['role'] == 'user':
            lines.append(f"{C_ACCENT}▸ You{C_RESET}{ts}")
        else:
            lines.append(f"{C_OK}▸ Claude{C_RESET}{ts}")
        for para in m['text'].split('\n'):
            wrapped = textwrap.wrap(para, width) or ['']
            lines.extend(wrapped)
        lines.append('')

    if not lines:
        lines = [f"{C_DIM}(no conversation text in this session){C_RESET}"]
        marks = []

    show_meta = False
    while True:
        header = metadata_lines(stats, name, sid) if show_meta else None
        key = pager(('CLAUDECTL', project_name, 'TRANSCRIPT'),
                    lines, hint="i info   e export",
                    header_lines=header, extra_keys=('i', 'e'),
                    marks=marks, mark_label='msg')
        if key == 'i':
            show_meta = not show_meta
            continue
        if key == 'e':
            ok, msg = export_transcript(proj_folder, sid, project_path)
            flash(msg, ok=ok, secs=1.2)
            continue
        return


# ── export ───────────────────────────────────────────────────

def _slug(s):
    return re.sub(r'[^A-Za-z0-9_-]+', '-', s).strip('-')[:40] or 'session'


def export_transcript(proj_folder, sid, project_path):
    """Write the session as markdown next to the project. Returns (ok, message)."""
    from .stats import get_session_stats_cached

    jsonl = os.path.join(proj_folder, f"{sid}.jsonl")
    msgs  = iter_transcript(jsonl)
    stats = get_session_stats_cached(jsonl)
    name  = load_name(proj_folder, sid)

    title = name or stats.get('title') or sid[:8]
    out = [f"# Claude session — {title}", '']
    out += ['- ' + render.strip_ansi(l) for l in metadata_lines(stats, name, sid, plain=True)]
    out += ['', '---', '']
    for m in msgs:
        out.append(f"### {'User' if m['role'] == 'user' else 'Assistant'}")
        out.append('')
        out.append(m['text'])
        out.append('')

    fname = f"claude-session-{_slug(title)}.md"
    target_dir = project_path if (project_path and os.path.isdir(project_path)
                                  and os.access(project_path, os.W_OK)) else proj_folder
    fpath = os.path.join(target_dir, fname)
    if os.path.exists(fpath):
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        fpath = os.path.join(target_dir, f"claude-session-{_slug(title)}-{stamp}.md")

    try:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out) + '\n')
    except Exception as e:
        return False, f"Export failed: {e}"
    open_in_editor(fpath)
    return True, f"Exported {os.path.basename(fpath)}"
