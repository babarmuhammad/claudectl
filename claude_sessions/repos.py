"""Which git repos live under a project, and what state each one is in.

WHY THIS IS NOT `claude_md.find_git_repos`
------------------------------------------
That function is where discovery lived, and it was wrong in three ways that only
show up on a project which is a PARENT of repos rather than a repo:

  · it tested `isdir('.git')`, so every submodule and every linked worktree was
    invisible — both store `.git` as a FILE holding one `gitdir:` line
  · it stopped descending as soon as a child turned out to be a repo, so a
    repo's own nested repos were unreachable
  · depth 2 was too shallow for a tree like D:/Lavoro/python/Scripts/<x>/<repo>

Measured on the machine this was written for: D:/repos returned 8 repos and
missed all 7 of IKM.Workspace's submodules; D:/Lavoro returned 1 of 5.

THE ONE LINE THAT CLASSIFIES
----------------------------
A `.git` FILE says where the real gitdir is, and that path is the whole answer:
`.git/modules/<name>` is a submodule, `.git/worktrees/<name>` is a linked
worktree. No subprocess. This matters because a linked worktree must NOT be
counted as a repo — `.claude/worktrees/` holds ten of them here — and because
`git worktree list` run inside a submodule reports the GITDIR, not the working
directory, which would silently break the board's session-to-path join.

COST
----
The walk is cheap and is not cached: depth 4 over D:/repos is 21ms for 1183
directories. `git status` is not cheap (60-270ms) and IS cached, keyed by the
mtime of `.git/index`. The branch is never cached — reading `.git/HEAD` is
cheaper than the cache lookup and cannot go stale.

The cache lives beside the other per-account caches rather than in the project's
own `.claudectl/`: the statusline resolves a bare cwd with no project folder,
and writing into `D:/repos/SV3/.claudectl/` would show up in that repo's own
`git status` — this tool must never dirty a tree it is reporting on.
"""

import json
import os
import time

from . import config as _c
from . import proc

#: a pathological root (a whole drive) must not hang a statusline
MAX_DIRS = 4000

#: `git status` is re-run at most this often per repo
STATE_TTL = 60

#: a discovered repo list is re-walked at most this often
LIST_TTL = 300


def _git(args, cwd, timeout=15):
    """Kept as the name eight modules import; the implementation is
    `proc.git`."""
    return proc.git(args, cwd, timeout)


def _gitdir(path):
    """The real git directory for a working tree, resolving a `.git` file."""
    g = os.path.join(path, '.git')
    if os.path.isdir(g):
        return g
    try:
        with open(g, encoding='utf-8', errors='ignore') as f:
            text = f.read(4096).strip()
    except OSError:
        return ''
    if not text.startswith('gitdir:'):
        return ''
    p = text[7:].strip()
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(path, p))


def classify(path):
    """'repo' | 'submodule' | 'worktree' | None — filesystem only, no git."""
    g = os.path.join(path, '.git')
    if os.path.isdir(g):
        return 'repo'
    if not os.path.isfile(g):
        return None
    gd = _gitdir(path)
    if not gd:
        return None
    p = gd.replace('\\', '/')
    if '.git/modules/' in p:
        return 'submodule'
    if '.git/worktrees/' in p:
        return 'worktree'
    return 'repo'


def find_git_repos(root, max_depth=4):
    """Every repo at or under `root`, submodules included, worktrees excluded.

    Unlike the function this replaces it keeps descending after a hit — that is
    what makes a repo's submodules reachable.
    """
    from .connections import SKIP_DIRS      # 22ms to import; the walk-up path never needs it
    found, budget = [], [MAX_DIRS]

    def walk(d, depth):
        if budget[0] <= 0:
            return
        budget[0] -= 1
        kind = classify(d)
        if kind in ('repo', 'submodule'):
            found.append(d)
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if (e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS
                    and not e.name.startswith('.')):
                walk(e.path, depth + 1)

    walk(os.path.abspath(root), 0)
    return found


#: a directory holding one of these is its own project, git or not
SUBPROJECT_MARKERS = {'package.json', 'pyproject.toml', 'go.mod', 'Cargo.toml',
                      'composer.json', 'Gemfile', 'pom.xml', 'build.gradle'}


def find_subprojects(root, max_depth=4):
    """Directories UNDER `root` holding their own manifest.

    A subproject dropped into a project is a section of that project's memory
    even when it has no `.git` of its own — without this it is folded into the
    parent's cluster and its directories are flattened into the parent's module
    keys, so it never gets a summary, a rule file or a digest bullet.

    `root` itself is never returned: a subproject is *under* the root, and
    returning the root would relabel every file of a non-git project from its
    top-level directory to the root's basename.
    """
    from .connections import SKIP_DIRS
    found, budget = [], [MAX_DIRS]

    def walk(d, depth):
        if budget[0] <= 0 or depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if not (e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS
                    and not e.name.startswith('.')):
                continue
            budget[0] -= 1
            if budget[0] <= 0:
                return
            if any(os.path.isfile(os.path.join(e.path, m)) for m in SUBPROJECT_MARKERS):
                found.append(e.path)
            walk(e.path, depth + 1)

    walk(os.path.abspath(root), 0)
    return found


def owner_of(path):
    """The repo a path sits in, walking up. '' when there is none.

    Filesystem only — this is the statusline's hot path and runs every turn.
    """
    d = os.path.abspath(path)
    while True:
        if classify(d):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return ''
        d = parent


def head_branch(repo):
    """Branch name from `.git/HEAD`, '' when detached. Never cached: reading
    the file costs less than the cache lookup and cannot be stale."""
    gd = _gitdir(repo)
    if not gd:
        return ''
    try:
        with open(os.path.join(gd, 'HEAD'), encoding='utf-8', errors='ignore') as f:
            text = f.read(512).strip()
    except OSError:
        return ''
    return text[16:] if text.startswith('ref: refs/heads/') else ''


# ── cached git state ─────────────────────────────────────────

def _cache_file():
    return os.path.join(_c.config_dir, 'claudectl-repostate.json')


def _load_cache():
    try:
        with open(_cache_file(), encoding='utf-8') as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def _save_cache(c):
    """Temp + os.replace. Claude Code CANCELS an in-flight statusline when a new
    update arrives, so a plain write would leave a torn file behind."""
    now = time.time()
    c['repos'] = {k: v for k, v in (c.get('repos') or {}).items()
                  if now - (v.get('at') or 0) < 604800}
    _c.write_json_atomic(_cache_file(), c, indent=None)


def _index_stamp(repo):
    try:
        return os.stat(os.path.join(_gitdir(repo), 'index')).st_mtime_ns
    except OSError:
        return 0


def _fields(repo):
    """branch + dirty + ahead/behind in ONE git call.

    `--porcelain=v2 --branch` is 60ms where rev-parse + rev-list + status is
    ~180ms — on Windows the cost is process creation, so the win is spawning
    once rather than three times.
    """
    out = _git(['status', '--porcelain=v2', '--branch'], repo, timeout=5)
    if out is None:
        return None
    d = {'dirty': 0, 'ahead': 0, 'behind': 0, 'head': ''}
    for line in out.splitlines():
        if not line.startswith('#'):
            d['dirty'] += 1
        elif line.startswith('# branch.oid '):
            d['head'] = line[13:].strip()[:8]
        elif line.startswith('# branch.ab '):
            try:
                a, b = line[12:].split()
                d['ahead'], d['behind'] = int(a), -int(b)
            except ValueError:
                pass
    return d


def state(repo, max_age=STATE_TTL, refresh=True):
    """{branch, dirty, ahead, behind, head, stale} for one repo.

    With refresh=False this NEVER spawns git — that is the contract the
    statusline runs on. `branch` is always exact; `ahead`/`behind` are
    invalidated by the index mtime, so only `dirty` can be stale (a plain file
    edit touches nothing under .git, so no filesystem signature can see it).
    """
    key = os.path.normcase(os.path.abspath(repo))
    cache = _load_cache()
    hit = (cache.get('repos') or {}).get(key)
    stamp = _index_stamp(repo)
    fresh = (isinstance(hit, dict) and hit.get('idx') == stamp
             and time.time() - (hit.get('at') or 0) < max_age)
    if not fresh and refresh:
        f = _fields(repo)
        if f is not None:
            hit = dict(f, at=time.time(), idx=stamp)
            cache.setdefault('repos', {})[key] = hit
            _save_cache(cache)
            fresh = True
    if not isinstance(hit, dict):
        hit = {'dirty': 0, 'ahead': 0, 'behind': 0, 'head': ''}
        fresh = False
    return {'branch': head_branch(repo), 'dirty': hit.get('dirty', 0),
            'ahead': hit.get('ahead', 0), 'behind': hit.get('behind', 0),
            'head': hit.get('head', ''), 'stale': not fresh}


def remember(measured):
    """Bulk-warm the cache from state the caller ALREADY measured.

    {path: {dirty, ahead, behind, head}}. It takes the numbers rather than the
    paths on purpose: the board has just run git for every repo, and re-running
    it here to populate the cache would double the cost of opening the tab.
    This is what makes the next statusline turn free.
    """
    cache = _load_cache()
    now = time.time()
    slot = cache.setdefault('repos', {})
    for repo, f in (measured or {}).items():
        slot[os.path.normcase(os.path.abspath(repo))] = {
            'dirty': f.get('dirty', 0), 'ahead': f.get('ahead', 0),
            'behind': f.get('behind', 0), 'head': f.get('head', ''),
            'at': now, 'idx': _index_stamp(repo)}
    _save_cache(cache)


def summary(root, max_age=LIST_TTL, refresh=False):
    """{repos, dirty, stale} for everything under `root`. The statusline's
    parent-directory roll-up. Defaults to never spawning git."""
    key = os.path.normcase(os.path.abspath(root))
    cache = _load_cache()
    hit = (cache.get('roots') or {}).get(key)
    if isinstance(hit, dict) and time.time() - (hit.get('at') or 0) < max_age:
        paths = hit.get('list') or []
    else:
        paths = find_git_repos(root)
        cache.setdefault('roots', {})[key] = {'at': time.time(), 'list': paths}
        _save_cache(cache)
    dirty = stale = 0
    for p in paths:
        st = state(p, refresh=refresh)
        dirty += 1 if st['dirty'] else 0
        stale += 1 if st['stale'] else 0
    return {'repos': len(paths), 'dirty': dirty, 'stale': stale}
