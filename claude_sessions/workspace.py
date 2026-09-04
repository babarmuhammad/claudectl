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
import re
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
    'claude_md_claims': 5,
}

#: Operations that regenerate the project's context from live inputs, and so
#: legitimately re-baseline freshness. The WRITER (update_manifest) and the
#: READER (_last_gen) must use this one tuple: the original code stamped a
#: baseline for scaffold/ai_analyze only, while memory-rebuild, compress and
#: prune rebuild the very same AUTOGEN/SESSIONS blocks from live git. So the
#: score could never come back off Stale no matter how often memory was rebuilt
#: — and health._check_memory read an `operations.memory.head_at_gen` that
#: nothing on earth wrote.
_BASELINE_OPS = ('scaffold', 'ai_analyze', 'compress', 'memory', 'prune')

#: what the user should press to clear each stale check. Diagnosis without a
#: remedy is why this screen got read once and never again.
_FIXES = {
    'manifest': 'build memory (m → b) or scaffold CLAUDE.md (c)',
    'claude_md': 'scaffold CLAUDE.md (c)',
    'claude_md_fresh': 'rebuild memory (m → b) — cheap and incremental',
    'mcp_docs': 'analyze the undocumented server(s) from the MCP screen',
    'repo': 'rebuild memory (m → b) to re-baseline against this HEAD',
    'sessions': 'rebuild memory (m → b) to fold in the new sessions',
    'conflicts': 'README is newer than CLAUDE.md — re-run analyze (a)',
    'claude_md_claims': 'one of the two is out of date — rebuild memory (m → b) if '
                        'the graph is behind, or edit that sentence in CLAUDE.md '
                        'yourself; your prose is the one block claudectl never rewrites',
}


# ── low-level helpers ────────────────────────────────────────

def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _git_head(path):
    """(full_sha, short_sha, branch) for the repo at path, or ('','','')."""
    if not path or not os.path.isdir(os.path.join(path, '.git')):
        # still try rev-parse — path may be inside a worktree/subdir of a repo
        pass
    from .repos import _git
    sha = (_git(['rev-parse', 'HEAD'], path) or '').strip()
    if not sha:
        return ('', '', '')
    br = (_git(['branch', '--show-current'], path) or '').strip()
    return (sha, sha[:7], br)


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


def _global_md_paths():
    """[(account_name, path)] for every account's global CLAUDE.md.

    Derived per call, never cached at import — see the note in _gather_live."""
    try:
        return [(name, _c.global_claude_md_for(d)) for name, d in _c.all_config_dirs()]
    except Exception:
        return [('default', _c.global_claude_md_for(None))]


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
        #: freshness baseline — see _last_gen. Empty until an op that
        #: regenerates the project's context has run at least once.
        'baseline': {},
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

# ── does the prose still agree with the graph? ───────────────
#
# CLAUDE.md has two halves and claudectl owns exactly one of them. It rewrites
# AUTOGEN/SESSIONS/MEMORY/AGENTS from live inputs; it must never touch the prose
# above them, because a tool that silently rewords what you wrote is worse than
# one that lets it age. The consequence is that the hand-written half is the only
# part of the file with no freshness signal at all — and it is append-only by
# habit, so a fact written on line 44 is never read again.
#
# This repository's own CLAUDE.md carried "29 palettes x 7 skins" against a
# themes.py holding 32 and 8, for months, while the memory graph — re-extracted
# from the same code — said 32. So the oracle already existed; nothing compared
# the two. That is all this does, with no model call and no subprocess.

#: Words that end a claim. "32 palettes and 4 themed worlds" must register
#: `palettes: 32`, not tie 32 to every noun in the rest of the sentence.
_CLAIM_STOP = frozenset(
    'a an and are as at because by for from in into is of on or over per plus '
    'that the to under via with x'.split())
#: Units a text may legitimately restate with a different number — a budget of
#: 250 tokens here and 600 there is not a contradiction.
_CLAIM_UNITS = frozenset(
    'bytes chars characters days entries hours items lines minutes months '
    'percent pixels seconds times tokens weeks years'.split())
#: A number, then the words it counts. The lookbehind keeps `1.9.0` and `0.5s`
#: from being read as claims about whatever follows them.
_CLAIM_RE = re.compile(r'(?<![\w.])(\d[\d,]*)\s+([a-z][a-z \-]{0,40})')
#: A sentence carrying one of these is describing what the project USED to be.
#: This repository's own CLAUDE.md says "an earlier design was 26 generative
#: canvas renderers … that was deleted", and comparing that against a graph
#: extracted from the code that replaced it is the single loudest false positive
#: this check can produce — the prose is correct, and it is history.
_CLAIM_PAST = re.compile(
    r'\b(?:was|were|used to|had|earlier|previously|before|old|former|deleted|'
    r'removed|replaced|dropped|gone|no longer|instead of|rejected)\b', re.I)
#: Sentence boundaries, plus list items and headings: a markdown bullet is a
#: sentence for this purpose even when it never reaches a full stop.
_CLAIM_SPLIT = re.compile(r'(?:[.!?;]\s|\n)')


def _claims(text, present_only=False):
    """`{noun: number}` for every countable claim the text makes exactly once.

    A noun stated with two different numbers is DROPPED rather than guessed at.
    That single rule is most of what keeps this usable: it is why "32 palettes
    and 4 themed worlds" contributes `palettes` and `worlds` but not `themed`,
    and why a page mentioning two different budgets contributes neither.

    Two more filters, both learned from running it on this repository:
    `present_only` skips sentences written in the past tense (see _CLAIM_PAST),
    and only PLURAL nouns count — "15 call sites" and "2 because" were both read
    as claims before that, and a counted thing is essentially always plural."""
    seen = {}
    for part in _CLAIM_SPLIT.split(text or ''):
        if present_only and _CLAIM_PAST.search(part):
            continue
        for num, tail in _CLAIM_RE.findall(part):
            try:
                n = int(num.replace(',', ''))
            except ValueError:
                continue
            for word in tail.split()[:3]:
                word = word.strip('-')
                if word in _CLAIM_STOP:
                    break
                if len(word) >= 4 and word.endswith('s') and word not in _CLAIM_UNITS:
                    seen.setdefault(word, set()).add(n)
    return {w: next(iter(ns)) for w, ns in seen.items() if len(ns) == 1}


def _claim_conflicts(md_text, mem):
    """`[(noun, what CLAUDE.md says, what memory says)]`, sorted.

    This reports a DISAGREEMENT, not a verdict, and the wording everywhere says
    so. The graph is usually the fresher of the two — it is re-extracted from the
    code, while the prose is written once and then only appended to — but it is
    not a clean oracle: it holds entities extracted in different cycles, so it
    can contradict itself. On this repository one entity says "29 palettes and 7
    skins" while a newer one says "32 palettes and 4 themed worlds".

    The ambiguity rule in `_claims` turns that into a MISS rather than a false
    accusation (`palettes` carries two numbers on the graph side, so it is
    dropped) — which is the right way round, and the reason the fix text names
    rebuilding memory first. Resolving it by preferring the newest entity was
    considered and rejected: `created_at` is when an entity was first seen, not
    when it was last refreshed, so the tiebreak would be reading a timestamp that
    does not mean what it would need to mean.

    Only the MANUAL half is read — a generated block disagreeing with the graph
    is a rebuild, not a contradiction, and would be a permanent false positive."""
    from .ctxaudit import split_blocks
    said = _claims(split_blocks(md_text)['manual'], present_only=True)
    if not said:
        return []
    summaries = [e.get('summary') or '' for e in mem.get('entities') or []]
    for key in ('repo_summaries', 'summaries'):
        val = mem.get(key)
        if isinstance(val, dict):
            summaries += [v for v in val.values() if isinstance(v, str)]
    known = _claims(' \n'.join(summaries))
    return sorted((w, said[w], known[w])
                  for w in said if w in known and said[w] != known[w])


def _gather_live(project_path, proj_folder):
    """Cheaply collect the current observable workspace facts."""
    from .claude_md import resolve_memory_files

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

    # None means "not compared", which is a different fact from "compared and
    # agrees" — most projects have no graph yet, and reporting them Fresh here
    # would be the same confident overstatement claude_md_fresh already avoids.
    claim_conflicts = None
    try:
        proj = next((c for c in claude_md_files
                     if c['label'] == 'project' and c['exists']), None)
        if proj and project_path:
            from . import memory
            mem = memory.load_memory(project_path, proj_folder)
            if mem.get('entities'):
                with open(proj['path'], encoding='utf-8', errors='ignore') as f:
                    claim_conflicts = _claim_conflicts(f.read(), mem)
    except Exception:
        pass

    # A count and the two extreme mtimes — so this must NOT parse transcripts.
    # `scan_sessions` also builds a preview and a message count for every file
    # (1 542 ms over 687 sessions on this repo) and both are discarded here,
    # which is most of what made a read-only status call take seconds. The
    # internal-session filter is kept, so `analyzed_count` is still the same
    # number `update_manifest` baselined.
    sess = {'analyzed_count': 0, 'first_ts': 0, 'last_ts': 0, 'range_days': 0}
    stamps = []
    if proj_folder:
        from .sessions import is_internal_session, project_session_folders
        seen_sids = set()
        for folder in project_session_folders(proj_folder):
            if not os.path.isdir(folder):
                continue
            for nm in os.listdir(folder):
                if not nm.endswith('.jsonl') or nm[:-6] in seen_sids:
                    continue                       # dedup by sid across accounts
                p = os.path.join(folder, nm)
                try:
                    mtime = os.path.getmtime(p)
                except OSError:
                    continue
                if is_internal_session(p):
                    continue
                seen_sids.add(nm[:-6])
                stamps.append(mtime)
    if stamps:
        last_ts, first_ts = max(stamps), min(stamps)
        sess = {'analyzed_count': len(stamps), 'first_ts': first_ts, 'last_ts': last_ts,
                'range_days': round(max(0.0, last_ts - first_ts) / 86400, 1)}

    # MCP docs live in the global CLAUDE.md (per-server sentinel sections).
    # Freshness = each live server has a section there; tool counts parsed from it.
    #
    # Read EVERY account's global CLAUDE.md, not `_c.global_claude_md`. That
    # module attribute is computed at import from the then-active config dir,
    # while the writer (mcp.update_global_claude_md_mcp) takes a cfgdir — so
    # documenting a server under a non-default account wrote a file this reader
    # never opened, and the check was permanently stale. Fourth instance of the
    # import-time-binding bug this codebase keeps re-learning.
    #
    # Read the cache, never spawn. `mcp.mcp_servers` is filled by a background
    # thread at import, so reading it is free; `get_mcp_status()` shells out to
    # `claude mcp list` and took ~3 s — inside a call whose own docstring calls
    # itself read-only. An empty list is also the legitimate answer for someone
    # with no servers, so the cache is trusted only once `_mcp_ready` says the
    # thread finished, the same guard ctxaudit.py:223 already uses.
    servers = []
    mcp_ready = False
    try:
        from . import mcp
        mcp_ready = bool(getattr(mcp, '_mcp_ready', False))
        cur = list(mcp.mcp_servers) if mcp_ready else []
        texts = []
        for _name, d in _global_md_paths():
            try:
                if os.path.isfile(d):
                    texts.append(open(d, encoding='utf-8', errors='ignore').read())
            except Exception:
                continue
        for n, s in cur:
            start, end = f'<!-- MCP:{n}:START -->', f'<!-- MCP:{n}:END -->'
            documented, tool_count = False, 0
            for gtext in texts:
                if start in gtext and end in gtext:
                    documented = True
                    seg = gtext[gtext.index(start) + len(start):gtext.index(end)]
                    tool_count = max(tool_count, _count_tools(seg))
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
        'mcp_ready': mcp_ready,
        'claim_conflicts': claim_conflicts,
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

        # MCP snapshot from live status + global-CLAUDE.md documentation.
        # Only when the status is actually known: since _gather_live stopped
        # spawning `claude mcp list`, an update that lands before the background
        # thread finishes would otherwise overwrite a correct snapshot with
        # zero servers.
        now = _now_iso()
        if live.get('mcp_ready', True):
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

        # Baseline for freshness: HEAD + key hashes at generation time.
        #
        # Stored ONCE at the top level, because it describes the project, not
        # the operation that happened to refresh it. Keeping it per-op meant
        # picking a winner among several, and ISO timestamps are second-
        # resolution — two ops in the same second tied, and the tie went to
        # whichever came first in _BASELINE_OPS rather than to the latest.
        # Still mirrored onto the op record: health._check_memory reads
        # operations['memory']['head_at_gen'], and old manifests only have it
        # there.
        if op in _BASELINE_OPS:
            base = {'head_at_gen': live['repo']['head_sha'],
                    'readme_hash': (live['file_hashes']
                                    .get('README.md', {}).get('sha256', '')),
                    'sessions_at_gen': live['sessions']['analyzed_count']}
            m['operations'][op].update(base)
            m['baseline'] = dict(base, op=op, last_run=m['operations'][op]['last_run'])

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
    """The freshness baseline: what the repo looked like when the project's
    context was last regenerated.

    Written at the top level by update_manifest. Falls back to the per-op
    records for manifests written before that existed — `sessions_at_gen` is
    the marker there, because an op that regenerates nothing (a `launch`) has
    only `last_run`, and taking that as a baseline would report `fresh` on no
    evidence at all."""
    base = m.get('baseline')
    if isinstance(base, dict) and 'sessions_at_gen' in base:
        return base
    cand = [m['operations'].get(k) for k in _BASELINE_OPS]
    cand = [c for c in cand if c and c.get('last_run') and 'sessions_at_gen' in c]
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
    elif live.get('mcp_ready', True):
        add('mcp_docs', 'fresh', 'no MCP servers', applicable=False)
    else:
        # empty because nothing has been read yet, which is a different fact
        # from having no servers — say which one it is
        add('mcp_docs', 'fresh', 'MCP status not read yet', applicable=False)

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

    # claude_md_claims: the hand-written prose vs the graph. This is the only
    # check about the half of CLAUDE.md claudectl may not repair, so its detail
    # has to carry the whole finding — there is no button that fixes it.
    conflicts = live.get('claim_conflicts')
    if not md_exists or conflicts is None:
        add('claude_md_claims', 'fresh', 'no memory graph to check against',
            applicable=False)
    elif conflicts:
        word, said, known = conflicts[0]
        more = f' (+{len(conflicts) - 1} more)' if len(conflicts) > 1 else ''
        add('claude_md_claims', 'stale',
            f'CLAUDE.md says {said} {word}, memory says {known}{more}')
    else:
        add('claude_md_claims', 'fresh', 'prose agrees with memory')

    # freshness score over applicable, weighted checks
    total = sum(_WEIGHTS[c['name']] for c in checks if c['applicable'] and c['name'] in _WEIGHTS)
    got = sum(_WEIGHTS[c['name']] for c in checks
              if c['applicable'] and c['name'] in _WEIGHTS and c['state'] == 'fresh')
    score = round(100 * got / total) if total else 100
    safe = not any(c['state'] == 'invalid' for c in checks)
    return checks, score, safe


# ── rendering ────────────────────────────────────────────────

_DOTS = {'fresh': '🟢', 'stale': '🟡', 'invalid': '🔴', 'n/a': '⚪'}
_WORDS = {'fresh': 'Fresh', 'stale': 'Stale', 'invalid': 'Invalid', 'n/a': 'n/a'}
_COLORS = lambda: {'fresh': _c.C_OK, 'stale': _c.C_WARN, 'invalid': _c.C_ERR,
                   'n/a': _c.C_DIM}


def _state_of(checks, name):
    """The display state of a check.

    An `applicable=False` check is excluded from BOTH sides of the score, so
    painting it 🟡 Stale told the user a warning that contributed nothing to the
    number underneath it. It reads 'n/a' now, and the dots add up to the score."""
    for c in checks:
        if c['name'] == name:
            return c['state'] if c.get('applicable', True) else 'n/a'
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
    # 'the file is there' and 'the file is current' are two different claims.
    # When there is no baseline the second one is UNKNOWN, and n/a says so —
    # reporting Fresh there would be the same confident overstatement as the
    # permanent Stale this screen used to show, just pointing the other way.
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
    # every point the score is missing, and the one thing that recovers it
    todo = [(c['name'], c['detail']) for c in checks
            if c.get('applicable', True) and c['state'] != 'fresh'
            and c['name'] in _WEIGHTS]
    if todo:
        lines.append('')
        lines.append(f"  {D}To raise it:{R}")
        for name, detail in todo:
            fix = _FIXES.get(name, '')
            lines.append(f"    {_c.C_WARN}●{R} {detail}"
                         f"{'  ' + D + '→ ' + fix + R if fix else ''}"
                         f"  {D}(+{_WEIGHTS[name]}){R}")
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
