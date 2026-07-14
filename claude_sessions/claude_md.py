import os
import re
import json
import time
import queue
import threading
import subprocess
import shutil

from .config import (W, _AUTOGEN_START, _AUTOGEN_END, _SESSIONS_START, _SESSIONS_END,
                     _AI_MARKER, _MEMORY_START, _MEMORY_END)
from .config import get_claude_exe, open_in_editor, config_dir
from .sessions import get_session_info, get_session_rich_summary, read_extra_paths, format_age
from .ui import text_input, _cls, wait_event, poll_event


def write_memory_block(project_path, digest):
    """Insert/replace the CLAUDECTL:MEMORY sentinel block in <project>/CLAUDE.md,
    leaving all other content (user prose, AUTOGEN, SESSIONS, AI marker) intact.
    Returns (ok, old_content, new_content)."""
    md_path = os.path.join(project_path, 'CLAUDE.md')
    old = ''
    if os.path.exists(md_path):
        try:
            old = open(md_path, encoding='utf-8', errors='ignore').read()
        except Exception:
            old = ''
    section = (f"{_MEMORY_START}\n## Project memory (claudectl — auto-maintained)\n"
               f"<!-- Generated from the semantic graph; edits here are overwritten -->\n\n"
               f"{digest}\n{_MEMORY_END}\n")
    if _MEMORY_START in old and _MEMORY_END in old:
        pre = old[:old.index(_MEMORY_START)]
        post = old[old.index(_MEMORY_END) + len(_MEMORY_END):]
        new = pre + section + post
    elif old.strip():
        new = old.rstrip('\n') + '\n\n' + section
    else:
        name = os.path.basename(project_path) or project_path
        new = f"# {name}\n\n{section}"
    if new == old:
        return True, old, new
    try:
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(new)
        return True, old, new
    except Exception:
        return False, old, old


def _valid_claude_md(text):
    """Reject model commentary/refusals masquerading as CLAUDE.md content.
    Real output starts with a markdown title and has structure."""
    t = (text or '').lstrip()
    if not t.startswith('#') or len(t) < 200:
        return False
    if re.match(r"(?i)^\W*(i'll|i will|i've|here(?: is|'s)|sure|okay|certainly|edit pending)", t):
        return False
    return len(re.findall(r'^#{1,3} ', text, re.M)) >= 2


def _preserve_block(final, existing, start=_MEMORY_START, end=_MEMORY_END):
    """Carry a sentinel block from `existing` into `final` verbatim. AI analyze
    rewrites the whole CLAUDE.md and would otherwise drop the machine-maintained
    CLAUDECTL:MEMORY block (it lives after AUTOGEN/SESSIONS, so Claude never
    sees it). Always keep it."""
    if start not in existing or end not in existing:
        return final
    block = existing[existing.index(start):existing.index(end) + len(end)]
    if start in final and end in final:  # drop any stray block the model emitted
        final = final[:final.index(start)] + final[final.index(end) + len(end):]
    return final.rstrip('\n') + '\n\n' + block.rstrip('\n') + '\n'


def resolve_memory_files(project_path):
    """Which CLAUDE.md files load for a project, broadest→narrowest, with
    @import references resolved one level. Returns [(label, path, exists, imports)]."""
    candidates = [
        ('user',          os.path.join(config_dir, 'CLAUDE.md')),
        ('project',       os.path.join(project_path, 'CLAUDE.md')),
        ('project/.claude', os.path.join(project_path, '.claude', 'CLAUDE.md')),
        ('local',         os.path.join(project_path, 'CLAUDE.local.md')),
    ]
    out = []
    for label, path in candidates:
        exists = os.path.isfile(path)
        imports = []
        if exists:
            try:
                text = open(path, encoding='utf-8', errors='ignore').read()
                for m in re.finditer(r'@([^\s]+)', text):
                    ref = m.group(1)
                    rp = os.path.expanduser(ref)
                    if not os.path.isabs(rp):
                        rp = os.path.join(os.path.dirname(path), rp)
                    imports.append((ref, os.path.isfile(rp)))
            except Exception:
                pass
        out.append((label, path, exists, imports))
    return out


def memory_map_menu(project_path, project_name):
    """Show the CLAUDE.md hierarchy for a project; open any file in editor."""
    from .ui import menu, flash
    from . import config as _c
    while True:
        rows = resolve_memory_files(project_path)
        items = []
        for label, path, exists, imports in rows:
            mark = f'{_c.C_OK}●{_c.C_RESET}' if exists else f'{_c.C_DIM}○{_c.C_RESET}'
            imp = (f"  {_c.C_DIM}{len(imports)} @import{_c.C_RESET}" if imports else '')
            items.append((f"{mark} {label:<16} {_c.C_DIM}{path}{_c.C_RESET}{imp}",
                          f'file:{path}' if exists else f'new:{path}'))
        sel = menu(items, f"MEMORY MAP  /  {project_name}")
        if not sel:
            return
        path = sel.split(':', 1)[1]
        if sel.startswith('new:'):
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, 'a', encoding='utf-8').close()
            except Exception as e:
                flash(f"Create failed: {e}", ok=False, secs=1.4)
                continue
        open_in_editor(path)


def find_git_repos(root, max_depth=2):
    """Return list of paths that contain a .git dir, up to max_depth levels deep."""
    repos = []
    # root itself may be a repo
    if os.path.isdir(os.path.join(root, '.git')):
        repos.append(root)
    if max_depth <= 0:
        return repos
    try:
        for entry in sorted(os.scandir(root), key=lambda e: e.name):
            if not entry.is_dir(follow_symlinks=False):
                continue
            sub = entry.path
            if os.path.isdir(os.path.join(sub, '.git')):
                repos.append(sub)
            elif max_depth > 1:
                # one more level
                try:
                    for e2 in sorted(os.scandir(sub), key=lambda e: e.name):
                        if e2.is_dir(follow_symlinks=False) and os.path.isdir(os.path.join(e2.path, '.git')):
                            repos.append(e2.path)
                except PermissionError:
                    pass
    except PermissionError:
        pass
    return repos


def _parse_existing_sessions(text):
    """Extract session entries from SESSIONS block. Returns dict {sid_prefix: line}."""
    entries = {}
    if _SESSIONS_START not in text or _SESSIONS_END not in text:
        return entries
    block = text[text.index(_SESSIONS_START) + len(_SESSIONS_START):text.index(_SESSIONS_END)]
    for line in block.splitlines():
        line = line.strip()
        if line.startswith('- '):
            m = re.search(r'\*\*(.+?)\*\*', line)
            key = m.group(1) if m else line[:20]
            entries[key] = line
    return entries


def _build_sessions_block(proj_folder, existing_entries, cap=None):
    """Merge fresh session scan with existing entries. Returns full sessions
    block text. Kept to the most recent `cap` entries (claude_md_sessions_cap,
    0 = unlimited) — CLAUDE.md is loaded on EVERY turn of every session, so an
    unbounded session log is a permanent per-message token tax."""
    if cap is None:
        try:
            from .config import load_settings
            cap = load_settings().get('claude_md_sessions_cap', 10)
        except Exception:
            cap = 10
    if not proj_folder or not os.path.isdir(proj_folder):
        if existing_entries:
            kept = list(existing_entries.values())
            kept = kept[:cap] if cap else kept
            return "## Session topics\n" + '\n'.join(kept) + "\n\n"
        return ''

    merged = dict(existing_entries)  # key -> line, preserves all old entries

    # every account's sessions for this project, newest first (same encoded
    # folder name under each account's projects dir)
    from .sessions import project_session_folders
    jsonl_paths, _seen_sids = [], set()
    for folder in project_session_folders(proj_folder):
        try:
            for f in os.listdir(folder):
                if f.endswith('.jsonl') and f[:-6] not in _seen_sids:
                    _seen_sids.add(f[:-6])
                    jsonl_paths.append(os.path.join(folder, f))
        except Exception:
            continue
    try:
        jsonl_paths.sort(key=os.path.getmtime, reverse=True)
    except Exception:
        pass

    new_keys = []
    for jpath in jsonl_paths:
        jf = os.path.basename(jpath)
        sid = jf[:-6]
        name_file = os.path.join(os.path.dirname(jpath), sid + '.name')
        sess_name = ''
        if os.path.exists(name_file):
            try:
                sess_name = open(name_file, encoding='utf-8').read().strip()
            except Exception:
                pass
        preview, count = get_session_info(jpath)
        if not preview:
            continue
        key = sess_name if sess_name else sid[:8]
        line = f"- **{key}** ({count} msgs): {preview[:120]}"
        if key not in merged:
            new_keys.append(key)
        merged[key] = line  # always update count/preview for existing keys

    if not merged:
        return ''

    # New sessions first, then older ones (preserving existing order for old entries)
    old_keys = [k for k in existing_entries if k not in new_keys]
    ordered = new_keys + old_keys
    # any keys in merged not yet ordered (edge case)
    for k in merged:
        if k not in ordered:
            ordered.append(k)
    if cap:
        ordered = ordered[:cap]              # newest survive, oldest drop

    return "## Session topics\n" + '\n'.join(merged[k] for k in ordered) + "\n\n"


def _build_autogen_block(project_path, proj_folder, commits=None):
    """Build repos/commits/READMEs block (always refreshed)."""
    if commits is None:
        try:
            from .config import load_settings
            commits = load_settings().get('claude_md_commits', 7)
        except Exception:
            commits = 7
    commits = int(commits or 7)
    block = ''

    extra_paths = read_extra_paths(proj_folder)
    search_roots = [(project_path, None)]
    for ep in extra_paths:
        if os.path.normcase(ep) != os.path.normcase(project_path):
            search_roots.append((ep, ep))

    seen_repos = set()
    all_repos = []
    for root, label_override in search_roots:
        for repo in find_git_repos(root, max_depth=2):
            key = os.path.normcase(repo)
            if key not in seen_repos:
                seen_repos.add(key)
                all_repos.append((repo, root, label_override))

    repos = [r for r, _, _ in all_repos]

    for repo, base_root, label_override in all_repos:
        rel = os.path.relpath(repo, base_root)
        if label_override:
            label = os.path.join(os.path.basename(label_override), rel) if rel != '.' else os.path.basename(label_override)
        else:
            label = '.' if rel == '.' else rel
        try:
            ru = subprocess.run(['git', '-C', repo, 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, timeout=5)
            origin = ru.stdout.strip() if ru.returncode == 0 else ''
        except Exception:
            origin = ''
        block += f"## Repo: {label}" + (f"  ({origin})" if origin else "") + "\n"
        try:
            r = subprocess.run(['git', '-C', repo, 'log', '--oneline', f'-{commits}'],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                block += f"```\n{r.stdout.strip()}\n```\n"
        except Exception:
            pass
        for readme in ['README.md', 'readme.md', 'README.txt']:
            rp = os.path.join(repo, readme)
            if os.path.exists(rp):
                try:
                    with open(rp, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()[:15]
                    block += "\n**README:**\n" + ''.join(lines) + "\n"
                    break
                except Exception:
                    pass
        block += "\n"

    if not repos:
        try:
            items = sorted(os.listdir(project_path))[:25]
            block += "## Root structure\n```\n" + '\n'.join(items) + "\n```\n\n"
        except Exception:
            pass

    return block


def replace_machine_blocks(existing, new_autogen, new_sessions):
    """Swap the AUTOGEN + SESSIONS sentinel blocks inside `existing`, appending
    any that are missing. `new_autogen`/`new_sessions` are full blocks INCLUDING
    sentinels ('' skips sessions). Returns the new full text."""
    if _AUTOGEN_START in existing and _AUTOGEN_END in existing:
        pre  = existing[:existing.index(_AUTOGEN_START)]
        post = existing[existing.index(_AUTOGEN_END) + len(_AUTOGEN_END):]
    else:
        pre  = existing.rstrip('\n') + '\n\n'
        post = ''
    if _SESSIONS_START in post and _SESSIONS_END in post:
        post = (post[:post.index(_SESSIONS_START)]
                + (new_sessions or '')
                + post[post.index(_SESSIONS_END) + len(_SESSIONS_END):])
    elif new_sessions:
        post = (post.rstrip('\n') + '\n\n' + new_sessions) if post.strip() else new_sessions
    return pre + new_autogen + post


def prune_claude_md(project_path, proj_folder=None):
    """Rebuild the AUTOGEN + SESSIONS blocks with the configured caps, WITHOUT
    opening an editor — the audit screen's one-key fix for a CLAUDE.md whose
    session log grew unbounded. Returns (old_tokens, new_tokens) or None if
    there is no CLAUDE.md."""
    from .memory import tokens_estimate
    md_path = os.path.join(project_path, 'CLAUDE.md')
    if not os.path.isfile(md_path):
        return None
    try:
        existing = open(md_path, 'r', encoding='utf-8', errors='ignore').read()
    except Exception:
        return None

    autogen_content = _build_autogen_block(project_path, proj_folder)
    new_autogen = f"{_AUTOGEN_START}\n{autogen_content}{_AUTOGEN_END}\n"
    sessions_content = _build_sessions_block(proj_folder, _parse_existing_sessions(existing))
    new_sessions = (f"{_SESSIONS_START}\n{sessions_content}{_SESSIONS_END}\n"
                    if sessions_content else '')
    final = replace_machine_blocks(existing, new_autogen, new_sessions)
    if final == existing:
        return (tokens_estimate(existing), tokens_estimate(existing))
    try:
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(final)
    except Exception:
        return None
    try:
        from . import diffview
        diffview.record(project_path, proj_folder, 'claude_md', existing, final)
    except Exception:
        pass
    return (tokens_estimate(existing), tokens_estimate(final))


def _valid_compressed(text):
    """Relaxed validator for a COMPRESSED CLAUDE.md — _valid_claude_md's
    200-char / 2-heading floor would reject a legitimately lean file."""
    t = (text or '').lstrip()
    if not t.startswith('#') or len(t) < 80:
        return False
    return not re.match(
        r"(?i)^\W*(i'll|i will|i've|here(?: is|'s)|sure|okay|certainly|edit pending)", t)


def ai_compress_claude_md(project_path, proj_folder=None):
    """Rewrite the MANUAL content of CLAUDE.md into a lean lookup-table style
    (target < 500 tok) with one Claude call — CLAUDE.md rides in the context on
    every turn of every session, so this is the single highest-leverage token
    cut. Machine blocks (AUTOGEN/SESSIONS/MEMORY) are stripped before the call
    and reassembled verbatim/rebuilt after; the old file is kept as CLAUDE.md.bak.
    Returns True if written."""
    from .memory import _claude_stdin, tokens_estimate
    from .ui import flash
    md_path = os.path.join(project_path, 'CLAUDE.md')
    name = os.path.basename(project_path) or project_path
    try:
        existing = open(md_path, encoding='utf-8', errors='ignore').read()
    except Exception:
        existing = ''
    if not existing.strip():
        flash("No CLAUDE.md to compress — scaffold one first (c)", ok=False, secs=1.6)
        return False

    from .ctxaudit import split_blocks
    manual = split_blocks(existing)['manual']
    prompt = (
        "Compress this CLAUDE.md project-instructions file. It is loaded into the "
        "model's context on EVERY message, so every token counts.\n\n"
        "Rewrite it as a lean lookup table targeting UNDER 500 tokens: dense "
        "one-liners and small tables instead of prose, keep EVERY durable fact, "
        "command, constraint and preference, drop filler, marketing tone, "
        "restatements of things obvious from the code, and meeting-notes-style "
        "history. Keep the # title. Do not invent new facts.\n\n"
        "Output ONLY the raw markdown of the compressed file — no preamble, no "
        "code fences, no commentary.\n\n"
        f"FILE:\n{manual}"
    )
    out = _claude_stdin(prompt, os.path.abspath(project_path),
                        crumbs=('CLAUDECTL', 'COMPRESS', name),
                        label='Compressing CLAUDE.md...')
    compressed = (out or '').strip()
    if compressed.startswith('```'):
        compressed = re.sub(r'^```[a-z]*\n?|```\s*$', '', compressed).strip()
    if not _valid_compressed(compressed):
        flash("Compression failed (empty/invalid output) — CLAUDE.md untouched",
              ok=False, secs=2)
        return False

    autogen_content = _build_autogen_block(project_path, proj_folder)
    new_autogen = f"{_AUTOGEN_START}\n{autogen_content}{_AUTOGEN_END}\n"
    sessions_content = _build_sessions_block(proj_folder, _parse_existing_sessions(existing))
    new_sessions = (f"{_SESSIONS_START}\n{sessions_content}{_SESSIONS_END}\n"
                    if sessions_content else '')
    final = replace_machine_blocks(compressed.rstrip('\n') + '\n',
                                   new_autogen, new_sessions)
    final = _preserve_block(final, existing)
    if _AI_MARKER in existing and _AI_MARKER not in final:
        lines = final.split('\n')
        insert_at = 1
        for i, ln in enumerate(lines[:5]):
            if ln.strip().startswith('# '):
                insert_at = i + 1
                break
        lines.insert(insert_at, _AI_MARKER)
        final = '\n'.join(lines)

    old_tok, new_tok = tokens_estimate(existing), tokens_estimate(final)
    from . import diffview
    if not diffview.confirm(existing, final,
                            f"COMPRESS {old_tok}→{new_tok} tok  /  {name}"):
        flash("Rejected — CLAUDE.md not written", ok=False, secs=1.4)
        return False
    try:
        with open(md_path + '.bak', 'w', encoding='utf-8') as f:
            f.write(existing)                    # backup BEFORE overwriting
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(final)
    except Exception as e:
        flash(f"Write failed: {e}", ok=False, secs=2)
        return False
    try:
        diffview.record(project_path, proj_folder, 'claude_md', existing, final)
    except Exception:
        pass
    try:
        from . import workspace
        workspace.update_manifest(project_path, proj_folder, 'compress')
    except Exception:
        pass
    flash(f"CLAUDE.md compressed: ~{old_tok} → ~{new_tok} tok (backup: CLAUDE.md.bak)",
          ok=True, secs=2)
    return True


def _build_ai_context(project_path, proj_folder):
    """Build rich plaintext context for AI prompt: tree, git repos, extra paths, sessions."""
    SKIP_DIRS = {'.git', 'node_modules', '__pycache__', 'venv', '.venv',
                 '.tox', 'dist', 'build', '.mypy_cache', '.pytest_cache'}
    parts = []

    # 1. Directory tree (2 levels, skip noise)
    parts.append("=== DIRECTORY STRUCTURE ===")
    try:
        def _tree(path, depth):
            if depth > 2:
                return
            try:
                entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name))[:20]
            except (PermissionError, OSError):
                return
            for entry in entries:
                indent = '  ' * depth
                parts.append(f"{indent}{entry.name}{'/' if entry.is_dir() else ''}")
                if entry.is_dir() and entry.name not in SKIP_DIRS and depth < 2:
                    _tree(entry.path, depth + 1)
        _tree(project_path, 0)
    except Exception as e:
        parts.append(f"(error: {e})")

    # 2. Git repos across project + extra paths
    extra_paths = read_extra_paths(proj_folder) if proj_folder else []
    search_roots = [(project_path, None)]
    for ep in extra_paths:
        if os.path.normcase(ep) != os.path.normcase(project_path):
            search_roots.append((ep, ep))

    seen_repos = set()
    for root, label in search_roots:
        root_label = label or project_path
        repos = find_git_repos(root, max_depth=2)
        if repos:
            parts.append(f"\n=== GIT REPOS ({root_label}) ===")
        for repo in repos:
            key = os.path.normcase(repo)
            if key in seen_repos:
                continue
            seen_repos.add(key)
            rel = os.path.relpath(repo, root)
            parts.append(f"\nRepo: {rel if rel != '.' else os.path.basename(repo)}")
            try:
                r = subprocess.run(['git', '-C', repo, 'remote', 'get-url', 'origin'],
                                   capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and r.stdout.strip():
                    parts.append(f"Origin: {r.stdout.strip()}")
            except Exception:
                pass
            try:
                r = subprocess.run(['git', '-C', repo, 'branch', '--show-current'],
                                   capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and r.stdout.strip():
                    parts.append(f"Branch: {r.stdout.strip()}")
            except Exception:
                pass
            try:
                r = subprocess.run(['git', '-C', repo, 'log', '--oneline', '-15'],
                                   capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and r.stdout.strip():
                    parts.append(f"Recent commits:\n{r.stdout.strip()}")
            except Exception:
                pass
            for readme in ['README.md', 'readme.md', 'README.txt', 'README.rst']:
                rp = os.path.join(repo, readme)
                if os.path.exists(rp):
                    try:
                        with open(rp, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read(3000)
                        parts.append(f"README ({readme}):\n{content}")
                        break
                    except Exception:
                        pass

    # 3. Extra linked paths detail
    if extra_paths:
        parts.append("\n=== EXTRA LINKED PATHS ===")
        for ep in extra_paths:
            parts.append(f"\nLinked path: {ep}")
            try:
                items = sorted(os.listdir(ep))[:30]
                parts.append(f"Contents: {', '.join(items)}")
            except Exception:
                parts.append("(unreadable)")

    # 4. Session history (last 10 sessions, top 5 user msgs each)
    if proj_folder and os.path.isdir(proj_folder):
        parts.append("\n=== SESSION HISTORY ===")
        try:
            jsonl_files = sorted(
                [f for f in os.listdir(proj_folder) if f.endswith('.jsonl')],
                key=lambda f: os.path.getmtime(os.path.join(proj_folder, f)),
                reverse=True
            )[:10]
        except Exception:
            jsonl_files = []
        for jf in jsonl_files:
            jpath = os.path.join(proj_folder, jf)
            sid = jf[:-6]
            name = ''
            name_path = os.path.join(proj_folder, sid + '.name')
            if os.path.exists(name_path):
                try:
                    name = open(name_path, encoding='utf-8').read().strip()
                except Exception:
                    pass
            ai_title, user_msgs = get_session_rich_summary(jpath)
            label = name or ai_title or sid[:8]
            if user_msgs:
                parts.append(f"\nSession [{label}]:")
                for msg in user_msgs[:5]:
                    parts.append(f"  - {msg[:150]}")

    return '\n'.join(parts)


def _pager_confirm(title, content):
    """Review content in a flicker-free scrollable pager.
    Returns True=approve, False=reject."""
    from .ui import flush_input, wait_event, poll_event
    from . import render

    flush_input()   # discard keys buffered during generation
    lines = content.splitlines()
    top = 0
    pending = None
    while True:
        page = max(4, render.frame_height() - 6)
        top = max(0, min(top, max(0, len(lines) - page)))
        at_end = top + page >= len(lines)

        frame = [render.header('CLAUDECTL', title, 'REVIEW'), '']
        for ln in lines[top:top + page]:
            frame.append(render.fit('  ' + ln, render.content_width()))
        frame.append('')
        frame.append(render.hint_keys(
            [('↑↓', 'scroll'), ('←→/SPACE', 'page'),
             ('ENTER', 'approve & write'), ('ESC', 'reject')],
            prefix=f"{min(top + page, len(lines))}/{len(lines)}"
                   + ("  (end)" if at_end else "")))
        render.render_frame(frame)

        ev = pending if pending else wait_event()
        pending = None
        if ev[0] == 'up':
            top -= 1
        elif ev[0] == 'down':
            top += 1
        elif ev[0] in ('left', 'back'):
            top -= page
        elif ev[0] == 'right':
            top += page
        elif ev[0] == 'char' and ev[1] == ' ':
            top += page
        elif ev[0] == 'enter':
            return True
        elif ev[0] == 'esc':
            return False
        # coalesce queued scroll repeats; other events carry to next iteration
        while True:
            nxt = poll_event()
            if not nxt:
                break
            if nxt[0] == 'up':
                top -= 1
            elif nxt[0] == 'down':
                top += 1
            else:
                pending = nxt
                break


def ai_scaffold_claude_md(project_path, proj_folder=None):
    """Use Claude CLI (-p) to deeply analyze project and generate comprehensive CLAUDE.md."""
    md_path = os.path.join(project_path, 'CLAUDE.md')
    name = os.path.basename(project_path) or project_path

    # Read existing CLAUDE.md.
    # Update mode = any existing content (not just files with AI marker).
    # This ensures we NEVER rewrite a file the user has edited manually.
    existing_for_sessions = ''
    is_update = False
    existing_ai_sections = ''
    if os.path.exists(md_path):
        try:
            existing_for_sessions = open(md_path, 'r', encoding='utf-8', errors='ignore').read()
            if existing_for_sessions.strip():
                is_update = True
                # Extract content before AUTOGEN block to pass to Claude for updating
                cut = (existing_for_sessions.index(_AUTOGEN_START)
                       if _AUTOGEN_START in existing_for_sessions
                       else len(existing_for_sessions))
                existing_ai_sections = (existing_for_sessions[:cut]
                                        .replace(_AI_MARKER + '\n', '')
                                        .replace(_AI_MARKER, '')
                                        .strip())
        except Exception:
            pass

    # ── Confirmation screen ──────────────────────────────────────
    _cls()
    print(f"\n  AI ANALYZE  /  {name}\n")
    if is_update:
        md_age = format_age(os.path.getmtime(md_path))
        print(f"  Mode    : UPDATE  (existing file, last modified {md_age} ago)")
        print(f"  Existing content preserved. Only outdated facts updated.")
    else:
        print(f"  Mode    : FRESH  (no existing CLAUDE.md)")
        print(f"  Will generate full structured CLAUDE.md from project data.")
    print(f"\n  ENTER start   ESC cancel\n")

    # ── Optional extra prompt ────────────────────────────────────
    extra_prompt = ''
    while True:
        ev = wait_event()
        if ev[0] == 'enter':   # show extra prompt input
            _cls()
            print(f"\n  AI ANALYZE  /  {name}\n")
            print(f"  Optional: add extra instructions for Claude (ENTER to skip)\n")
            print(f"  Example: 'focus on API endpoints' / 'add client-facing language rules'\n")
            result = text_input("Extra instructions:", default='')
            if result is None:  # ESC — cancel
                return
            extra_prompt = result
            break
        elif ev[0] == 'esc':
            return
        # ignore any other key — require explicit ENTER

    # Gather context
    _cls()
    print(f"\n  AI ANALYZE  /  {name}\n")
    print(f"  Gathering project context...", flush=True)
    context = _build_ai_context(project_path, proj_folder)

    # Build prompt — update vs fresh
    _EXTRA = (f"\n\nADDITIONAL INSTRUCTIONS FROM USER:\n{extra_prompt}\n" if extra_prompt else '')
    _TAIL = (
        f"End the file with EXACTLY these four lines (no extra text after):\n"
        f"<!-- AUTOGEN:START -->\n"
        f"<!-- AUTOGEN:END -->\n"
        f"<!-- SESSIONS:START -->\n"
        f"<!-- SESSIONS:END -->\n\n"
        f"CRITICAL: You are running in non-interactive print mode. A script reads your stdout.\n"
        f"DO NOT use any tools (Write, Edit, Bash, or any other tool).\n"
        f"DO NOT write or create any files.\n"
        f"Output ONLY the raw CLAUDE.md text directly. No preamble, no code fences, no commentary."
    )

    if is_update:
        prompt = (
            f"Update the existing CLAUDE.md for project '{name}'.\n\n"
            f"STRICT RULES:\n"
            f"- Preserve ALL existing sections and their content verbatim unless factually wrong\n"
            f"- Do NOT remove any section, even custom user-written ones\n"
            f"- Do NOT rewrite sections that are still accurate\n"
            f"- Only update specific facts that have clearly changed (new deps, new files, etc.)\n"
            f"- Add new sections ONLY if clearly missing critical information\n"
            f"- Preserve all user comments, notes, and custom content exactly\n\n"
            f"EXISTING CLAUDE.MD (treat as ground truth — modify minimally):\n{existing_ai_sections}\n\n"
            f"NEW PROJECT DATA (use ONLY to correct outdated facts):\n{context}\n\n"
            f"Output the complete CLAUDE.md with minimal changes from the existing version.\n"
            + _EXTRA + _TAIL
        )
    else:
        prompt = (
            f"Analyze this software project and write a comprehensive CLAUDE.md file.\n"
            f"This file is context for future Claude Code sessions — be accurate and specific.\n\n"
            f"PROJECT NAME: {name}\n"
            f"PROJECT PATH: {project_path}\n\n"
            f"PROJECT DATA:\n{context}\n\n"
            f"Write CLAUDE.md with ONLY these sections (omit any section if truly not applicable):\n\n"
            f"# {name}\n\n"
            f"## Project context\n"
            f"[What this project does, its purpose, current state]\n\n"
            f"## Tech stack\n"
            f"[Languages, frameworks, key libraries with versions if visible]\n\n"
            f"## Architecture\n"
            f"[Key components, how they interact, data flow]\n\n"
            f"## Key files\n"
            f"[Most important files/dirs and what they do]\n\n"
            f"## Development workflow\n"
            f"[How to build, run, test, deploy]\n\n"
            f"## Important notes\n"
            f"[Gotchas, special setup, known issues, conventions]\n\n"
            f"# Compact instructions\n"
            f"[One short paragraph steering conversation compaction: what to "
            f"preserve (current task state, recent code changes, test results) "
            f"and what to drop (exploration dead-ends, old tool output)]\n\n"
            + _EXTRA + _TAIL
        )

    # Locate claude.exe
    claude_exe = get_claude_exe()
    if not claude_exe:
        print(f"\n  claude.exe not found (checked %USERPROFILE%\\.local\\bin and PATH)")
        print(f"  Falling back to standard scaffold...")
        time.sleep(2)
        scaffold_claude_md(project_path, proj_folder)
        return

    stderr_lines = []
    cancelled = False
    ai_content = ''
    start_t = time.time()

    try:
        # Prompt goes via stdin, NOT argv: a large project context (e.g. a
        # folder of many repos) overruns the Windows command-line limit
        # (~32KB) → [WinError 206]. `claude -p` with no positional prompt
        # reads the prompt from stdin.
        proc = subprocess.Popen(
            [claude_exe, '-p', '--output-format', 'stream-json', '--verbose', '--allowedTools', ''],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='ignore',
            cwd=project_path
        )

        # Writer thread: feed the prompt and close stdin. Threaded so a prompt
        # larger than the pipe buffer can't deadlock against our stdout reads.
        def _write_stdin():
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except Exception:
                pass
        threading.Thread(target=_write_stdin, daemon=True).start()

        # Reader thread feeds raw stdout lines into a queue
        line_q = queue.Queue()
        def _read_stdout():
            for ln in iter(proc.stdout.readline, ''):
                line_q.put(ln)
            line_q.put(None)  # sentinel
        def _read_stderr():
            for ln in iter(proc.stderr.readline, ''):
                stderr_lines.append(ln)

        threading.Thread(target=_read_stdout, daemon=True).start()
        threading.Thread(target=_read_stderr, daemon=True).start()

        # Collect ALL raw events to a log so we can inspect structure if needed
        _dbg_log = os.path.join(os.environ['TEMP'], 'ai_analyze_debug.jsonl')
        _dbg_f = open(_dbg_log, 'w', encoding='utf-8')

        printed_len = 0
        result_content = ''
        spin_i = 0
        done = False

        while not done:
            try:
                while True:
                    raw = line_q.get_nowait()
                    if raw is None:
                        done = True
                        break
                    _dbg_f.write(raw if raw.endswith('\n') else raw + '\n')
                    _dbg_f.flush()
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except Exception:
                        continue
                    etype = ev.get('type', '')

                    # Collect candidate text from all known locations
                    candidates = []
                    # ev.message.content[].text  (standard API format)
                    for block in ev.get('message', {}).get('content', []):
                        if isinstance(block, dict) and block.get('type') == 'text':
                            candidates.append(block.get('text', ''))
                    # ev.content[].text
                    for block in ev.get('content', []):
                        if isinstance(block, dict) and block.get('type') == 'text':
                            candidates.append(block.get('text', ''))
                    # ev.delta.text  (streaming delta format)
                    candidates.append(ev.get('delta', {}).get('text', ''))
                    # ev.text  (direct)
                    candidates.append(ev.get('text', ''))
                    # ev.result / ev.output  (result event)
                    candidates.append(ev.get('result', ''))
                    candidates.append(ev.get('output', ''))

                    text = max(candidates, key=len)
                    if not text:
                        continue

                    # The `result` event carries the final answer — it wins
                    # outright (streamed assistant text can include tool chatter
                    # or commentary that previously leaked into the file).
                    if etype == 'result' and ev.get('result'):
                        result_content = ev['result']
                    elif etype in ('assistant', 'text', 'content_block_delta', 'message'):
                        if len(text) > printed_len:
                            printed_len = len(text)
                            ai_content = text
                        elif text and not ai_content:
                            # delta mode — append
                            ai_content += text
                            printed_len = len(ai_content)

            except queue.Empty:
                pass

            if not done:
                # Progress frame: animated bar + elapsed + received size
                from . import render
                from .config import C_DIM, C_RESET
                preview = render.trunc(ai_content.strip().split('\n')[-1] if ai_content else '',
                                       render.content_width() - 6)
                render.render_frame([
                    render.header('CLAUDECTL', name, 'AI ANALYZE'),
                    '',
                    f"  Claude is analyzing the project and writing CLAUDE.md...",
                    '',
                    '  ' + render.progress_bar(spin_i),
                    f"  {C_DIM}{int(time.time() - start_t)}s elapsed · "
                    f"{len(ai_content)} chars received{C_RESET}",
                    '',
                    (f"  {C_DIM}{preview}{C_RESET}" if preview else ''),
                    '',
                    render.hint_keys([('ESC', 'cancel (falls back to standard scaffold)')]),
                ])
                spin_i += 1
                time.sleep(0.1)
                # Drain ALL pending input — wheel/held arrows otherwise pile up
                # and replay into the next screen
                while True:
                    ev = poll_event()
                    if not ev:
                        break
                    if ev[0] == 'esc':
                        proc.terminate()
                        cancelled = True
                        done = True
                        break

        _dbg_f.close()

        proc.wait()

    except Exception as e:
        print(f"\n\n  Error: {e}")
        print(f"  Falling back to standard scaffold...")
        time.sleep(2)
        scaffold_claude_md(project_path, proj_folder)
        return

    if cancelled:
        print(f"\n\n  Cancelled. Falling back to standard scaffold...")
        time.sleep(1)
        scaffold_claude_md(project_path, proj_folder)
        return

    if result_content:
        ai_content = result_content            # final result event wins

    if not ai_content or proc.returncode != 0:
        msg = ''.join(stderr_lines).strip()[:200] if stderr_lines else 'no output'
        print(f"\n\n  AI analysis failed: {msg}")
        print(f"  Falling back to standard scaffold...")
        time.sleep(2)
        scaffold_claude_md(project_path, proj_folder)
        return

    if not _valid_claude_md(ai_content):
        print(f"\n\n  AI output doesn't look like a CLAUDE.md (commentary/refusal detected).")
        print(f"  Falling back to standard scaffold...")
        time.sleep(2)
        scaffold_claude_md(project_path, proj_folder)
        return

    print(f"\n  {'─' * W}\n")

    # Inject mechanical blocks into AUTOGEN/SESSIONS placeholders
    autogen_content = _build_autogen_block(project_path, proj_folder)
    new_autogen = f"{_AUTOGEN_START}\n{autogen_content}{_AUTOGEN_END}\n"

    existing_sessions = _parse_existing_sessions(existing_for_sessions)
    sessions_content = _build_sessions_block(proj_folder, existing_sessions)
    new_sessions = (f"{_SESSIONS_START}\n{sessions_content}{_SESSIONS_END}\n"
                    if sessions_content else f"{_SESSIONS_START}\n{_SESSIONS_END}\n")

    final = replace_machine_blocks(ai_content, new_autogen, new_sessions)

    # Never drop the semantic-memory block — reinject it verbatim from the old file
    final = _preserve_block(final, existing_for_sessions)

    # Preview the DIFF (old → proposed) so the user approves/rejects based on
    # what actually changed. 'f' toggles to the full proposed content.
    from . import diffview
    if not diffview.confirm(existing_for_sessions, final, f"AI ANALYZE  /  {name}"):
        _cls()
        print(f"\n  Rejected — CLAUDE.md not written.\n")
        time.sleep(1)
        return

    # Inject AI marker as second line (after the # title) so future runs detect update mode
    # Only scan first 5 lines to avoid matching '# Heading' inside README content in AUTOGEN block
    lines = final.split('\n')
    insert_at = 1
    for i, ln in enumerate(lines[:5]):
        if ln.strip().startswith('# '):
            insert_at = i + 1
            break
    if _AI_MARKER not in final:
        lines.insert(insert_at, _AI_MARKER)
    final = '\n'.join(lines)

    try:
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(final)
        print(f"  Done — CLAUDE.md {'updated' if is_update else 'written'}.")
        time.sleep(1)
    except Exception as e:
        print(f"\n  Write error: {e}")
        time.sleep(2)
        return

    try:
        from . import workspace
        workspace.update_manifest(project_path, proj_folder, 'ai_analyze', is_update=is_update)
    except Exception:
        pass
    try:
        from . import diffview
        diffview.record(project_path, proj_folder, 'claude_md',
                        existing_for_sessions, final)   # diff already shown pre-write
    except Exception:
        pass
    open_in_editor(md_path)


def scaffold_claude_md(project_path, proj_folder=None):
    md_path = os.path.join(project_path, 'CLAUDE.md')
    name = os.path.basename(project_path) or project_path

    existing = ''
    if os.path.exists(md_path):
        try:
            existing = open(md_path, 'r', encoding='utf-8', errors='ignore').read()
        except Exception:
            pass

    # Build fresh repos/commits/READMEs block
    autogen_content = _build_autogen_block(project_path, proj_folder)
    new_autogen = f"{_AUTOGEN_START}\n{autogen_content}{_AUTOGEN_END}\n"

    # Merge session topics (accumulate, never discard old entries)
    existing_sessions = _parse_existing_sessions(existing)
    sessions_content = _build_sessions_block(proj_folder, existing_sessions)
    new_sessions = f"{_SESSIONS_START}\n{sessions_content}{_SESSIONS_END}\n" if sessions_content else ''

    if existing:
        final = replace_machine_blocks(existing, new_autogen, new_sessions)
    else:
        # First time
        final = (f"# {name}\n\n"
                 f"## Project context\n"
                 f"<!-- Edit this section freely — it will never be overwritten -->\n\n"
                 f"# Compact instructions\n\n"
                 f"When compacting, preserve: the current task and its state, recent "
                 f"code changes with file paths, test results, and decisions made. "
                 f"Drop: exploration dead-ends, old tool output, and resolved errors.\n\n"
                 + new_autogen
                 + ('\n' + new_sessions if new_sessions else ''))

    try:
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(final)
    except Exception:
        return
    try:
        from . import workspace
        workspace.update_manifest(project_path, proj_folder, 'scaffold')
    except Exception:
        pass
    try:
        from . import diffview
        diffview.record(project_path, proj_folder, 'claude_md', existing, final)
    except Exception:
        pass
    open_in_editor(md_path)


