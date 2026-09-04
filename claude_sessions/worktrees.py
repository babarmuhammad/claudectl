"""The worktree board: which agent is working where, and on what.

Every tool in the parallel-agent category — Conductor, Crystal/Nimbalyst, Claude
Squad, ccpm, vibe-kanban — is built on the same idea: run several agents at once
in isolated git worktrees, then review and merge the diffs in one place.
claudectl could already *launch* into a worktree (`-w`) and had no idea what
happened next.

WHAT THIS IS NOT
----------------
Not a clone of Conductor. About 70% of the parts were already here — worktree
launch, background jobs with approval gates, `diffview`, multi-account, and the
semantic memory — and what was missing is only the view that joins them up.

The join is the interesting bit and it is one nobody else can make: claudectl
knows which SESSION is in which worktree, because it knows both. A session's
transcript records its `cwd`; a worktree is a path. Match them and the board can
say "the refactor is running in ../wt-refactor, it has touched 9 files, and it
last spoke 40 seconds ago" — which is the question you actually have when three
agents are running.

And the differentiator none of the others have: every one of those sessions
reads the same semantic memory, so they are not each rediscovering the codebase.

COST
----
`git worktree list --porcelain` per project and a stat of each session's
transcript. No transcript parsing in the listing path — that happens only when
you open one.
"""

import os
import time

from . import config as _c
from .repos import _git      # module-level name so tests can still monkeypatch it

#: a session whose transcript moved inside this window is "live" in its worktree
LIVE_WINDOW = 600

#: git state for many repos at once — the wall clock is one repo, not their sum
BOARD_WORKERS = 8


def list_worktrees(project_path):
    """[{path, branch, head, bare, detached, main}] for a repo, or []."""
    out = _git(['worktree', 'list', '--porcelain'], project_path)
    if not out:
        return []
    trees, cur = [], {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                trees.append(cur)
                cur = {}
            continue
        key, _, val = line.partition(' ')
        if key == 'worktree':
            cur = {'path': os.path.normpath(val), 'branch': '', 'head': '',
                   'detached': False, 'bare': False}
        elif key == 'HEAD':
            cur['head'] = val[:8]
        elif key == 'branch':
            cur['branch'] = val.rsplit('/', 1)[-1]
        elif key == 'detached':
            cur['detached'] = True
        elif key == 'bare':
            cur['bare'] = True
    if cur:
        trees.append(cur)
    # the first entry is always the main working tree
    for i, t in enumerate(trees):
        t['main'] = (i == 0)
    return trees


def dirty(path):
    """(changed_files, insertions_estimate) for uncommitted work, or (0, 0)."""
    out = _git(['status', '--porcelain'], path)
    if out is None:
        return 0, 0
    files = [l for l in out.splitlines() if l.strip()]
    return len(files), 0


def ahead_behind(path, base='HEAD@{upstream}'):
    """(ahead, behind) against the upstream, or (0, 0) when there is none."""
    out = _git(['rev-list', '--left-right', '--count', f'{base}...HEAD'], path)
    if not out:
        return 0, 0
    try:
        behind, ahead = out.split()[:2]
        return int(ahead), int(behind)
    except Exception:
        return 0, 0


def _sessions_by_cwd(project_path, proj_folder):
    """{normalised cwd -> newest session touching it}.

    The join that makes this a BOARD rather than a list of directories. A
    transcript records the cwd it ran in, so a worktree path resolves to the
    session working in it — which is the thing you want to know and the thing
    only a tool that owns both halves can answer.
    """
    from .sessions import account_folders_for, scan_sessions
    from .stats import get_session_stats_cached
    out = {}
    try:
        enc = os.path.basename(proj_folder or '')
        folders = account_folders_for(enc) if enc else []
    except Exception:
        folders = []
    for acct, folder in folders:
        try:
            rows = scan_sessions(folder)
        except Exception:
            continue
        for mtime, sid, _preview, count in rows:
            jsonl = os.path.join(folder, f'{sid}.jsonl')
            try:
                st = get_session_stats_cached(jsonl)
            except Exception:
                continue
            cwd = (st.get('cwd') or '').strip()
            if not cwd:
                continue
            key = os.path.normcase(os.path.normpath(cwd))
            prev = out.get(key)
            if prev and prev['mtime'] >= mtime:
                continue
            out[key] = {'sid': sid, 'mtime': mtime, 'account': acct,
                        'msgs': count, 'branch': st.get('branch', ''),
                        'title': st.get('title', ''), 'cfgdir':
                            os.path.dirname(os.path.dirname(folder))}
    return out


def board(project_path, proj_folder=None, by_cwd=None):
    """Everything the board renders, in one call.

    [{path, name, branch, head, main, dirty, ahead, behind, session}] where
    `session` is the newest session running in that worktree, or None.

    `by_cwd` lets a multi-repo caller scan sessions ONCE and share the result;
    left alone it scans for itself, which is what the single-repo path does.
    """
    trees = list_worktrees(project_path)
    if not trees:
        return {'worktrees': [], 'repo': False}
    if by_cwd is None:
        by_cwd = _sessions_by_cwd(project_path, proj_folder)
    now = time.time()
    rows = []
    for t in trees:
        if t.get('bare'):
            continue
        key = os.path.normcase(os.path.normpath(t['path']))
        s = by_cwd.get(key)
        n_dirty, _ = dirty(t['path'])
        ahead, behind = ahead_behind(t['path'])
        if s:
            age = now - s['mtime']
            s = dict(s, age=int(age), live=age < LIVE_WINDOW)
        rows.append({
            'path': t['path'],
            'name': os.path.basename(t['path']) or t['path'],
            'branch': t['branch'] or ('detached @ ' + t['head']),
            'head': t['head'], 'main': t['main'],
            'dirty': n_dirty, 'ahead': ahead, 'behind': behind,
            'session': s,
        })
    # live first, then dirtiest, then the main tree last — the board is for
    # finding the one that needs you, not for browsing directories
    rows.sort(key=lambda r: (r['main'],
                             not (r['session'] or {}).get('live'),
                             -r['dirty']))
    return {'worktrees': rows, 'repo': True}


def project_board(root, proj_folder=None):
    """The board for a project that may hold MANY repos.

    A project is often a parent directory — `D:/repos` holds 15 repos, one of
    which carries 7 submodules — and `git worktree list` in such a directory
    simply fails, which is why the tab used to say "not a git repository".

    Three things make this cheap enough to be one click:
      · the session scan runs ONCE and is shared, not per repo (it walks every
        transcript, so per-repo would dominate everything else here)
      · the repos are polled in parallel; the wall clock is the slowest single
        repo rather than their sum
      · every repo's state is written back to the shared cache, so the next
        statusline turn is free

    A submodule never gets `git worktree list`: run inside one, it reports the
    GITDIR (`…/.git/modules/<name>`) rather than the working directory, which
    would silently break the session-to-path join.
    """
    from concurrent.futures import ThreadPoolExecutor
    from . import repos as _repos
    try:
        from .connections import _discover_repos
        found = _discover_repos(os.path.abspath(root), proj_folder)
    except Exception:
        found = _repos.find_git_repos(root)
    if not found:
        return {'repo': False, 'repos': [], 'root': root}

    shared = _sessions_by_cwd(root, proj_folder)
    kinds = {p: _repos.classify(p) for p in found}

    def one(path):
        if kinds.get(path) == 'submodule':
            st = _repos.state(path)
            return {'worktrees': [{
                'path': path, 'name': os.path.basename(path) or path,
                'branch': st['branch'] or ('detached @ ' + st['head']),
                'head': st['head'], 'main': True, 'dirty': st['dirty'],
                'ahead': st['ahead'], 'behind': st['behind'],
                'session': shared.get(os.path.normcase(os.path.normpath(path))),
            }], 'repo': True}
        return board(path, proj_folder, by_cwd=shared)

    with ThreadPoolExecutor(max_workers=BOARD_WORKERS) as pool:
        boards = list(pool.map(one, found))

    by_path = {}
    for path, b in zip(found, boards):
        trees = b.get('worktrees') or []
        if not trees:
            continue
        main = next((t for t in trees if t.get('main')), trees[0])
        by_path[path] = {
            'path': path, 'name': os.path.basename(path) or path,
            'kind': kinds.get(path) or 'repo',
            'branch': main['branch'], 'head': main['head'],
            'dirty': main['dirty'],
            'ahead': main['ahead'], 'behind': main['behind'],
            'worktrees': trees, 'children': [],
        }

    # nest by longest path prefix — the same shape connections._cluster_of uses
    tops, ordered = [], sorted(by_path, key=len)
    for path in ordered:
        parent = next((p for p in reversed(ordered)
                       if p != path and path.startswith(p + os.sep)), None)
        (by_path[parent]['children'] if parent else tops).append(by_path[path])

    for entry in by_path.values():
        entry['sublabel'] = ('submodules'
                             if os.path.isfile(os.path.join(entry['path'], '.gitmodules'))
                             else 'nested repos')

    def live(entry):
        return any((t.get('session') or {}).get('live') for t in entry['worktrees'])

    tops.sort(key=lambda e: (not live(e), -e['dirty'], e['name'].lower()))
    _repos.remember({e['path']: e for e in by_path.values()})
    return {'repo': True, 'root': root, 'repos': tops,
            'multi': len(by_path) > 1}


def diff(path, staged=False):
    """The uncommitted diff of one worktree, for review before merging."""
    args = ['diff', '--stat=200', '--patch']
    if staged:
        args.insert(1, '--cached')
    return _git(args, path) or ''


def merge_into_main(project_path, branch):
    """Merge a worktree's branch into the current branch of the main tree.

    Gated by the caller through `diffview.confirm` — the same approval path
    every other write in claudectl goes through — so nothing here decides on
    its own that a merge is a good idea. `--no-ff` keeps the parallel work
    legible as its own line in the history, which is the whole reason it was
    run in a separate worktree.
    """
    # a ref beginning `-` is an option, not a branch — and this one arrives from
    # a request body
    if not branch or branch.startswith('-'):
        return False, 'not a branch name'
    out = _git(['merge', '--no-ff', '--no-edit', '--', branch],
               project_path, timeout=60)
    if out is None:
        return False, f'merge of {branch} failed (conflicts, or not a repo)'
    return True, out.strip()[:400] or f'Merged {branch}'


def remove(path, force=False):
    """Drop a worktree. Refuses to discard uncommitted work unless forced."""
    n_dirty, _ = dirty(path)
    if n_dirty and not force:
        return False, f'{n_dirty} uncommitted change(s) — nothing removed'
    args = ['worktree', 'remove']
    if force:
        args.append('--force')
    out = _git(args + [path], os.path.dirname(path) or path, timeout=30)
    if out is None:
        return False, 'git refused to remove that worktree'
    return True, f'Removed {os.path.basename(path)}'
