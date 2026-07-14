"""Deny-rules generator — hard-block Claude from reading heavy/generated
content via permissions.deny in the PROJECT's .claude/settings.json.

A single stray Read of node_modules/ or a lockfile can burn thousands of
context tokens; permissions.deny enforces the block at the permission layer
(unlike .claudeignore, which is advisory). Only rules for things actually
present in the project are proposed, and the merge never clobbers existing
settings content.
"""

import json
import os

HEAVY_DIRS = ['node_modules', 'dist', 'build', 'out', '.venv', 'venv',
              '__pycache__', 'target', 'vendor', '.next', '.nuxt', 'coverage',
              '.tox', '.mypy_cache', '.pytest_cache', '.gradle', 'Pods']
LOCK_FILES = ['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'Cargo.lock',
              'poetry.lock', 'uv.lock', 'composer.lock', 'Gemfile.lock', 'go.sum']


def _entry_count(path):
    try:
        return len(os.listdir(path))
    except Exception:
        return 0


def scan_heavy(project_path, max_depth=2):
    """[(deny_pattern, why)] for heavy dirs / lock files actually present,
    scanning to `max_depth` levels below the project root (skips .git and
    doesn't descend into heavy dirs themselves)."""
    root = os.path.abspath(project_path or '')
    if not root or not os.path.isdir(root):
        return []
    out, seen = [], set()

    def _rel(p):
        return os.path.relpath(p, root).replace('\\', '/')

    def _walk(path, depth):
        try:
            entries = list(os.scandir(path))
        except OSError:
            return
        for e in entries:
            if e.name == '.git':
                continue
            if e.is_dir(follow_symlinks=False):
                if e.name in HEAVY_DIRS:
                    rel = _rel(e.path)
                    pat = f'Read({rel}/**)'
                    if pat not in seen:
                        seen.add(pat)
                        out.append((pat, f'{rel}/ present, {_entry_count(e.path)} entries'))
                elif depth < max_depth:
                    _walk(e.path, depth + 1)
            elif e.is_file(follow_symlinks=False) and e.name in LOCK_FILES:
                pat = f'Read(**/{e.name})'
                if pat not in seen:
                    seen.add(pat)
                    try:
                        kb = os.path.getsize(e.path) // 1024
                    except OSError:
                        kb = 0
                    out.append((pat, f'{_rel(e.path)} present, {kb} KB'))

    _walk(root, 0)
    return sorted(out)


def merge_deny(project_path, patterns):
    """Merge deny patterns into <project>/.claude/settings.json →
    permissions.deny (create keys as needed, dedupe, preserve everything
    else). Returns (added, already_present)."""
    sp = os.path.join(os.path.abspath(project_path), '.claude', 'settings.json')
    data = {}
    try:
        with open(sp, encoding='utf-8-sig') as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        pass
    perms = data.setdefault('permissions', {})
    deny = perms.setdefault('deny', [])
    if not isinstance(deny, list):
        deny = perms['deny'] = []
    added = existed = 0
    for pat in patterns:
        if pat in deny:
            existed += 1
        else:
            deny.append(pat)
            added += 1
    if added:
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    return added, existed


def deny_rules_screen(project_path, project_name):
    """Preview the proposed deny rules and write them on confirm."""
    from . import render
    from .config import C_DIM, C_RESET, C_OK, C_WARN
    from .ui import wait_event, flash, confirm
    found = scan_heavy(project_path)
    frame = [render.header('CLAUDECTL', project_name, 'DENY RULES'), '',
             f"  {C_DIM}Block Claude from reading heavy/generated content "
             f"(.claude/settings.json → permissions.deny):{C_RESET}", '']
    if not found:
        frame += [f"  {C_OK}Nothing heavy found — no deny rules needed.{C_RESET}", '',
                  render.hint_keys([('ESC', 'back')])]
        render.render_frame(frame)
        while wait_event()[0] not in ('esc', 'enter'):
            pass
        return
    for pat, why in found:
        frame.append(f"  {C_WARN}{pat}{C_RESET}  {C_DIM}— {why}{C_RESET}")
    frame += ['', render.hint_keys([('ENTER', f'write {len(found)} rules'),
                                    ('ESC', 'cancel')])]
    render.render_frame(frame)
    while True:
        ev = wait_event()
        if ev[0] == 'esc':
            return
        if ev[0] == 'enter':
            break
    if not confirm(f"Write {len(found)} deny rules into this project's "
                   f".claude/settings.json?"):
        return
    added, existed = merge_deny(project_path, [p for p, _ in found])
    flash(f"Deny rules: {added} added, {existed} already present", ok=True, secs=1.8)
