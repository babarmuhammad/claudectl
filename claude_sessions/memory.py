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
import time

from . import config as _c

# background-refresh coordination: a thread sets _tls.silent so its Claude calls
# run headless (no progress UI / keyboard) and never touch the live TUI.
_tls = threading.local()
_bg_lock = threading.Lock()
_bg_active = set()          # project paths currently refreshing in the background
_bg_spawned = {}            # project path -> when a detached worker was last spawned
_BG_SPAWN_COOLDOWN = 60

SCHEMA_VERSION = 3
MEM_SUBDIR = os.path.join('.claudectl', 'memory')
GRAPH_NAME = 'graph.json'
PER_FILE_CHARS = 4000    # cap content per file
PER_BATCH_CHARS = 40000  # cap corpus per repo/module Claude call
MODULE_MAX_FILES = 24    # representative files per module
MIN_UNIT_FILES = 3       # below this a nested directory folds into its parent
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
            'pending_units': 0, 'repo_summaries': {},
            # lifetime spend. No SCHEMA_VERSION bump needed: _migrate
            # setdefaults every _empty() key onto whatever it loads.
            'cost_usd_total': 0.0, 'cost_history': []}


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
            # A corrupt graph is moved aside rather than treated as an empty
            # one: `save_memory` writes straight back, so silently defaulting
            # here is what would destroy it.
            from . import jsonstore
            data = jsonstore.load(p, default=None, expect=dict)
            return _migrate(data) if data else _empty()
    return _empty()


def _snapshot_if_shrinking(project_path, proj_folder, new):
    """Snapshot the graph before a save that LOSES entities.

    Only on shrink: every refresh saves, and versioning every one of them would
    push the interesting snapshot out of the ring within an hour. Eviction and
    decay are the operations a user needs to be able to walk back, and they are
    exactly the ones that shrink it. Best-effort, never blocks the save."""
    try:
        for d in _mem_dirs(project_path, proj_folder):
            p = os.path.join(d, GRAPH_NAME)
            if not os.path.isfile(p):
                continue
            old_text = open(p, encoding='utf-8', errors='ignore').read()
            try:
                old_n = len(json.loads(old_text).get('entities') or [])
            except Exception:
                return
            if old_n <= len(new.get('entities') or []):
                return
            from . import diffview
            diffview.record(project_path, proj_folder, 'memory_graph',
                            old_text, json.dumps(new, indent=1))
            return
    except Exception:
        pass


def save_memory(project_path, proj_folder, m):
    # Write to BOTH the working-dir and encoded-folder locations so the graph
    # is discoverable for cross-project scanning (conventions) regardless of
    # which one a caller resolves. Success if at least one write lands.
    _snapshot_if_shrinking(project_path, proj_folder, m)
    ok = False
    for d in _mem_dirs(project_path, proj_folder):
        try:
            os.makedirs(d, exist_ok=True)
            # atomic: a killed process (the detached bg worker, or claudectl
            # exiting to launch claude) must never leave torn JSON here —
            # load_memory would silently reset it to _empty().
            if not _c.write_json_atomic(os.path.join(d, GRAPH_NAME), m):
                continue
            ok = True
        except Exception:
            continue
    return ok


# ── cross-process scan lock ──────────────────────────────────
# The background memory update runs in a DETACHED worker process (see
# spawn_background_worker) so it survives the TUI exiting to launch claude.
# A marker file makes its status visible to any claudectl process: dedup
# guard for spawners, live progress for the sessions-menu badge.

SCAN_LOCK = 'scan.lock'
SCAN_LOCK_STALE_SEC = 900
#: projects THIS process currently holds a lock for. A single global root meant
#: two concurrent refreshes (the on-open async one can overlap a scheduler pass)
#: shared one slot: the second acquirer overwrote it, so one project's progress
#: was written into the other's lock file and whichever finished first cleared
#: reporting for both.
_scan_lock_roots = set()
_scan_lock_root = None      # deprecated alias, kept for tests that monkeypatch it


def _scan_lock_path(project_path):
    if not project_path:
        return None
    return os.path.join(os.path.abspath(project_path), MEM_SUBDIR, SCAN_LOCK)


def _pid_alive(pid):
    from . import proc
    return proc.pid_alive(pid)


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
    """Claim the scan lock for THIS process. False if a live worker holds it.

    O_CREAT|O_EXCL, so the claim is the same syscall as the test. The old
    read-then-write left a window in which the GUI and a detached worker could
    both see 'free' and both proceed to read-modify-write the same graph."""
    global _scan_lock_root
    import time
    p = _scan_lock_path(project_path)
    if not p:
        return False
    _read_scan_lock(project_path)       # reclaims a dead worker's lock, if any
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False                    # a live worker holds it
    except Exception:
        return False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'pid': os.getpid(), 'started': _iso(),
                       'updated': time.time(), 'progress': ''}, f)
    except Exception:
        try:
            os.remove(p)                # never leave a lock we cannot describe
        except OSError:
            pass
        return False
    root = os.path.abspath(project_path)
    _scan_lock_roots.add(root)
    _scan_lock_root = root
    return True


def clear_scan_lock(project_path):
    global _scan_lock_root
    p = _scan_lock_path(project_path)
    if p and os.path.isfile(p):
        try:
            os.remove(p)
        except Exception:
            pass
    root = os.path.abspath(project_path or '')
    _scan_lock_roots.discard(root)
    if _scan_lock_root == root:
        _scan_lock_root = next(iter(_scan_lock_roots), None)


class MemoryBusy(RuntimeError):
    """Another process is already scanning this project."""


class scan_guard:
    """Hold the scan lock for one refresh — unless this process already holds it.

    The lock lived at the CALLERS: the GUI job, the scheduler and the detached
    worker each acquired it, while the three manual TUI builds
    (`connections` 'm', `memhub` 'b', `session_menu` '!') took no lock at all
    and read-modify-wrote the same graph.json as a running worker, last writer
    winning whole-file. Guarding the function every one of them calls is one
    place instead of four, and it stays correct for the next caller.

    Re-entrant by design: `auto_cycle`'s callers legitimately hold the lock
    around the whole cycle, so a nested acquire must be a no-op, not a failure.
    """

    def __init__(self, project_path):
        self.root = os.path.abspath(project_path) if project_path else ''
        self.mine = False

    def __enter__(self):
        if not self.root or self.root in _scan_lock_roots:
            return self
        if not acquire_scan_lock(self.root):
            raise MemoryBusy('a memory scan is already running for this project')
        self.mine = True
        return self

    def __exit__(self, *exc):
        if self.mine:
            clear_scan_lock(self.root)
        return False


def _report_progress(text, project_path=None):
    """Update the scan-lock progress line — no-op unless this process holds a
    lock (foreground refresh/scan calls this too; it must stay silent there)."""
    import time
    root = os.path.abspath(project_path) if project_path else _scan_lock_root
    if not root or root not in _scan_lock_roots:
        return
    p = _scan_lock_path(root)
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
    if scan_lock_status(project_path) is not None:
        return None
    # Claim under the lock. The check used to be an unsynchronised read of
    # _bg_active that nothing ever wrote here, so two callers arriving together
    # both passed it and both spawned a detached worker — and the child only
    # takes scan.lock some milliseconds later. The claim is time-boxed because
    # the child, not this process, decides when the work is done.
    with _bg_lock:
        # -inf, not 0: time.monotonic() is seconds since BOOT on every platform,
        # so 0 as the "never spawned" sentinel put the whole first minute of
        # uptime inside the cooldown and suppressed every spawn. Invisible in
        # development (uptime is hours) and on the Windows CI runners (minutes
        # to reach the job); deterministic on a Linux runner, which starts the
        # job ~30s after boot, and on claudectl launched from a login shortcut.
        if time.monotonic() - _bg_spawned.get(root, float('-inf')) < _BG_SPAWN_COOLDOWN:
            return None
        _bg_spawned[root] = time.monotonic()
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


def _budget_args():
    """`--max-budget-usd`, when the user has set a cap. A timeout bounds how
    LONG one of claudectl's own calls may run; this bounds what it may spend,
    and subagent spend counts toward the same cap."""
    try:
        from .config import load_settings
        cap = float(load_settings().get('headless_budget_usd') or 0)
    except Exception:
        return []
    return ['--max-budget-usd', f'{cap:g}'] if cap > 0 else []


def _claude_stdin(prompt, cwd, timeout=EXTRACT_TIMEOUT,
                  crumbs=('CLAUDECTL', 'MEMORY'), label='Working with Claude...',
                  model=None, extra_args=()):
    """Run `claude -p` reading the prompt from stdin (avoids the Windows
    command-line length limit). Foreground: visible progress bar (ESC cancels).
    Background threads (_tls.silent): headless subprocess, no UI/keyboard.
    `model` overrides the economy extract_model; '' forces the account default.
    `extra_args` is how _claude_json adds the structured-output flags without
    becoming a second spawn site.
    Returns stdout text or ''.

    Every prompt leaves with `sessions.HEADLESS_MARK`. `claude -p` writes a
    transcript into ~/.claude/projects exactly like a session you had, so
    claudectl's own calls — extract a module, distil lessons, compress
    CLAUDE.md — were being listed back to you as "session topics" and costing
    always-on CLAUDE.md tokens to describe claudectl talking to itself. This is
    the one seam every headless call passes through, so marking here is both
    complete and impossible to forget at a new call site."""
    global last_call_error
    from .config import get_claude_exe
    from .sessions import HEADLESS_MARK
    last_call_error = ''
    exe = get_claude_exe()
    if not exe:
        last_call_error = 'the claude executable was not found'
        from . import events
        events.record('memory', last_call_error)
        return ''
    prompt = (prompt or '') + '\n\n' + HEADLESS_MARK
    args = [exe, '-p', '--max-turns', '20', '--disallowedTools', 'Write,Edit,NotebookEdit,Bash']
    m = extract_model() if model is None else (model or '').strip()
    if m:
        args += ['--model', m]
    args += _budget_args()
    args += list(extra_args)
    if getattr(_tls, 'silent', False):
        from .gui_api import _run_cancellable
        try:
            return _run_cancellable(args, input_text=prompt, cwd=cwd, timeout=timeout)
        except Exception:
            return ''
    from .ui import run_with_progress_stdin
    out, _cancelled = run_with_progress_stdin(
        args, prompt, crumbs, label, timeout=timeout, cwd=cwd)
    return out or ''


#: why the last headless call failed, or ''. Same module-level-latch idiom as
#: `last_call_cost` below, and for the same reason: the failure happens three
#: frames below the code that has to report it. Set by gui_api._run_cancellable
#: on a nonzero exit, cleared at the start of each call.
last_call_error = ''

def why_failed(default='No output from Claude'):
    """What the last headless call actually said, for a caller that would
    otherwise report "No output from Claude" — which, for a rate-limited
    account, was every one of them."""
    return last_call_error or default


#: last `total_cost_usd` reported by a _claude_json envelope, or None. The JSON
#: output format carries the real figure, which is strictly better than the
#: COST_PER_MTOK estimate claudectl otherwise has to make about its own calls.
last_call_cost = None


def _claude_json(prompt, cwd, schema, **kw):
    """`claude -p --output-format json --json-schema <schema>` → the validated
    object, or None.

    This replaces recovering JSON from prose. `_parse_json` strips code fences
    and slices between the first '{' and the last '}' — a heuristic that returns
    None the moment the model wraps its answer in a sentence, and one that has
    no way to notice it got a DIFFERENT shape than asked for.

    The fallback is load-bearing, not defensive padding: Claude Code before
    v2.1.205 silently ignored a schema it considered invalid and returned
    unstructured text, so an older CLI lands on exactly the old behaviour rather
    than on nothing.
    """
    global last_call_cost
    last_call_cost = None
    raw = _claude_stdin(prompt, cwd, extra_args=(
        '--output-format', 'json', '--json-schema', json.dumps(schema)), **kw)
    if not raw:
        return None
    env = None
    try:
        env = json.loads(raw.strip())
    except Exception:
        env = None
    if isinstance(env, dict):
        try:
            c = env.get('total_cost_usd')
            last_call_cost = float(c) if isinstance(c, (int, float)) else None
        except Exception:
            last_call_cost = None
        if isinstance(env.get('structured_output'), (dict, list)):
            return env['structured_output']
        # envelope parsed but carried no structured output — the text result is
        # still the model's answer, so try it the old way before giving up
        if isinstance(env.get('result'), str):
            return _parse_json(env['result'])
    return _parse_json(raw)


def _parse_json(text):
    """Recover JSON from model prose. The FALLBACK path — _claude_json asks
    Claude Code to enforce a schema and only lands here when that is
    unavailable.

    Tries the whole (de-fenced) text first, because a well-behaved answer needs
    no surgery, and only then slices to the outermost object or array. The
    array case matters: bracket-slicing a two-element array on '{'..'}' yields
    '{...}, {...}', which is not JSON, so a list-shaped answer used to come back
    as None from here even though it parsed perfectly as-is.
    """
    if not text:
        return None
    t = text.strip()
    if '```' in t:                       # strip code fences
        import re
        m = re.search(r'```(?:json)?\s*(.*?)```', t, re.S)
        if m:
            t = m.group(1).strip()
    for cand in (t, _slice_between(t, '{', '}'), _slice_between(t, '[', ']')):
        if not cand:
            continue
        try:
            return json.loads(cand)
        except Exception:
            continue
    return None


def _slice_between(t, open_ch, close_ch):
    """The outermost open_ch..close_ch span, or '' when there isn't one."""
    if open_ch not in t or close_ch not in t:
        return ''
    i, j = t.index(open_ch), t.rindex(close_ch)
    return t[i:j + 1] if j > i else ''


#: The shape _extract asks for, as a JSON Schema Claude Code enforces. The
#: prompt still SPELLS OUT the shape below, deliberately: that text is what the
#: fallback path (an older CLI, or a rejected schema) relies on.
GRAPH_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string'},
        'entities': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'name': {'type': 'string'},
                           'type': {'type': 'string',
                                    'enum': ['module', 'component', 'concept',
                                             'service', 'model']},
                           'summary': {'type': 'string'}},
            'required': ['name', 'type', 'summary']}},
        'relations': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'source': {'type': 'string'},
                           'target': {'type': 'string'},
                           'rel': {'type': 'string',
                                   'enum': ['uses', 'calls', 'depends_on',
                                            'contains', 'implements']}},
            'required': ['source', 'target', 'rel']}},
    },
    'required': ['summary', 'entities', 'relations'],
}


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
    data = _claude_json(prompt, cwd, GRAPH_SCHEMA,
                        crumbs=('CLAUDECTL', 'MEMORY', unit or 'EXTRACT'), label=label)
    # None means the CALL failed (timeout, budget, unparseable) — which is a
    # different fact from "this module has no entities", and the caller must be
    # able to tell them apart. Returning an empty result for a failure made the
    # loop invalidate every fact it already knew about the module AND record its
    # hashes as current, so the module's memory was wiped and never retried.
    if not isinstance(data, dict):
        return None
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
    # A nested directory holding a file or two is part of its parent, not a
    # module: a unit costs a Claude call and a rule file, and an app-router tree
    # is one directory per page. Once a subproject became its own cluster the
    # module keys turned relative to IT, so `www/app` split into twenty units of
    # one file each — this folds them back and leaves the real modules alone.
    for (repo, module), fs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if '/' in module and len(fs) < MIN_UNIT_FILES:
            groups.setdefault((repo, module.rsplit('/', 1)[0]), []).extend(fs)
            del groups[(repo, module)]
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

def _project_opts(project_path, encoded=None):
    from .config import load_settings
    st = load_settings()
    if encoded is None:
        try:
            from .paths import encode_component
            encoded = encode_component(os.path.abspath(project_path or ''))
        except Exception:
            encoded = ''
    return st, (st.get('project_defaults') or {}).get(encoded) or {}


def auto_enabled(project_path, encoded=None):
    """Is this project opted into BACKGROUND auto-memory?

    One answer for every runner that schedules work on its own: the GUI's
    periodic pass, the TUI's, and the detached worker. The flag it reads is the
    same `project_defaults[enc].auto_memory` the GUI checkbox writes — which
    previously only the GUI scheduler consulted, so ticking the box did nothing
    outside a running GUI window and the TUI had no way to set it at all.

    Explicit opt-in, deliberately: a machine-wide default here would mean an
    hourly Claude spend on every project claudectl can see."""
    _st, proj = _project_opts(project_path, encoded)
    return bool(proj.get('auto_memory'))


def refresh_on_open(project_path, encoded=None):
    """Should opening this project kick off a refresh?

    A different question from `auto_enabled` — this one is scoped to something
    you just did, so for a project with no opinion the global
    `memory_auto_refresh` decides and the long-standing behaviour is unchanged.

    Opting a project into background auto-memory used to imply this too, and
    that is exactly what made the configured cadence not mean anything: the
    scheduler waited the interval, and then opening the project spent a cycle
    anyway. Background auto-memory now means the scheduler OWNS the spend —
    launch, then the interval, and nothing else. Pressing Build with Claude
    still refreshes on demand; that is a decision, not a side effect of
    navigating."""
    st, proj = _project_opts(project_path, encoded)
    if 'auto_memory' in proj:
        # True  -> the scheduler owns it, so opening must not add a cycle
        # False -> explicitly turned off; an explicit no beats the global default
        return False
    return st.get('memory_auto_refresh') == 'open'


#: sidecar the stale-on-edit hook appends to. Owned HERE, not in the hook: a
#: hook script is an entry point, and it reconfigures stdout at import because
#: Claude Code hands it a pipe. Importing one as a library ran that line in a
#: process that has no stdout at all — a windowed pythonw, i.e. the GUI — and
#: `AttributeError: 'NoneType' object has no attribute 'reconfigure'` took down
#: the whole auto-memory cycle. The dependency now points the other way.
DIRTY_LOG = 'dirty.log'


def dirty_log_path(project_path):
    return os.path.join(os.path.abspath(project_path or ''), MEM_SUBDIR, DIRTY_LOG)


def drain_dirty(project_path):
    """Paths the stale-on-edit hook recorded since the last cycle, and clear it.

    Read-and-remove, like `recall.fold_hits`: the log is a hint that work is
    owed, and a hint consumed twice is a wasted call. Returns a set of absolute
    paths (empty when the hook is not installed, which is the normal case)."""
    p = dirty_log_path(project_path)
    out = set()
    try:
        if not os.path.isfile(p):
            return out
        with open(p, encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if line:
                    out.add(os.path.abspath(line))
        os.remove(p)
    except Exception:
        pass
    return out


def has_dirty(project_path):
    """True if the edit hook recorded anything — checked WITHOUT draining, so a
    staleness probe never eats the signal a refresh still needs."""
    try:
        p = dirty_log_path(project_path)
        return os.path.isfile(p) and os.path.getsize(p) > 0
    except OSError:
        return False


def dirty_count(project_path):
    """How many edits are queued. Same no-drain contract as has_dirty —
    `drain_dirty` stays the only remover, or a display would eat the work."""
    try:
        p = dirty_log_path(project_path)
        with open(p, encoding='utf-8', errors='replace') as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


COST_HISTORY = 24


def charge(mem):
    """Fold the cost of the Claude call that just returned into the graph's
    running total, and return it.

    `last_cost_usd` only ever held the MOST RECENT cycle, so what memory has
    cost over its lifetime was unmeasurable — the number was overwritten by the
    next cycle before anything could add it up. One writer, called wherever a
    memory call is paid for; `ask_memory` deliberately does not call it, because
    a query is not memory being built.
    """
    c = last_call_cost
    if not c:
        return 0.0
    mem['cost_usd_total'] = round((mem.get('cost_usd_total') or 0) + c, 4)
    mem['cost_history'] = ((mem.get('cost_history') or []) + [round(c, 4)])[-COST_HISTORY:]
    return c


def _file_sig(path):
    """(mtime_ns, size) — the cheap half of the change check."""
    try:
        st = os.stat(path)
        return [st.st_mtime_ns, st.st_size]
    except OSError:
        return None


def _changed_units(root, units, mem):
    """(todo, deleted, cur_hashes): units whose representative files changed or
    that the current graph doesn't cover yet, plus tracked files now gone.
    No Claude calls. Shared by refresh_memory and is_stale.

    A file is re-hashed only when its (mtime, size) differs from the recorded
    signature. Content hash stays the source of truth — a file touched but not
    changed must not cost a Claude call — but an unchanged tree now costs one
    `stat` per file instead of a full SHA-256 read of the whole project, which
    is what this paid on every scheduler tick and every project open. Same idea
    as the hash tree Cursor walks: compare cheaply, descend only where it
    differs. Records with no signature (written before this existed) hash once
    and gain one.
    """
    from .workspace import _sha256_file
    prov = mem.get('provenance', {})
    cur_hashes = {}
    cur_sigs = {}
    todo = []
    for repo, module, fs in units:
        changed = False
        for f in fs:
            rel = _rel(root, f)
            old = prov.get(rel) or {}
            sig = _file_sig(f)
            cur_sigs[rel] = sig
            if sig is not None and old.get('sig') == sig and old.get('hash'):
                cur_hashes[rel] = old['hash']       # unchanged on disk
                continue
            hv = _sha256_file(f)
            cur_hashes[rel] = hv
            if old.get('hash') != hv:
                changed = True
        if changed:
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
    cur = {rel: {'hash': h, 'sig': cur_sigs.get(rel)} for rel, h in cur_hashes.items()}
    return todo, deleted, cur


def _stamp_fresh(project_path, proj_folder):
    """Re-baseline workspace freshness against the CURRENT repo state.

    Called whenever memory is confirmed to match the code — after a refresh, and
    after a check that found nothing to do. Both mean the same thing to the
    freshness score, and only the first one used to say so."""
    try:
        from . import workspace
        workspace.update_manifest(project_path, proj_folder, 'memory')
    except Exception:
        _c.log.exception('memory: freshness stamp failed')


def _prioritise(todo, mem, edited=(), sigs=None, root=''):
    """Order the units a cycle will extract, most valuable first.

    Two problems with taking them in `_units` order, which is by file COUNT:
    the budget then keeps the biggest modules and drops the important small
    ones (a repo's entry point is rarely its largest directory), and the
    coverage-repair units appended by `_changed_units` land at the very end,
    making them the first casualties of the cap that was meant to catch up on
    them. Rank by dependency degree — the measure the graph already computes,
    and the same signal Aider's repo map ranks on — with never-covered units
    first because a unit with no entities at all contributes nothing until it
    is extracted once.

    Recency breaks the remaining tie, ahead of size: a module written this
    afternoon is what a session is about, and file count is not a proxy for it.
    The mtimes come from the signatures `_changed_units` already stat'ed, so
    this costs no syscall; an unknown file sorts as oldest.
    """
    rank = {}
    covered = set()
    for e in mem.get('entities', []):
        key = (e.get('repo'), e.get('module'))
        covered.add(key)
        rank[key] = max(rank.get(key, 0), e.get('rank', 0) or 0)
    edited = {os.path.abspath(p) for p in (edited or ())}
    sigs = sigs or {}

    def _mtime(f):
        sig = (sigs.get(_rel(root, f)) or {}).get('sig') or ()
        return sig[0] if sig else 0

    def _key(u):
        repo, module, fs = u
        just_edited = bool(edited) and any(os.path.abspath(f) in edited for f in fs)
        return (not just_edited,                          # what you just touched
                (repo, module) in covered,                # then never-covered
                -rank.get((repo, module), 0),             # then most-depended-on
                -max((_mtime(f) for f in fs), default=0),  # then most recent
                -len(fs))                                 # then biggest

    return sorted(todo, key=_key)


def _reserve_for_new(todo, budget, mem):
    """Move up to half a cycle's budget worth of never-covered units to the front.

    `_prioritise` puts them ahead of covered units, but BEHIND what you just
    edited — and the edit log is refilled by every turn you work. So a project
    under active development spent all six calls an hour on units it already
    knew, and a subproject added to it stayed absent from the graph indefinitely
    while auto-memory reported itself as working. Half the budget is reserved so
    the new thing lands and the thing you are editing still stays current.
    """
    covered = {(e.get('repo'), e.get('module')) for e in mem.get('entities', [])}
    keep = max(1, budget // 2)
    head, rest = [], []
    for u in todo:
        (head if (u[0], u[1]) not in covered and len(head) < keep else rest).append(u)
    return head + rest


def is_stale(project_path, proj_folder):
    """True if the project's source changed since its memory graph was built —
    a cheap hash-only check (no Claude). Used to decide whether an auto-refresh
    is worth running.

    An empty graph counts as stale. It used to return False, which meant
    auto-memory could never BOOTSTRAP: a project you had opted in stayed at zero
    entities forever, and the only way to start was a manual build. That guard
    existed because a first build was all-or-nothing and expensive; now a cycle
    takes `auto_cap` units at a time and the rest wait for the next one, so a
    first build is just a longer sequence of budgeted cycles."""
    try:
        # the edit hook already said so — no walk, no stat, no hashing
        if has_dirty(project_path):
            return True
        mem = load_memory(project_path, proj_folder)
        if not mem.get('entities'):
            return bool(_units(project_path, proj_folder))
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

    `auto_cap`: how many changed units one cycle may extract. The rest are left
    in `pending_units` for the next cycle rather than abandoning the run — see
    the note in the body.

    Raises MemoryBusy if another process is already scanning this project."""
    with scan_guard(project_path):
        return _refresh_locked(project_path, proj_folder, project_name, auto_cap)


def _refresh_locked(project_path, proj_folder, project_name, auto_cap=None):
    from .config import load_settings
    root = os.path.abspath(project_path)
    mem = load_memory(project_path, proj_folder)
    prov = mem.get('provenance', {})
    units = _units(project_path, proj_folder)

    # consumed here rather than in is_stale: a probe must not eat the signal a
    # refresh still needs. The paths only reorder work — the hash gate below is
    # still what decides whether a unit is genuinely changed.
    edited = drain_dirty(project_path)

    todo, deleted, cur_hashes = _changed_units(root, units, mem)
    if not todo and not deleted and mem.get('entities'):
        mem['last_extracted'] = 0        # nothing to do is not "we did work"
        # …and nothing to do also means nothing is queued. This never cleared
        # the counters, so one failed cycle left "6 module(s) still queued" on
        # screen indefinitely, long after the work had actually completed.
        mem['pending_units'] = mem['last_failed'] = mem['last_skipped'] = 0
        mem['last_error'] = ''
        # Reinforcement still has to land. fold_hits ran ONLY in the full path
        # below, and this early return is the common case on a settled repo —
        # so on this very project the sidecar held 807 recorded hits while the
        # highest `hits` value in the graph was 1. The counter that decides
        # what eviction keeps was, in practice, never incremented.
        try:
            from .recall import fold_hits
            if fold_hits(project_path, proj_folder, mem):
                save_memory(project_path, proj_folder, mem)
        except Exception:
            _c.log.exception('memory: folding recall hits failed')
        # …but it IS a verification that the memory matches the code right now,
        # which is a stronger statement than "we did work" and the one the
        # freshness score asks for. Without it, a commit that touched no source
        # file (docs, a README) moved HEAD, nothing needed re-extracting, and
        # the workspace screen reported the project stale while auto-memory was
        # switched on and working perfectly. Costs no Claude call.
        _stamp_fresh(project_path, proj_folder)
        return mem
    todo = _prioritise(todo, mem, edited, cur_hashes, root)

    # How much this cycle may do. `auto_cap` used to be all-or-nothing: more
    # changed units than the cap and the whole run returned having done NOTHING,
    # so the harder you worked the less memory updated — and because provenance
    # is now only advanced for units actually extracted, the remainder is simply
    # picked up by the next cycle. A busy repo converges instead of stalling.
    max_calls = load_settings().get('memory_max_calls') or None
    budget = min(x for x in (auto_cap, max_calls) if x) if (auto_cap or max_calls) else None
    skipped_units = 0
    if budget:
        skipped_units = max(0, len(todo) - budget)
        todo = _reserve_for_new(todo, budget, mem)[:budget]

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
    done_units = []
    failed_units = 0
    last_error = ''
    spent = 0.0
    for i, (repo, module, fs) in enumerate(todo):
        unit = f"{repo}/{module}"
        corpus = _unit_corpus(root, _representative(fs))
        if not corpus.strip():
            continue
        # Extract BEFORE discarding what we already know. The old order popped
        # the unit's summary and relations first, so a failed or empty call left
        # the module stripped of everything and nothing to put back.
        ex = _extract(corpus, root, unit=unit, progress=f"({i + 1}/{n})")
        # the real figure from the headless JSON envelope, which was recorded
        # and then read by nothing outside the tests — so what a cycle costs
        # has never been visible anywhere in the product
        spent += charge(mem)
        if ex is None:
            # the call failed: keep the old facts, do NOT record these hashes,
            # and count the unit so the next cycle picks it up again
            failed_units += 1
            if last_call_error:
                last_error = last_call_error
            _report_progress(f"memory {i + 1}/{n}", project_path)
            continue
        summaries.pop(unit, None)
        relations = [r for r in relations if r.get('unit') != unit]
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
        done_units.append(unit)
        for f in fs:
            rel = _rel(root, f)
            if rel in cur_hashes:
                done_hashes[rel] = cur_hashes[rel]
        if i < n - 1:                        # last unit → the full save below
            snap = dict(mem)
            snap['entities'] = kept + list(prev_touched.values())
            snap['relations'] = relations
            snap['summaries'] = summaries
            snap['provenance'] = {**prov, **done_hashes}
            snap['generated_at'] = _iso()
            save_memory(project_path, proj_folder, snap)
        _report_progress(f"memory {i + 1}/{n}", project_path)
        from .gui_api import JobCancelled, _JOBCTX
        _job = getattr(_JOBCTX, 'job', None)
        if _job and _job.get('cancel_event', threading.Event()).is_set():
            raise JobCancelled

    # Entities of touched units that were never reached — an empty corpus or a
    # failed call — ride along unchanged. The checkpoint at the top of the loop
    # already did this; the final write did not, so anything skipped on the LAST
    # pass was silently dropped.
    kept = kept + list(prev_touched.values())

    module_edges, unit_rank = _module_graph(project_path, proj_folder, units)
    for e in kept:
        e['rank'] = unit_rank.get((e.get('repo'), e.get('module')), 0)

    # Provenance advances ONLY for units actually extracted, exactly as the
    # per-unit checkpoint does. Writing every hash here is what let a truncated
    # or failed run mark unprocessed units current: the hash gate then said
    # "unchanged" forever and the stale facts were never revisited.
    new_prov = {rel: v for rel, v in prov.items() if rel in cur_hashes}
    new_prov.update(done_hashes)

    mem.update({'entities': kept, 'relations': relations, 'summaries': summaries,
                'provenance': new_prov,
                'module_edges': module_edges,
                'last_extracted': len(done_units),
                'last_cost_usd': round(spent, 4),
                'pending_units': skipped_units + failed_units,
                # A unit that FAILED and a unit that was capped are not the same
                # thing, and fusing them into one number is why six rate-limited
                # calls an hour read as a calm "queued — the next cycle takes
                # them". Both terms are kept so the difference can be said out
                # loud, along with what the failure actually was.
                'last_failed': failed_units,
                'last_skipped': skipped_units,
                'last_error': last_error if failed_units else '',
                'repo_summaries': _rollup_summaries(units, summaries, unit_rank),
                'generated_at': _iso()})
    # Reinforcement counts recorded by the per-prompt recall hook. Folded in
    # HERE, before consolidation reads `hits` to decide what to evict, and at
    # no extra cost — this function is already rewriting the graph.
    try:
        from .recall import fold_hits
        fold_hits(project_path, proj_folder, mem)
    except Exception:
        _c.log.exception('memory: folding recall hits failed')
    _consolidate(mem)
    # This run paid for one Claude call per unit. Losing it quietly is the one
    # outcome worse than not running at all, and save_memory's False return had
    # no reader anywhere.
    if not save_memory(project_path, proj_folder, mem):
        raise OSError('memory: could not write graph.json to either location')
    sync_to_claudemd(project_path, proj_folder, mem)
    try:
        from .memrules import sync_rules
        sync_rules(project_path, proj_folder, mem)
    except Exception:
        _c.log.exception('memory: rules sync failed')
    _stamp_fresh(project_path, proj_folder)
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
        if e.get('status') == 'pinned':
            cur['status'] = 'pinned'                 # a pin survives the merge
    reg = list(merged.values())

    # 2. importance cap — score = dependency rank + access reinforcement (hits).
    #    Useful, frequently-recalled knowledge stays; dead knowledge is evicted.
    #    Pinned entities are held out of the cap entirely: if you pinned more
    #    than the cap allows, the pins win and the cap gives way, not the
    #    reverse. Nothing a user explicitly protected is dropped to satisfy it.
    #    Pinning reached lessons only before this, so the cap could silently
    #    drop the very fact you had marked as the one that matters.
    pinned = [e for e in reg if e.get('status') == 'pinned']
    reg = [e for e in reg if e.get('status') != 'pinned']
    room = max(0, cap - len(pinned))
    evicted = []
    if len(reg) > room:
        reg.sort(key=lambda e: (e.get('rank', 0) + e.get('hits', 0) * 2,
                                len(e.get('summary', ''))), reverse=True)
        evicted = [e.get('name', '') for e in reg[room:]]
        reg = reg[:room]

    reg = pinned + reg
    mem['entities'] = reg + lessons + invalid
    kept_names = {e.get('name') for e in reg}
    mem['relations'] = [r for r in mem.get('relations', [])
                        if r.get('source') in kept_names and r.get('target') in kept_names]
    _add_unlinked_mentions(mem, reg)
    mem['evicted_entities'] = len(evicted)
    # names, not just a count: "12 entities evicted" is not something a user can
    # check, and checking is the whole point of telling them
    mem['evicted_names'] = evicted[:50]
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


def auto_cycle(project_path, proj_folder, project_name, auto_cap=6):
    """EVERYTHING memory-related, in one pass. The auto path's single entry point.

    Auto-memory used to refresh the semantic graph and nothing else, so on a
    project with it enabled the graph stayed current while lessons silently
    stopped accruing — `lessons.scan_sessions` was reachable only from the
    manual GUI job and the TUI review screen:

        "se abilitato auto memory, tutte le cose riguardanti memoria devono
         essere aggiornate automaticamente"

    Order matters. The graph refresh writes the CLAUDE.md digest, and that digest
    COUNTS LESSONS — so mining lessons afterwards leaves the block stale by
    exactly the number just learned. Hence the re-sync at the end, and only when
    something actually changed.

    What this must never do is touch anything a person wrote. Every write below
    goes through a namespaced writer:
      · claude_md.write_memory_block  — replaces only the region between the
        CLAUDECTL:MEMORY sentinels, leaving prose, AUTOGEN and SESSIONS intact
      · memrules.sync_rules           — owns `claudectl-mem-*.md` and nothing
        else in .claude/rules/
      · lessons.apply_decay           — never evicts a pinned lesson
    tests/test_memauto.py holds that line byte-for-byte.

    Returns a dict describing what it did — the caller stamps it for the UI.
    """
    from .config import load_settings
    st = load_settings()
    out = {'graph': False, 'lessons': 0, 'scanned': 0, 'pending': 0}

    mem = refresh_memory(project_path, proj_folder, project_name, auto_cap=auto_cap)
    # "how many units did this cycle actually re-extract" — not "was everything
    # done". A capped cycle now makes partial progress, and reporting that as a
    # no-op is what let the UI claim nothing happened after paying for calls.
    out['graph'] = int(mem.get('last_extracted') or 0) > 0
    out['extracted'] = int(mem.get('last_extracted') or 0)
    out['pending'] = int(mem.get('pending_units') or 0)
    # A failed cycle and a capped one both left `pending` set, and every
    # consumer worded it as "the next cycle takes them" — so a rate-limited
    # account produced six dead calls an hour, forever, reported as progress.
    out['failed'] = int(mem.get('last_failed') or 0)
    out['skipped'] = int(mem.get('last_skipped') or 0)
    out['error'] = mem.get('last_error') or ''

    # 'off' is a real answer and is honoured; 'prompt' still mines here, because
    # the prompt is about REVIEWING lessons, not about whether to notice them.
    # merge_lessons applies memory_lessons_autoapprove, so a low-confidence
    # lesson still lands as pending and waits for a human.
    if st.get('memory_lessons', 'prompt') != 'off':
        try:
            from . import lessons as _lessons
            mem = load_memory(project_path, proj_folder)
            sids = _lessons.pending_sids(proj_folder, mem)
            if sids:
                added, scanned = _lessons.scan_sessions(project_path, proj_folder, sids)
                out['lessons'], out['scanned'] = added, scanned
        except Exception:
            _c.log.exception('memory: auto lesson scan failed')

    if out['lessons']:
        # the digest counts lessons, so it is now short by exactly `added`
        try:
            mem = load_memory(project_path, proj_folder)
            sync_to_claudemd(project_path, proj_folder, mem)
            from .memrules import sync_rules
            sync_rules(project_path, proj_folder, mem)
        except Exception:
            _c.log.exception('memory: post-lesson sync failed')

    try:
        mem = load_memory(project_path, proj_folder)
        mem['auto_updated'] = _iso()
        mem['auto_last'] = out
        save_memory(project_path, proj_folder, mem)
    except Exception:
        pass
    return out


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


def _clip(s, n):
    """First `n` chars, cut at a word boundary. A rollup summary can run to 240
    characters — 60 of a 250-token budget on one sentence, which is why the
    module links and the reinforced facts never used to fit."""
    s = ' '.join((s or '').split())
    if len(s) <= n:
        return s
    cut = s[:n]
    return cut[:cut.rfind(' ')].rstrip(' ,;:—-') + '…' if ' ' in cut else cut + '…'


def _short_unit(u):
    """'Claude/claude_sessions/web' → 'claude_sessions/web' — the repo prefix is
    the same on every edge, so it is pure repetition inside a token budget."""
    return str(u or '').split('/', 1)[-1] or str(u or '')


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
        out.append(f"- **{repo}**" + (f" — {_clip(summ, 150)}" if summ else ''))
        names = sorted(mods, key=lambda m: len(mods[m]), reverse=True)
        shown = names[:6]
        line = "  modules: " + ', '.join(shown)
        if len(names) > 6:
            line += f" (+{len(names) - 6})"
        out.append(line)
    lessons = [e for e in ents if e.get('type') == 'lesson'
               and e.get('status') in ('approved', 'pinned')]
    # The budget is 250 tokens and this block was spending 117 of it on a module
    # LISTING — names with no content, which tells a session nothing it could
    # not get from `ls`. The graph's value is the facts it holds, so the rest of
    # the budget goes to the highest-signal ones: pinned first, then confident,
    # then most-reinforced.
    if lessons:
        out.append(f"- lessons: {len(lessons)} learned"
                   + (f", {sum(1 for e in lessons if e.get('status') == 'pinned')} pinned"
                      if any(e.get('status') == 'pinned' for e in lessons) else ''))
        for e in sorted(lessons, key=lambda e: (e.get('status') != 'pinned',
                                                -(e.get('confidence') or 0),
                                                -(e.get('hits') or 0)))[:3]:
            s = _clip(e.get('summary'), 95)
            out.append(f"  · {e.get('name', '')}" + (f" — {s}" if s else ''))
    # How the modules actually depend on each other. `module_edges` is derived
    # from real imports by the architecture graph and was going into the graph
    # file and no further — yet "which module leans on which" is exactly the
    # orientation a session lacks on its first turn, and it is one line.
    edges = sorted((e for e in (mem.get('module_edges') or []) if e.get('weight')),
                   key=lambda e: -(e.get('weight') or 0))[:3]
    if edges:
        out.append("- depends: " + ', '.join(
            f"{_short_unit(e['source'])} → {_short_unit(e['target'])}" for e in edges))
    top = sorted((e for e in ents
                  if e.get('type') != 'lesson' and e.get('valid', True) and e.get('hits')),
                 key=lambda e: -(e.get('hits') or 0))[:5]
    if top:
        out.append("- most used: " + ', '.join(
            f"{e.get('name', '')} ({e.get('module', '')})" for e in top))
    out.append('Detail on demand: run `claudectl recall "<topic>"` (Bash) for the '
               'task-relevant subgraph of this project\'s memory.')
    text = '\n'.join(out)
    # trim from the end (reinforced facts, then links, then lesson detail, then
    # repo lines), never the pointer
    while tokens_estimate(text) > max_tokens and len(out) > 2:
        out.pop(-2)
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
