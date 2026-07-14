"""Workspace provenance & freshness.

Records where a project's generated context came from (CLAUDE.md, MCP docs,
sessions, repo state) and whether it is still valid. Written after scaffold,
AI-analyze, MCP discovery, and launch ops into <project>/.claudectl/
workspace-manifest.json (falling back to the encoded ~/.claude/projects folder
when the working dir is gone or read-only). Surfaced via `claudectl workspace
status` and the sessions-menu `w` screen.

The manifest is schema-versioned: _migrate() fills missing keys and preserves
unknown ones, so old files load and future fields survive round-trips. Every
write is best-effort — a manifest failure must never block the operation that
triggered it.
"""

import os
import json
import time
import hashlib
import subprocess
from datetime import datetime, timezone

from . import config as _c
from . import render

SCHEMA_VERSION = 1
MANIFEST_DIR = '.claudectl'
MANIFEST_NAME = 'workspace-manifest.json'
IMPORTANT_FILES = ['CLAUDE.md', 'README.md', '.mcp.json', 'pyproject.toml', 'package.json']

# check name -> weight (freshness score contribution when fresh)
_WEIGHTS = {
    'manifest': 5, 'claude_md': 25, 'claude_md_fresh': 25,
    'mcp_docs': 15, 'repo': 10, 'sessions': 10, 'conflicts': 10,
}


# ── low-level helpers ────────────────────────────────────────

def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _git_head(path):
    """(full_sha, short_sha, branch) for the repo at path, or ('','','')."""
    if not path or not os.path.isdir(os.path.join(path, '.git')):
        # still try rev-parse — path may be inside a worktree/subdir of a repo
        pass
    try:
        sha = subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if not sha:
            return ('', '', '')
        br = subprocess.run(['git', '-C', path, 'branch', '--show-current'],
                            capture_output=True, text=True, timeout=5).stdout.strip()
        return (sha, sha[:7], br)
    except Exception:
        return ('', '', '')


def _sha256_file(path):
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ''


def _file_meta(path):
    if not path or not os.path.isfile(path):
        return {'exists': False, 'sha256': '', 'size': 0, 'mtime': 0}
    try:
        st = os.stat(path)
        return {'exists': True, 'sha256': _sha256_file(path),
                'size': st.st_size, 'mtime': st.st_mtime}
    except Exception:
        return {'exists': False, 'sha256': '', 'size': 0, 'mtime': 0}


def _count_tools(md):
    """Heuristic tool count from analyze_mcp_tools markdown."""
    if not md:
        return 0
    rows = 0
    for ln in md.splitlines():
        s = ln.strip()
        if s.startswith('|') and '---' not in s and not s.lower().startswith('| tool'):
            rows += 1
        elif s.startswith(('### ', '- `', '* `')):
            rows += 1
    return rows


# ── persistence ──────────────────────────────────────────────

def _candidate_paths(project_path, proj_folder):
    out = []
    if project_path:
        out.append(os.path.join(project_path, MANIFEST_DIR, MANIFEST_NAME))
    if proj_folder:
        out.append(os.path.join(proj_folder, MANIFEST_DIR, MANIFEST_NAME))
    return out


def _empty_manifest():
    return {
        'schema_version': SCHEMA_VERSION,
        'generated_at': '',
        'project_path': '',
        'repo': {'head_sha': '', 'head_short': '', 'branch': ''},
        'source_inputs': [],
        'file_hashes': {},
        'sessions': {'analyzed_count': 0, 'first_ts': 0, 'last_ts': 0, 'range_days': 0},
        'claude_md_files': [],
        'mcp': {'count': 0, 'servers': []},
        'operations': {},
        'validation': {'checks': [], 'stale': [], 'conflicts': []},
        'freshness_score': 0,
        'safe_to_launch': True,
    }


def _migrate(m):
    """Fill missing keys from the empty template; preserve unknown keys."""
    base = _empty_manifest()
    for k, v in base.items():
        if k not in m:
            m[k] = v
        elif isinstance(v, dict) and isinstance(m.get(k), dict):
            for kk, vv in v.items():
                m[k].setdefault(kk, vv)
    m['schema_version'] = SCHEMA_VERSION
    return m


def load_manifest(project_path, proj_folder=None):
    """Load the manifest (first existing candidate). Missing → empty;
    corrupt → empty with a sentinel flag so status can show 🔴."""
    for p in _candidate_paths(project_path, proj_folder):
        if os.path.isfile(p):
            try:
                with open(p, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return _migrate(data)
                return _corrupt()
            except Exception:
                return _corrupt()
    return _empty_manifest()


def _corrupt():
    m = _empty_manifest()
    m['_corrupt'] = True
    return m


def save_manifest(project_path, m, proj_folder=None):
    """Write to the first writable candidate location. Returns True on success."""
    for p in _candidate_paths(project_path, proj_folder):
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(m, f, indent=2)
            return True
        except Exception:
            continue
    return False


# ── refresh / update ─────────────────────────────────────────

def _gather_live(project_path, proj_folder):
    """Cheaply collect the current observable workspace facts."""
    from .claude_md import resolve_memory_files
    from .sessions import scan_sessions

    sha, short, branch = _git_head(project_path) if project_path else ('', '', '')

    file_hashes = {}
    for fn in IMPORTANT_FILES:
        if project_path:
            file_hashes[fn] = _file_meta(os.path.join(project_path, fn))

    claude_md_files = []
    if project_path:
        for label, path, exists, _imports in resolve_memory_files(project_path):
            claude_md_files.append({
                'label': label, 'path': path, 'exists': exists,
                'sha256': _sha256_file(path) if exists else '',
            })

    sess = {'analyzed_count': 0, 'first_ts': 0, 'last_ts': 0, 'range_days': 0}
    rows = []
    if proj_folder:
        from .sessions import project_session_folders
        seen_sids = set()
        for folder in project_session_folders(proj_folder):
            for r in scan_sessions(folder):
                if r[1] not in seen_sids:          # dedup by sid across accounts
                    seen_sids.add(r[1])
                    rows.append(r)
        rows.sort(key=lambda r: r[0], reverse=True)
    if rows:
        last_ts, first_ts = rows[0][0], rows[-1][0]
        sess = {'analyzed_count': len(rows), 'first_ts': first_ts, 'last_ts': last_ts,
                'range_days': round(max(0.0, last_ts - first_ts) / 86400, 1)}

    # MCP docs live in the global CLAUDE.md (per-server sentinel sections).
    # Freshness = each live server has a section there; tool counts parsed from it.
    servers = []
    try:
        from . import mcp
        cur = mcp.mcp_servers or mcp.get_mcp_status()
        gtext = ''
        try:
            if os.path.isfile(_c.global_claude_md):
                gtext = open(_c.global_claude_md, encoding='utf-8', errors='ignore').read()
        except Exception:
            pass
        for n, s in cur:
            start, end = f'<!-- MCP:{n}:START -->', f'<!-- MCP:{n}:END -->'
            documented = start in gtext and end in gtext
            tool_count = 0
            if documented:
                seg = gtext[gtext.index(start) + len(start):gtext.index(end)]
                tool_count = _count_tools(seg)
            servers.append({'name': n, 'status': s,
                            'documented': documented, 'tool_count': tool_count})
    except Exception:
        pass

    return {
        'repo': {'head_sha': sha, 'head_short': short, 'branch': branch},
        'file_hashes': file_hashes,
        'claude_md_files': claude_md_files,
        'sessions': sess,
        'mcp_live': servers,
    }


def update_manifest(project_path, proj_folder, op, **data):
    """Refresh live facts, stamp operation `op`, recompute validation, save.
    Best-effort: never raises."""
    try:
        m = load_manifest(project_path, proj_folder)
        m.pop('_corrupt', None)
        m['project_path'] = project_path or m.get('project_path', '')
        live = _gather_live(project_path, proj_folder)
        m['repo'] = live['repo']
        m['file_hashes'] = live['file_hashes']
        m['claude_md_files'] = live['claude_md_files']
        m['sessions'] = live['sessions']

        # MCP snapshot from live status + global-CLAUDE.md documentation
        now = _now_iso()
        m['mcp'] = {
            'count': len(live['mcp_live']),
            'servers': [{'name': s['name'], 'status': s['status'],
                         'tool_count': s['tool_count'],
                         'documented_at': now if s['documented'] else ''}
                        for s in live['mcp_live']],
        }

        # source inputs snapshot
        m['source_inputs'] = _source_inputs(m)

        op_rec = dict(m['operations'].get(op, {}))
        op_rec['last_run'] = _now_iso()
        op_rec.update({k: v for k, v in data.items() if k != 'tool_count'})
        m['operations'][op] = op_rec

        # baseline for freshness: record HEAD + key hashes at generation time
        if op in ('scaffold', 'ai_analyze'):
            m['operations'][op]['head_at_gen'] = live['repo']['head_sha']
            m['operations'][op]['readme_hash'] = (live['file_hashes']
                                                  .get('README.md', {}).get('sha256', ''))
            m['operations'][op]['sessions_at_gen'] = live['sessions']['analyzed_count']

        checks, score, safe = _evaluate(m, live)
        m['validation'] = {
            'checks': checks,
            'stale': [c['name'] for c in checks if c['state'] == 'stale'],
            'conflicts': [c['name'] for c in checks if c['state'] == 'invalid'],
        }
        m['freshness_score'] = score
        m['safe_to_launch'] = safe
        m['generated_at'] = _now_iso()
        save_manifest(project_path, m, proj_folder)
        return m
    except Exception:
        _c.log.exception('workspace manifest update failed')
        return None


def _source_inputs(m):
    out = []
    if m['repo'].get('head_sha'):
        out.append({'type': 'git_repo', 'path': m.get('project_path', ''),
                    'head': m['repo']['head_short']})
    rm = m['file_hashes'].get('README.md')
    if rm and rm.get('exists'):
        out.append({'type': 'readme', 'sha256': rm['sha256']})
    if m['sessions'].get('analyzed_count'):
        out.append({'type': 'sessions', 'count': m['sessions']['analyzed_count']})
    if m['mcp'].get('count'):
        out.append({'type': 'mcp', 'count': m['mcp']['count']})
    return out


# ── status evaluation ────────────────────────────────────────

def compute_status(project_path, proj_folder=None):
    """Read-only freshness evaluation. Never writes — viewing status must not
    mutate the manifest (a corrupt file stays detectable as 🔴).
    Returns (manifest, live, checks, score, safe_to_launch)."""
    m = load_manifest(project_path, proj_folder)
    live = _gather_live(project_path, proj_folder)
    checks, score, safe = _evaluate(m, live)
    return m, live, checks, score, safe


def _last_gen(m):
    """Most recent of scaffold / ai_analyze op records (the freshness baseline)."""
    cand = [m['operations'].get(k) for k in ('ai_analyze', 'scaffold')]
    cand = [c for c in cand if c and c.get('last_run')]
    if not cand:
        return None
    return max(cand, key=lambda c: c['last_run'])


def _evaluate(m, live):
    """Return (checks, freshness_score, safe_to_launch). Pure over m + live."""
    checks = []

    def add(name, state, detail, applicable=True):
        checks.append({'name': name, 'state': state, 'detail': detail,
                       'applicable': applicable})

    corrupt = m.get('_corrupt')
    initialized = bool(_last_gen(m)) and not corrupt

    if corrupt:
        add('manifest', 'invalid', 'manifest file is corrupt')
    elif not initialized:
        add('manifest', 'stale', 'not initialized yet')
    else:
        add('manifest', 'fresh', f"schema v{m.get('schema_version')}")

    # CLAUDE.md presence
    proj_md = next((c for c in live['claude_md_files'] if c['label'] == 'project'), None)
    md_exists = bool(proj_md and proj_md['exists'])
    if md_exists:
        add('claude_md', 'fresh', 'CLAUDE.md present')
    elif initialized:
        add('claude_md', 'invalid', 'CLAUDE.md was generated but is now missing')
    else:
        add('claude_md', 'stale', 'CLAUDE.md not generated')

    gen = _last_gen(m)
    # claude_md_fresh: repo HEAD + README hash vs generation baseline
    if md_exists and gen:
        head_now = live['repo']['head_sha']
        readme_now = live['file_hashes'].get('README.md', {}).get('sha256', '')
        head_ok = (not gen.get('head_at_gen')) or gen.get('head_at_gen') == head_now
        readme_ok = (not gen.get('readme_hash')) or gen.get('readme_hash') == readme_now
        if head_ok and readme_ok:
            add('claude_md_fresh', 'fresh', 'matches repo & README at generation')
        else:
            why = 'repo moved' if not head_ok else 'README changed'
            add('claude_md_fresh', 'stale', f'CLAUDE.md may be outdated ({why})')
    else:
        add('claude_md_fresh', 'stale', 'no generation baseline', applicable=initialized)

    # repo changed since last manifest write
    if live['repo']['head_sha']:
        changed = bool(gen and gen.get('head_at_gen') and
                       gen['head_at_gen'] != live['repo']['head_sha'])
        add('repo', 'stale' if changed else 'fresh',
            'HEAD moved since generation' if changed else 'HEAD unchanged')
    else:
        add('repo', 'fresh', 'not a git repo', applicable=False)

    # mcp docs: every live server documented in global CLAUDE.md?
    live_servers = live['mcp_live']
    if live_servers:
        undoc = sorted(s['name'] for s in live_servers if not s['documented'])
        if undoc:
            add('mcp_docs', 'stale', f"undocumented: {', '.join(undoc)}")
        else:
            add('mcp_docs', 'fresh', 'all servers documented')
    else:
        add('mcp_docs', 'fresh', 'no MCP servers', applicable=False)

    # sessions: new since generation
    cur_sessions = live['sessions']['analyzed_count']
    if gen and gen.get('sessions_at_gen') is not None:
        if cur_sessions > gen['sessions_at_gen']:
            add('sessions', 'stale',
                f"{cur_sessions - gen['sessions_at_gen']} new since generation")
        else:
            add('sessions', 'fresh', f'{cur_sessions} analyzed')
    else:
        add('sessions', 'stale', 'session count not baselined', applicable=initialized)

    # conflicts: CLAUDE.md older than README
    if md_exists:
        try:
            md_t = os.path.getmtime(proj_md['path'])
        except Exception:
            md_t = 0
        rm = live['file_hashes'].get('README.md', {})
        if rm.get('exists') and rm.get('mtime', 0) > md_t > 0:
            add('conflicts', 'stale', 'README edited after CLAUDE.md')
        else:
            add('conflicts', 'fresh', 'no conflicting inputs')
    else:
        add('conflicts', 'fresh', 'n/a', applicable=False)

    # freshness score over applicable, weighted checks
    total = sum(_WEIGHTS[c['name']] for c in checks if c['applicable'] and c['name'] in _WEIGHTS)
    got = sum(_WEIGHTS[c['name']] for c in checks
              if c['applicable'] and c['name'] in _WEIGHTS and c['state'] == 'fresh')
    score = round(100 * got / total) if total else 100
    safe = not any(c['state'] == 'invalid' for c in checks)
    return checks, score, safe


# ── rendering ────────────────────────────────────────────────

_DOTS = {'fresh': '🟢', 'stale': '🟡', 'invalid': '🔴'}
_WORDS = {'fresh': 'Fresh', 'stale': 'Stale', 'invalid': 'Invalid'}
_COLORS = lambda: {'fresh': _c.C_OK, 'stale': _c.C_WARN, 'invalid': _c.C_ERR}


def _state_of(checks, name):
    for c in checks:
        if c['name'] == name:
            return c['state']
    return 'fresh'


def _status_lines(project_path, proj_folder):
    """Build the list of display lines (with ANSI) shared by CLI + TUI."""
    m, live, checks, score, safe = compute_status(project_path, proj_folder)
    col = _COLORS()
    R = _c.C_RESET
    D = _c.C_DIM

    def field(label, value, state=None):
        v = value
        if state:
            v = f"{_DOTS[state]} {col[state]}{_WORDS[state]}{R}"
        return f"  {D}{label:<18}{R}{v}"

    # Headline values come from LIVE observation, not the stored manifest —
    # status must reflect the current workspace even before any op wrote a manifest.
    repo = live['repo']
    head = repo['head_short'] or '—'
    if repo.get('branch'):
        head = f"{head}  {D}({repo['branch']}){R}"
    md_state = _state_of(checks, 'claude_md')
    if md_state == 'fresh':
        md_state = _state_of(checks, 'claude_md_fresh')
    mcp_state = _state_of(checks, 'mcp_docs')
    repo_changed = _state_of(checks, 'repo') == 'stale'

    lines = [
        field('Repo HEAD', head),
        field('Sessions analyzed', str(live['sessions']['analyzed_count'])),
        field('MCP servers', str(len(live['mcp_live']))),
        field('CLAUDE.md status', '', md_state),
        field('MCP docs status', '', mcp_state),
        field('Repo changed', f"{_c.C_WARN}Yes{R}" if repo_changed else f"{_c.C_OK}No{R}"),
        field('Safe to launch', f"{_c.C_OK}Yes{R}" if safe else f"{_c.C_ERR}No{R}"),
        '',
        f"  {D}Workspace freshness score:{R} "
        f"{col['fresh'] if score >= 80 else (col['stale'] if score >= 50 else col['invalid'])}{score}%{R}"
        f"  {render.meter(score, width=20, color=(_c.C_OK if score >= 80 else _c.C_WARN))}",
    ]
    return lines, m, score, safe


def print_workspace_status(project_path, proj_folder=None):
    """Scriptable colored stdout block (no alt-screen)."""
    try:
        import sys
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    lines, _m, _score, _safe = _status_lines(project_path, proj_folder)
    print(f"\n  {_c.C_BOLD}Workspace Status{_c.C_RESET}")
    print(f"  {_c.C_DIM}{'─' * 16}{_c.C_RESET}")
    for ln in lines:
        print(ln)
    print()


def workspace_status_screen(project_path, proj_folder=None):
    """TUI screen for the sessions-menu `w` hotkey."""
    from .ui import wait_event
    name = os.path.basename(project_path) or project_path or 'workspace'
    from . import diffview
    from .sessions import format_age

    def _current(key):
        p = {'claude_md': os.path.join(project_path, 'CLAUDE.md') if project_path else '',
             'system_prompt': os.path.join(proj_folder, 'system-prompt.txt') if proj_folder else '',
             }.get(key, '')
        if p and os.path.isfile(p):
            try:
                return open(p, encoding='utf-8', errors='ignore').read()
            except Exception:
                return ''
        return ''

    while True:
        lines, m, score, safe = _status_lines(project_path, proj_folder)
        frame = [render.header('CLAUDECTL', name, 'WORKSPACE'), '', render.hline(), '']
        frame += lines
        # ── project health card (frequent Claude Code problems, auto-checked) ──
        try:
            from . import health
            issues = health.check_project(project_path, proj_folder)
        except Exception:
            issues = []
        if issues:
            frame += ['', f"  {_c.C_BOLD}Project health{_c.C_RESET}"]
            for sev, msg, hint in issues[:6]:
                col = _c.C_WARN if sev == 'warn' else _c.C_DIM
                frame.append(f"    {col}● {msg}{_c.C_RESET}")
                if hint:
                    frame.append(f"      {_c.C_DIM}{hint}{_c.C_RESET}")
        frame += ['', render.hline()]
        last = max((o.get('last_run', '') for o in m['operations'].values()), default='')
        if last:
            frame.append(f"  {_c.C_DIM}last operation: {last}{_c.C_RESET}")

        changes = [(k, diffview.last_change(project_path, proj_folder, k))
                   for k in ('claude_md', 'system_prompt')]
        changes = [(k, c) for k, c in changes if c]
        diff_keys = set()
        if changes:
            frame += ['', f"  {_c.C_BOLD}Recent changes{_c.C_RESET}"]
            for k, c in changes:
                diff_keys.add(k)
                age = format_age(c['ts'])
                frame.append(
                    f"    {diffview.TITLES[k]:<16} {_c.C_DIM}{age}{_c.C_RESET}  "
                    f"{_c.C_OK}+{c['added']}{_c.C_RESET} {_c.C_ERR}-{c['removed']}{_c.C_RESET}")

        keys = [('r', 'refresh')]
        if 'claude_md' in diff_keys:
            keys.append(('c', 'CLAUDE.md diff'))
        if 'system_prompt' in diff_keys:
            keys.append(('s', 'sys-prompt diff'))
        keys.append(('P', 'allowlist from history'))
        keys.append(('ENTER/ESC', 'back'))
        frame += ['', render.hint_keys(keys)]
        render.render_frame(frame)
        ev = wait_event()
        if ev[0] in ('enter', 'esc'):
            return
        if ev[0] == 'char' and ev[1] == 'r':
            continue
        if ev[0] == 'char' and ev[1] == 'P':
            from .ui import flash
            from . import health
            n, err = health.propose_allowlist(project_path, proj_folder)
            flash(f"Added {n} allow rules to project settings.json" if n
                  else f"No changes: {err}", ok=bool(n), secs=2)
        if ev[0] == 'char' and ev[1] == 'c' and 'claude_md' in diff_keys:
            diffview.show(diffview.load_prev(project_path, proj_folder, 'claude_md'),
                          _current('claude_md'), diffview.TITLES['claude_md'])
        if ev[0] == 'char' and ev[1] == 's' and 'system_prompt' in diff_keys:
            diffview.show(diffview.load_prev(project_path, proj_folder, 'system_prompt'),
                          _current('system_prompt'), diffview.TITLES['system_prompt'])
