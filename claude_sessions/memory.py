"""Claude-powered persistent project memory (native cognee-style ECL).

Builds and stores a semantic knowledge graph of a project — entities and
relationships extracted by Claude (claude.exe) from source files, CLAUDE.md,
and session summaries — under <project>/.claudectl/memory/. Updated
incrementally via file hashes. Powers the semantic layer of the connections
graph and a grounded "ask the project" query. No third-party deps, no separate
API key (reuses Claude Code's auth). Best-effort: failures never corrupt the
stored graph.

Inspired by cognee (Apache-2.0); implemented from scratch.
"""

import os
import re
import json
import threading

from . import config as _c

# background-refresh coordination: a thread sets _tls.silent so its Claude calls
# run headless (no progress UI / keyboard) and never touch the live TUI.
_tls = threading.local()
_bg_lock = threading.Lock()
_bg_active = set()          # project paths currently refreshing in the background

SCHEMA_VERSION = 3
MEM_SUBDIR = os.path.join('.claudectl', 'memory')
GRAPH_NAME = 'graph.json'
PER_FILE_CHARS = 4000    # cap content per file
PER_BATCH_CHARS = 40000  # cap corpus per repo/module Claude call
MODULE_MAX_FILES = 24    # representative files per module
EXTRACT_TIMEOUT = 300
_INVALID_CAP = 150       # max invalidated (superseded) facts kept as history


# ── persistence ──────────────────────────────────────────────

def _mem_dirs(project_path, proj_folder):
    out = []
    if project_path:
        out.append(os.path.join(project_path, MEM_SUBDIR))
    if proj_folder:
        out.append(os.path.join(proj_folder, MEM_SUBDIR))
    return out


def _empty():
    return {'schema_version': SCHEMA_VERSION, 'generated_at': '',
            'entities': [], 'relations': [], 'summaries': {}, 'provenance': {},
            'module_edges': [], 'lessons_scanned': {}, 'session_counter': 0,
            'pending_units': 0, 'repo_summaries': {}}


def _migrate(m):
    base = _empty()
    for k, v in base.items():
        m.setdefault(k, v)
    if m.get('schema_version', 1) < 3:
        for e in m.get('entities', []):          # temporal fields on existing facts
            e.setdefault('valid', True)
            e.setdefault('hits', 0)
    m['schema_version'] = SCHEMA_VERSION
    return m


def load_memory(project_path, proj_folder=None):
    for d in _mem_dirs(project_path, proj_folder):
        p = os.path.join(d, GRAPH_NAME)
        if os.path.isfile(p):
            try:
                with open(p, encoding='utf-8') as f:
                    data = json.load(f)
                return _migrate(data) if isinstance(data, dict) else _empty()
            except Exception:
                return _empty()
    return _empty()


def save_memory(project_path, proj_folder, m):
    # Write to BOTH the working-dir and encoded-folder locations so the graph
    # is discoverable for cross-project scanning (conventions) regardless of
    # which one a caller resolves. Success if at least one write lands.
    ok = False
    for d in _mem_dirs(project_path, proj_folder):
        try:
            os.makedirs(d, exist_ok=True)
            # temp file + atomic replace: a killed process (e.g. the detached
            # bg worker, or claudectl exiting to launch claude) can never leave
            # torn JSON — which load_memory would silently reset to _empty().
            path = os.path.join(d, GRAPH_NAME)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(m, f, indent=2)
            os.replace(tmp, path)
            ok = True
        except Exception:
            continue
    return ok


def clear_memory(project_path, proj_folder=None):
    for d in _mem_dirs(project_path, proj_folder):
        p = os.path.join(d, GRAPH_NAME)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except Exception:
                pass


# ── cross-process scan lock ──────────────────────────────────
# The background memory update runs in a DETACHED worker process (see
# spawn_background_worker) so it survives the TUI exiting to launch claude.
# A marker file makes its status visible to any claudectl process: dedup
# guard for spawners, live progress for the sessions-menu badge.

SCAN_LOCK = 'scan.lock'
SCAN_LOCK_STALE_SEC = 900
_scan_lock_root = None      # set while THIS process holds the lock


def _scan_lock_path(project_path):
    if not project_path:
        return None
    return os.path.join(os.path.abspath(project_path), MEM_SUBDIR, SCAN_LOCK)


def _pid_alive(pid):
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        # NEVER os.kill(pid, 0) on Windows — it TERMINATES the process.
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == 259         # STILL_ACTIVE
                return False
            finally:
                k32.CloseHandle(h)
        except Exception:
            return None                              # unknown → age decides
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_scan_lock(project_path):
    """Live lock dict, or None (missing / unreadable / stale — stale removed)."""
    import time
    p = _scan_lock_path(project_path)
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = None
    stale = True
    if isinstance(data, dict):
        alive = _pid_alive(data.get('pid'))
        fresh = time.time() - data.get('updated', 0) < SCAN_LOCK_STALE_SEC
        stale = (alive is False) or not fresh
    if stale:
        try:
            os.remove(p)
        except Exception:
            pass
        return None
    return data


def scan_lock_status(project_path):
    """None if no live worker, else its progress string (may be '')."""
    data = _read_scan_lock(project_path)
    return None if data is None else str(data.get('progress', ''))


def acquire_scan_lock(project_path):
    """Claim the scan lock for THIS process. False if a live worker holds it."""
    global _scan_lock_root
    import time
    p = _scan_lock_path(project_path)
    if not p:
        return False
    if _read_scan_lock(project_path) is not None:
        return False
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({'pid': os.getpid(), 'started': _iso(),
                       'updated': time.time(), 'progress': ''}, f)
        _scan_lock_root = os.path.abspath(project_path)
        return True
    except Exception:
        return False


def clear_scan_lock(project_path):
    global _scan_lock_root
    p = _scan_lock_path(project_path)
    if p and os.path.isfile(p):
        try:
            os.remove(p)
        except Exception:
            pass
    if _scan_lock_root == os.path.abspath(project_path or ''):
        _scan_lock_root = None


def _report_progress(text):
    """Update the scan-lock progress line — no-op unless this process holds a
    lock (foreground refresh/scan calls this too; it must stay silent there)."""
    import time
    if not _scan_lock_root:
        return
    p = _scan_lock_path(_scan_lock_root)
    try:
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({'pid': os.getpid(), 'started': _iso(),
                       'updated': time.time(), 'progress': str(text)}, f)
    except Exception:
        pass


def spawn_background_worker(project_path, proj_folder):
    """Run the memory update (lessons scan, then refresh) in a DETACHED child
    process so it survives claudectl exiting to launch claude.exe — on the .bat
    path the TUI process dies the moment a session is picked, which used to
    kill the daemon-thread scan mid-flight. Dedup via scan.lock. Returns the
    Popen or None."""
    import sys
    import subprocess
    root = os.path.abspath(project_path or '')
    if not root or not os.path.isdir(root):
        return None
    if scan_lock_status(project_path) is not None or root in _bg_active:
        return None
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env['PYTHONPATH'] = pkg_parent + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    flags = (getattr(subprocess, 'DETACHED_PROCESS', 0)
             | getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        return subprocess.Popen(
            [sys.executable, '-m', 'claude_sessions', '--bg-scan',
             root, proj_folder or ''],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags, cwd=root, env=env)
    except Exception:
        _c.log.exception('memory: bg worker spawn failed')
        return None


# ── Claude calls (monkeypatched in tests) ────────────────────

def extract_model():
    """Economy model for claudectl's OWN internal generation calls (memory,
    lessons, CLAUDE.md, agent/hook/skill gen). '' = leave the account default."""
    try:
        from .config import load_settings
        return (load_settings().get('extract_model') or '').strip()
    except Exception:
        return ''


def _claude_stdin(prompt, cwd, timeout=EXTRACT_TIMEOUT,
                  crumbs=('CLAUDECTL', 'MEMORY'), label='Working with Claude...',
                  model=None):
    """Run `claude -p` reading the prompt from stdin (avoids the Windows
    command-line length limit). Foreground: visible progress bar (ESC cancels).
    Background threads (_tls.silent): headless subprocess, no UI/keyboard.
    `model` overrides the economy extract_model; '' forces the account default.
    Returns stdout text or ''."""
    from .config import get_claude_exe
    exe = get_claude_exe()
    if not exe:
        return ''
    args = [exe, '-p', '--disallowedTools', 'Write,Edit,NotebookEdit,Bash']
    m = extract_model() if model is None else (model or '').strip()
    if m:
        args += ['--model', m]
    if getattr(_tls, 'silent', False):
        import subprocess
        try:
            p = subprocess.run(args, input=prompt, capture_output=True, text=True,
                               encoding='utf-8', errors='ignore', cwd=cwd, timeout=timeout)
            return p.stdout or ''
        except Exception:
            return ''
    from .ui import run_with_progress_stdin
    out, _cancelled = run_with_progress_stdin(
        args, prompt, crumbs, label, timeout=timeout, cwd=cwd)
    return out or ''


def _parse_json(text):
    if not text:
        return None
    t = text.strip()
    if '```' in t:                       # strip code fences
        import re
        m = re.search(r'```(?:json)?\s*(.*?)```', t, re.S)
        if m:
            t = m.group(1).strip()
    if '{' in t and '}' in t:
        t = t[t.index('{'):t.rindex('}') + 1]
    try:
        return json.loads(t)
    except Exception:
        return None


def _extract(corpus_text, cwd, unit='', progress=''):
    """Claude → {summary, entities:[{name,type,summary}], relations:[{source,target,rel}]}
    for one repo/module unit."""
    prompt = (
        "You are building a knowledge graph for a software project module. From "
        "the MODULE CONTENT below, extract: a one-sentence summary of what this "
        f"module ({unit or 'module'}) does, its key entities (components, "
        "services, data models, concepts), and relationships between them.\n\n"
        "Output ONLY valid JSON, no prose, no code fences:\n"
        '{"summary":"one sentence","entities":[{"name":"...",'
        '"type":"module|component|concept|service|model","summary":"one concise sentence"}],'
        '"relations":[{"source":"EntityName","target":"EntityName","rel":"uses|calls|depends_on|contains|implements"}]}\n\n'
        "At most ~15 entities. Use entity NAMES (not ids) in relations.\n\n"
        f"MODULE CONTENT:\n{corpus_text}"
    )
    label = f"Analyzing {unit} with Claude...  {progress}".strip()
    data = _parse_json(_claude_stdin(
        prompt, cwd, crumbs=('CLAUDECTL', 'MEMORY', unit or 'EXTRACT'), label=label))
    if not isinstance(data, dict):
        return {'summary': '', 'entities': [], 'relations': []}
    return {'summary': data.get('summary', '') or '',
            'entities': data.get('entities', []) or [],
            'relations': data.get('relations', []) or []}


def _answer(context, question, cwd):
    prompt = (
        "Answer the QUESTION about this project using ONLY the knowledge-graph "
        "CONTEXT below (entities, relationships, file summaries). Be concise and "
        "specific; if the context is insufficient, say so.\n\n"
        f"CONTEXT:\n{context}\n\nQUESTION: {question}\n"
    )
    return _claude_stdin(prompt, cwd, timeout=120,
                         crumbs=('CLAUDECTL', 'ASK'),
                         label='Asking Claude about the project...').strip()


# ── corpus / units (whole-project coverage) ──────────────────

_EXCLUDE_NAMES = {'claude.md', 'claude.local.md'}
_INTERFACE_HINTS = ('interface', 'api', 'service', 'controller', 'main', 'program',
                    'index', '__init__', 'module', 'core', 'manager', 'model', 'repository')


def _rel(root, f):
    try:
        return os.path.relpath(f, root).replace('\\', '/')
    except Exception:
        return os.path.basename(f)


def _module_of(root, rsorted, f, repo_label='', depth=2):
    """Module key = the file's dirname relative to its OWNING REPO (not the
    project root), capped at `depth` segments. Fixes the old parts[1] scheme
    that collapsed single-package repos into one '(root)' unit."""
    ap = os.path.abspath(f)
    base = root
    for rp in rsorted:
        if ap == rp or ap.startswith(rp + os.sep):
            base = rp
            break
    rel = _rel(base, f)
    dirs = rel.split('/')[:-1]
    # non-git fallback: cluster label is the top-level dir itself — drop the
    # duplicate leading segment so the module is relative to the cluster
    if dirs and base == root and dirs[0] == repo_label:
        dirs = dirs[1:]
    return '/'.join(dirs[:depth]) or '(root)'


def _units(project_path, proj_folder):
    """Whole project split into (repo, module, [abs files]) units — every repo
    and its modules, ordered most-important (most files) first."""
    from . import connections
    root = os.path.abspath(project_path)
    files, _ = connections._walk_source_files(root, connections.GROUP_MAX_FILES)
    files = [f for f in files if os.path.basename(f).lower() not in _EXCLUDE_NAMES]
    repos = connections._discover_repos(root, proj_folder)
    rsorted = sorted((os.path.abspath(p) for p in repos), key=len, reverse=True)
    groups = {}
    for f in files:
        repo = connections._cluster_of(f, root, rsorted)
        module = _module_of(root, rsorted, f, repo_label=repo)
        groups.setdefault((repo, module), []).append(f)
    units = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
    return [(r, m, fs) for (r, m), fs in units]


def _representative(files):
    """Pick the most informative files of a module (interfaces/headers/entry +
    largest), capped, so a module is covered without sending everything."""
    def score(f):
        b = os.path.basename(f).lower()
        s = 5 if any(k in b for k in _INTERFACE_HINTS) else 0
        if os.path.splitext(b)[1] in ('.h', '.hpp', '.cs', '.ts', '.py', '.go'):
            s += 2
        try:
            s += min(os.path.getsize(f) // 2500, 6)
        except OSError:
            pass
        return s
    return sorted(files, key=score, reverse=True)[:MODULE_MAX_FILES]


def _unit_corpus(root, files):
    parts, total = [], 0
    for f in files:
        rel = _rel(root, f)
        piece = f"### FILE: {rel}\n{_read(f)[:PER_FILE_CHARS]}"
        if total + len(piece) > PER_BATCH_CHARS:
            break
        parts.append(piece)
        total += len(piece)
    return '\n\n'.join(parts)


def _read(f):
    try:
        with open(f, encoding='utf-8', errors='ignore') as fh:
            return fh.read()
    except Exception:
        return ''


# ── refresh (cognify) — per repo/module, whole project ───────

def _changed_units(root, units, mem):
    """(todo, deleted, cur_hashes): units whose representative files changed or
    that the current graph doesn't cover yet, plus tracked files now gone.
    Hash-only — no Claude calls. Shared by refresh_memory and is_stale."""
    from .workspace import _sha256_file
    prov = mem.get('provenance', {})
    cur_hashes = {}
    todo = []
    for repo, module, fs in units:
        h = {_rel(root, f): _sha256_file(f) for f in fs}
        cur_hashes.update(h)
        if any(prov.get(rel, {}).get('hash') != hv for rel, hv in h.items()):
            todo.append((repo, module, fs))
    # key-scheme drift (e.g. v1→v2 module remap): units with unchanged hashes
    # but no entities under the current key must still be (re)extracted
    covered = {(e.get('repo'), e.get('module')) for e in mem.get('entities', [])}
    todo_keys = {(r, m) for r, m, _ in todo}
    if mem.get('entities'):
        for repo, module, fs in units:
            if (repo, module) not in covered and (repo, module) not in todo_keys:
                todo.append((repo, module, fs))
                todo_keys.add((repo, module))
    deleted = [rel for rel in prov if rel not in cur_hashes]
    return todo, deleted, cur_hashes


def is_stale(project_path, proj_folder):
    """True if the project's source changed since its memory graph was built —
    a cheap hash-only check (no Claude). Returns False when there's no graph yet
    (first build stays manual, matching the TUI). Used to decide whether an
    auto-refresh is actually worth running."""
    try:
        mem = load_memory(project_path, proj_folder)
        if not mem.get('entities'):
            return False
        root = os.path.abspath(project_path)
        units = _units(project_path, proj_folder)
        todo, deleted, _ = _changed_units(root, units, mem)
        return bool(todo or deleted)
    except Exception:
        _c.log.exception('memory: is_stale check failed')
        return False


def refresh_memory(project_path, proj_folder, project_name, auto_cap=None):
    """(Re)extract the semantic graph across EVERY repo and its important
    modules. Incremental by file hash; only changed modules are re-analyzed.
    `auto_cap`: if set and the number of changed units exceeds it, do nothing
    and return the current graph tagged `auto_skipped` (used by the auto-refresh
    path so a big rebuild never runs silently on project open)."""
    from .config import load_settings
    root = os.path.abspath(project_path)
    mem = load_memory(project_path, proj_folder)
    prov = mem.get('provenance', {})
    units = _units(project_path, proj_folder)

    todo, deleted, cur_hashes = _changed_units(root, units, mem)
    if not todo and not deleted and mem.get('entities'):
        return mem

    if auto_cap is not None and len(todo) > auto_cap and mem.get('entities'):
        mem['auto_skipped'] = len(todo)              # too big for a silent refresh
        return mem

    max_calls = load_settings().get('memory_max_calls') or None
    skipped_units = 0
    if max_calls:
        skipped_units = max(0, len(todo) - max_calls)
        todo = todo[:max_calls]

    touched_units = {(r, m) for r, m, _ in todo}
    current_units = {(r, m) for r, m, _ in units}          # units that still exist
    current_strs = {f"{r}/{m}" for r, m in current_units}
    now = _iso()

    prev = mem.get('entities', [])
    # entities of untouched units: carry as-is, but INVALIDATE (not delete) any
    # whose unit no longer exists — temporal history is preserved
    kept = []
    for e in prev:
        if (e.get('repo'), e.get('module')) in touched_units:
            continue                                       # reconciled below
        if (e.get('type') != 'lesson'
                and (e.get('repo'), e.get('module')) not in current_units
                and e.get('valid', True)):
            e['valid'] = False
            e['invalidated_at'] = now
        kept.append(e)

    # index prior entities of touched units so re-extraction UPDATES them in
    # place (keeping hits/created_at) and DISAPPEARED ones get invalidated
    prev_touched = {}
    for e in prev:
        if (e.get('repo'), e.get('module')) in touched_units:
            prev_touched[(f"{e.get('repo')}/{e.get('module')}", e.get('name'))] = e

    summaries = {u: s for u, s in mem.get('summaries', {}).items() if u in current_strs}
    relations = [r for r in mem.get('relations', []) if r.get('unit') in current_strs]

    n = len(todo)
    done_hashes = {}
    for i, (repo, module, fs) in enumerate(todo):
        unit = f"{repo}/{module}"
        summaries.pop(unit, None)
        relations = [r for r in relations if r.get('unit') != unit]
        corpus = _unit_corpus(root, _representative(fs))
        if not corpus.strip():
            continue
        ex = _extract(corpus, root, unit=unit, progress=f"({i + 1}/{n})")
        if ex.get('summary'):
            summaries[unit] = ex['summary']
        rel0 = _rel(root, fs[0])
        fresh_names = set()
        for e in ex['entities']:
            name = e.get('name')
            if not name:
                continue
            fresh_names.add(name)
            old = prev_touched.pop((unit, name), None)
            if old is not None:                            # fact still true → update
                old.update({'type': e.get('type', old.get('type', 'concept')),
                            'summary': e.get('summary', old.get('summary', '')),
                            'source_files': [rel0], 'valid': True})
                old.pop('invalidated_at', None)
                kept.append(old)
            else:
                kept.append({'id': f"entity:{repo}:{module}:{name}", 'name': name,
                             'type': e.get('type', 'concept'), 'summary': e.get('summary', ''),
                             'repo': repo, 'module': module, 'source_files': [rel0],
                             'valid': True, 'created_at': now, 'hits': 0})
        # prior entities of this unit not re-emitted → invalidated (kept as history)
        for (u, _nm), old in list(prev_touched.items()):
            if u == unit:
                if old.get('valid', True):
                    old['valid'] = False
                    old['invalidated_at'] = now
                kept.append(old)
                prev_touched.pop((u, _nm), None)
        names = fresh_names
        for r in ex['relations']:
            if r.get('source') in names and r.get('target') in names:
                relations.append({'source': r['source'], 'target': r['target'],
                                  'rel': r.get('rel', 'relates'), 'unit': unit})

        # checkpoint: persist after EVERY unit (each one cost a Claude call),
        # so an interruption keeps completed work. Entities of touched units
        # not yet re-extracted ride along unchanged, and provenance advances
        # only for processed units so the rest re-extract on the next run.
        for f in fs:
            rel = _rel(root, f)
            done_hashes[rel] = cur_hashes.get(rel)
        if i < n - 1:                        # last unit → the full save below
            snap = dict(mem)
            snap['entities'] = kept + list(prev_touched.values())
            snap['relations'] = relations
            snap['summaries'] = summaries
            snap['provenance'] = {**prov,
                                  **{r: {'hash': h} for r, h in done_hashes.items()}}
            snap['generated_at'] = _iso()
            save_memory(project_path, proj_folder, snap)
        _report_progress(f"memory {i + 1}/{n}")

    module_edges, unit_rank = _module_graph(project_path, proj_folder, units)
    for e in kept:
        e['rank'] = unit_rank.get((e.get('repo'), e.get('module')), 0)

    mem.update({'entities': kept, 'relations': relations, 'summaries': summaries,
                'provenance': {rel: {'hash': h} for rel, h in cur_hashes.items()},
                'module_edges': module_edges,
                'pending_units': skipped_units,
                'repo_summaries': _rollup_summaries(units, summaries, unit_rank),
                'generated_at': _iso()})
    _consolidate(mem)
    save_memory(project_path, proj_folder, mem)
    sync_to_claudemd(project_path, proj_folder, mem)
    try:
        from .memrules import sync_rules
        sync_rules(project_path, proj_folder, mem)
    except Exception:
        _c.log.exception('memory: rules sync failed')
    try:
        from . import workspace
        workspace.update_manifest(project_path, proj_folder, 'memory')
    except Exception:
        pass
    return mem


def _consolidate(mem):
    """Keep the graph bounded and accurate as the project grows — so memory
    cost stays flat (or shrinks) instead of ballooning with the codebase:
      1. merge duplicate entities (same normalized name) across modules;
      2. cap total non-lesson entities by importance (rank), evicting the least
         connected. Lessons are never touched here (they have their own decay).
    """
    from .config import load_settings
    cap = load_settings().get('memory_max_entities', 500) or 500
    ents = mem.get('entities', [])
    lessons = [e for e in ents if e.get('type') == 'lesson']
    # invalidated facts: kept as temporal history but bounded + never injected
    invalid = [e for e in ents if e.get('type') != 'lesson' and not e.get('valid', True)]
    invalid.sort(key=lambda e: e.get('invalidated_at', ''), reverse=True)
    invalid = invalid[:_INVALID_CAP]
    reg = [e for e in ents if e.get('type') != 'lesson' and e.get('valid', True)]

    # 1. cross-module merge by normalized name
    merged = {}
    for e in reg:
        key = re.sub(r'\W+', '', (e.get('name') or '').lower())
        if not key:
            continue
        cur = merged.get(key)
        if cur is None:
            e = dict(e)
            e['modules'] = [m for m in [f"{e.get('repo')}/{e.get('module')}"] if m]
            merged[key] = e
            continue
        cur['rank'] = cur.get('rank', 0) + e.get('rank', 0)
        cur['source_files'] = sorted(set((cur.get('source_files') or [])
                                         + (e.get('source_files') or [])))[:6]
        u = f"{e.get('repo')}/{e.get('module')}"
        if u not in cur['modules']:
            cur['modules'].append(u)
        if len(e.get('summary', '')) > len(cur.get('summary', '')):
            cur['summary'] = e['summary']            # keep the richer summary
    reg = list(merged.values())

    # 2. importance cap — score = dependency rank + access reinforcement (hits).
    #    Useful, frequently-recalled knowledge stays; dead knowledge is evicted.
    dropped = 0
    if len(reg) > cap:
        reg.sort(key=lambda e: (e.get('rank', 0) + e.get('hits', 0) * 2,
                                len(e.get('summary', ''))), reverse=True)
        dropped = len(reg) - cap
        reg = reg[:cap]

    mem['entities'] = reg + lessons + invalid
    kept_names = {e.get('name') for e in reg}
    mem['relations'] = [r for r in mem.get('relations', [])
                        if r.get('source') in kept_names and r.get('target') in kept_names]
    _add_unlinked_mentions(mem, reg)
    mem['evicted_entities'] = dropped
    return mem


def _add_unlinked_mentions(mem, reg, cap=120):
    """Obsidian-style: if entity A's name appears in entity B's summary but no
    relation links them, add a weak 'mentions' edge. Enriches recall's graph
    expansion at zero Claude cost. Bounded + deduped against existing edges."""
    existing = {(r.get('source'), r.get('target')) for r in mem.get('relations', [])}
    existing |= {(t, s) for s, t in existing}
    names = [e.get('name') for e in reg if e.get('name')]
    lower = [(n, n.lower()) for n in names if len(n) >= 4]
    added = 0
    for e in reg:
        summ = (e.get('summary') or '').lower()
        if not summ:
            continue
        src = e.get('name')
        for n, nl in lower:
            if n == src or (src, n) in existing:
                continue
            if re.search(r'\b' + re.escape(nl) + r'\b', summ):
                mem['relations'].append({'source': src, 'target': n,
                                         'rel': 'mentions',
                                         'unit': f"{e.get('repo')}/{e.get('module')}"})
                existing.add((src, n))
                existing.add((n, src))
                added += 1
                if added >= cap:
                    return


def _rollup_summaries(units, summaries, unit_rank, per_repo=3):
    """GraphRAG-style community rollup: one summary per repo built LOCALLY from
    its top module summaries (by dep rank) — no extra Claude call. Gives the
    digest an accurate repo one-liner and cheap global-question context that
    stays flat as the project grows."""
    by_repo = {}
    for repo, module, _fs in units:
        by_repo.setdefault(repo, []).append(module)
    out = {}
    for repo, mods in by_repo.items():
        mods.sort(key=lambda m: unit_rank.get((repo, m), 0), reverse=True)
        parts = []
        for m in mods[:per_repo]:
            s = (summaries.get(f"{repo}/{m}", '') or '').strip().rstrip('.')
            if s:
                parts.append(s)
        if parts:
            roll = '; '.join(parts)
            out[repo] = (roll[:220] + '…') if len(roll) > 220 else roll
    return out


def _module_graph(project_path, proj_folder, units):
    """Aggregate connections' file→file dep edges to unit→unit edges + a
    dep-degree rank per unit. Real cross-module structure the LLM extraction
    can't see (its relations are per-unit only). Best-effort."""
    try:
        from . import connections
        g = connections.build_hierarchy(project_path, proj_folder)   # cached
    except Exception:
        return [], {}
    unit_of = {}
    for repo, module, fs in units:
        root = os.path.abspath(project_path)
        for f in fs:
            unit_of[_rel(root, f)] = (repo, module)
    agg, rank = {}, {}
    for e in g.get('dep_edges', []):
        s = unit_of.get(str(e.get('source', ''))[5:])   # strip 'file:'
        t = unit_of.get(str(e.get('target', ''))[5:])
        w = e.get('weight', 1)
        if s:
            rank[s] = rank.get(s, 0) + w
        if t:
            rank[t] = rank.get(t, 0) + w
        if s and t and s != t:
            agg[(s, t)] = agg.get((s, t), 0) + w
    edges = [{'source': f"{s[0]}/{s[1]}", 'target': f"{t[0]}/{t[1]}", 'weight': w}
             for (s, t), w in sorted(agg.items(), key=lambda kv: kv[1], reverse=True)]
    return edges, rank


def start_background_refresh(project_path, proj_folder, project_name, auto_cap=6):
    """Refresh memory in a daemon thread so the TUI stays responsive — the user
    works while memory updates. No-op if memory doesn't exist yet, if a refresh
    for this project is already running, or if disabled. Returns the thread or
    None."""
    root = os.path.abspath(project_path or '')
    if not root:
        return None
    if scan_lock_status(project_path) is not None:
        return None                          # a detached worker is already on it
    with _bg_lock:
        if root in _bg_active:
            return None
        if not load_memory(project_path, proj_folder).get('entities'):
            return None                      # nothing to incrementally refresh yet
        _bg_active.add(root)

    def _work():
        _tls.silent = True                   # headless Claude calls, no TUI
        try:
            refresh_memory(project_path, proj_folder, project_name, auto_cap=auto_cap)
        except Exception:
            _c.log.exception('memory: background refresh failed')
        finally:
            with _bg_lock:
                _bg_active.discard(root)

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    return t


def _iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── digest → CLAUDE.md ───────────────────────────────────────

def tokens_estimate(text):
    return max(1, len(text or '') // 4)


def build_digest_micro(mem, max_tokens=250):
    """Tiny always-loaded memory INDEX (repo one-liners + module names + recall
    pointer). Detail lives in path-scoped rules and `claudectl recall` — this
    replaces the old full entity dump (~430 tok) with ≤250 tok."""
    ents = mem.get('entities', [])
    summaries = mem.get('summaries', {})
    if not ents and not summaries:
        return "_(no semantic memory yet — press m in the project menu to build it)_"

    rollups = mem.get('repo_summaries', {})
    by_repo = {}
    for e in ents:
        if e.get('type') == 'lesson' or not e.get('valid', True):
            continue
        by_repo.setdefault(e.get('repo', '?'), {}).setdefault(e.get('module', '(root)'), []).append(e)
    repos = sorted(by_repo, key=lambda r: sum(len(v) for v in by_repo[r].values()), reverse=True)

    out = []
    for repo in repos:
        mods = by_repo[repo]
        # repo one-liner = rollup summary (summary-of-modules), else largest module
        summ = (rollups.get(repo, '') or '').strip()
        if not summ:
            biggest = max(mods, key=lambda m: len(mods[m]))
            summ = (summaries.get(f"{repo}/{biggest}", '') or '').strip()
        out.append(f"- **{repo}**" + (f" — {summ}" if summ else ''))
        names = sorted(mods, key=lambda m: len(mods[m]), reverse=True)
        shown = names[:6]
        line = "  modules: " + ', '.join(shown)
        if len(names) > 6:
            line += f" (+{len(names) - 6})"
        out.append(line)
    lessons = [e for e in ents if e.get('type') == 'lesson'
               and e.get('status') in ('approved', 'pinned')]
    if lessons:
        out.append(f"- lessons: {len(lessons)} learned (injected when relevant)")
    out.append('Detail on demand: run `claudectl recall "<topic>"` (Bash) for the '
               'task-relevant subgraph of this project\'s memory.')
    text = '\n'.join(out)
    while tokens_estimate(text) > max_tokens and len(out) > 2:
        out.pop(-2)                      # drop lowest-priority repo lines, keep pointer
        text = '\n'.join(out)
    return text


def build_digest(mem, per_module=10):
    """Project memory map for CLAUDE.md — structured by repo → module, covering
    every analyzed area (not a single global top-N slice)."""
    ents = mem.get('entities', [])
    summaries = mem.get('summaries', {})
    if not ents and not summaries:
        return "_(no semantic memory yet — open the project, press n → m to build it)_"

    # group entities by repo → module (valid facts only)
    by_repo = {}
    for e in ents:
        if e.get('type') != 'lesson' and not e.get('valid', True):
            continue
        by_repo.setdefault(e.get('repo', '?'), {}).setdefault(e.get('module', '(root)'), []).append(e)
    # repos ordered by total entity count (most significant first)
    repos = sorted(by_repo, key=lambda r: sum(len(v) for v in by_repo[r].values()), reverse=True)

    out = []
    for repo in repos:
        out.append(f"### {repo}")
        mods = by_repo[repo]
        for module in sorted(mods, key=lambda m: len(mods[m]), reverse=True):
            unit = f"{repo}/{module}"
            head = f"**{module}**"
            summ = summaries.get(unit, '').strip()
            out.append(head + (f" — {summ}" if summ else ''))
            for e in mods[module][:per_module]:
                s = e.get('summary', '').strip()
                out.append(f"- {e['name']}" + (f" — {s}" if s else ''))
            if len(mods[module]) > per_module:
                out.append(f"- …(+{len(mods[module]) - per_module} more)")
        out.append('')
    return '\n'.join(out).strip()


def sync_to_claudemd(project_path, proj_folder, mem):
    """Write the memory digest into CLAUDE.md (sentinel block) if enabled."""
    from .config import load_settings
    if not load_settings().get('memory_to_claudemd', True):
        return
    try:
        from .claude_md import write_memory_block
        from . import diffview
        ok, old, new = write_memory_block(project_path, build_digest_micro(mem))
        if ok and old != new:
            diffview.record(project_path, proj_folder, 'claude_md', old, new)
    except Exception:
        _c.log.exception('memory: claude.md sync failed')


# ── ask (search / GRAPH_COMPLETION analogue) ─────────────────

def _tokens(s):
    import re
    return set(re.findall(r'[a-z0-9]+', (s or '').lower()))


def ask_memory(project_path, proj_folder, question):
    mem = load_memory(project_path, proj_folder)
    if not mem.get('entities'):
        return "No project memory yet — build it first (press 'm' in the connections screen)."
    from . import recall
    r = recall.retrieve(project_path, proj_folder, question, budget_tokens=1800)
    ctx = r['text'] if not r['empty'] else recall.render_context(
        [(1.0, e) for e in mem['entities'][:12]], mem, 1800)[0]
    return _answer(ctx, question, os.path.abspath(project_path)) or "(no answer)"
