"""Project brief — instant, local (no Claude call) situational awareness:
  • work_suggestions: ranked next-steps from lessons, graph importance, health.
  • session_diff: what changed since the last session (git + session-log).
Both are token-frugal and automatic; surfaced in the memory hub.
"""

import os
import subprocess

from . import memory


def work_suggestions(project_path, proj_folder):
    """[(priority_str, text)] ranked next-steps. Pure local."""
    out = []
    mem = memory.load_memory(project_path, proj_folder)

    # 1. unresolved error_fix lessons → likely recurring pain
    fixes = [e for e in mem.get('entities', [])
             if e.get('type') == 'lesson' and e.get('kind') == 'error_fix'
             and e.get('status') in ('approved', 'pinned')]
    for l in fixes[:3]:
        out.append(('fix', f"recurring issue: {l.get('summary', l.get('name', ''))}"))

    # 2. pending lessons awaiting review
    try:
        from . import lessons
        pend = lessons.pending_sids(proj_folder, mem)
        if pend:
            out.append(('learn', f"{len(pend)} session(s) not yet learned — press L"))
    except Exception:
        pass

    # 3. most-connected modules (graph rank) → the project's backbone
    mods = {}
    for e in mem.get('entities', []):
        if e.get('type') == 'lesson':
            continue
        u = f"{e.get('repo')}/{e.get('module')}"
        mods[u] = max(mods.get(u, 0), e.get('rank', 0))
    top = sorted(mods.items(), key=lambda kv: -kv[1])[:3]
    for u, r in top:
        if r > 0:
            out.append(('core', f"central module: {u} (most-connected)"))

    # 4. open health issues
    try:
        from . import health
        for sev, msg, _hint in health.check_project(project_path, proj_folder):
            if sev == 'warn':
                out.append(('health', msg))
    except Exception:
        pass

    if not out:
        out.append(('info', 'no signals yet — build memory (m→b) and run a session'))
    return out


def _last_session_stamp(project_path):
    """ISO-ish date of the most recent session-log entry, or ''."""
    from .health import SESSION_LOG
    p = os.path.join(project_path, SESSION_LOG)
    if not os.path.isfile(p):
        return ''
    try:
        for line in reversed(open(p, encoding='utf-8', errors='ignore').read().splitlines()):
            if line.startswith('## '):
                # "## 2026-07-05 14:30 — <sid>"
                return line[3:].split('—')[0].strip().split()[0]
    except Exception:
        pass
    return ''


def _git_repos(project_path, proj_folder):
    """Every git repo for this project: the root if it's one, plus any nested
    repos (a workspace often holds several sub-project repos, none at the root)."""
    repos = []
    if os.path.isdir(os.path.join(project_path, '.git')):
        repos.append(project_path)
    try:
        from . import connections
        for r in connections._discover_repos(os.path.abspath(project_path), proj_folder):
            if os.path.isdir(os.path.join(r, '.git')) and r not in repos:
                repos.append(r)
    except Exception:
        pass
    return repos


def session_diff(project_path, proj_folder):
    """'Since last session': git commits + changed-file stat per repo since the
    last session-log stamp. Handles sub-project repos (commits may live in
    subdirs, not the project root). Pure local."""
    since = _last_session_stamp(project_path)
    repos = _git_repos(project_path, proj_folder)
    if not repos:
        return ['(no git repo here or in sub-projects — nothing to diff)']

    def _git(cwd, args):
        try:
            r = subprocess.run(['git', *args], cwd=cwd,
                               capture_output=True, text=True, timeout=8)
            return r.stdout.strip() if r.returncode == 0 else ''
        except Exception:
            return ''

    root = os.path.abspath(project_path)
    lines = []
    for repo in repos:
        label = os.path.basename(repo.rstrip(os.sep)) or repo
        log_args = ['log', '--oneline', f'--since={since}'] if since else ['log', '--oneline', '-10']
        commits = _git(repo, log_args)
        dirty = _git(repo, ['status', '--porcelain'])
        if not commits and not dirty:
            continue                                 # quiet repo — skip
        header = f"▸ {label}" if len(repos) > 1 or repo != root else "commits:"
        lines.append(header)
        if commits:
            for c in commits.splitlines()[:10]:
                lines.append('  ' + c)
        if dirty:
            lines.append(f"  ({len(dirty.splitlines())} uncommitted file(s))")
        lines.append('')
    if not lines:
        return [f"(no changes since {since or 'the last session'})"]
    return lines
