"""Cross-project convention memory — the one memory layer that spans ALL
projects. Preference/correction lessons that recur across repos (or are pinned)
are promoted into a small block in the user-level ~/.claude/CLAUDE.md, so a
convention learned in one project ("this machine uses PowerShell 5.1", "prefer
pytest") is remembered everywhere. Token-frugal (≤ ~200 tok), fully automatic.
"""

import os
import json

from . import config as _c
from .memory import _tokens, tokens_estimate

MAX_CONVENTIONS = 12
MAX_TOKENS = 220
#: A `decision` travels: "we use pytest, not unittest" is as much a convention
#: as a stated preference. `error_fix` deliberately does not — it is about one
#: codebase's bug, and promoting it would put project noise in the file every
#: session on this account reads.
_KINDS = ('preference', 'correction', 'decision')
#: how many DISTINCT projects must show a rule before it is promoted. Reviewed
#: lessons need two; an unreviewed one needs three, because recurrence at that
#: scale is its own evidence and waiting for a manual approval that never comes
#: is why this feature sat empty.
MIN_PROJECTS = 2
MIN_PROJECTS_UNREVIEWED = 3


def _iter_project_graphs():
    """Yield (project_key, graph, project_path, proj_folder) for every project
    of EVERY account.

    Three things were wrong with reading `_c.projects_dir` directly: it is an
    import-time constant, so only the account active at import was scanned; it
    only looked at the mirrored copy under the encoded folder, so a project
    whose graph was written to its working directory was invisible; and the
    same project reached through two accounts counted twice toward the
    threshold. Deduped by encoded name, so it counts once.

    The write targets come back with the graph because promoting a rule means
    pinning it where it lives — re-deriving those paths in a second walk is how
    the two halves drift apart.
    """
    from . import store
    seen = set()
    paths = {}
    try:
        from . import gui
        for row in gui.list_projects():
            if row.get('encoded'):
                paths[row['encoded']] = row.get('path') or ''
    except Exception:
        pass
    for _acct, cfgdir in _c.all_config_dirs():
        root = store.projects_root(cfgdir)
        if not os.path.isdir(root):
            continue
        for enc in os.listdir(root):
            folder = os.path.join(root, enc)
            d = _load_graph(os.path.join(folder, '.claudectl', 'memory',
                                         'graph.json'))
            if d and enc not in seen:
                seen.add(enc)
                # the working-dir path matters for WRITES: save_memory mirrors
                # to both, and pinning only the encoded copy leaves the working
                # one (which load_memory reads first) to win the next read
                yield enc, d, paths.get(enc) or _actual(enc, folder), folder
    # working-directory copies, for projects whose mirror was never written
    for enc, ppath in paths.items():
        if enc in seen or not ppath:
            continue
        d = _load_graph(os.path.join(ppath, '.claudectl', 'memory', 'graph.json'))
        if d:
            seen.add(enc)
            yield enc, d, ppath, ''


def _actual(enc, folder):
    """The real working directory for an encoded project folder, or ''."""
    try:
        from .paths import find_actual_path
        return find_actual_path(enc, folder=folder) or ''
    except Exception:
        return ''


def _load_graph(p):
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get('entities') else None
    except Exception:
        return None


def _candidates():
    """Every convention-shaped lesson across every project, clustered.

    Returns [{'text','projects','pinned','reviewed','confidence','promoted'}].
    Kept separate from the promotion decision so the UI can show what ALMOST
    qualified: an empty card that cannot say why it is empty is a card nobody
    ever fills."""
    seen = []   # [tokenset, summary, projects:set, pinned, reviewed, conf]
    for key, g, _ppath, _folder in _iter_project_graphs():
        for e in g.get('entities', []):
            if e.get('type') != 'lesson' or e.get('kind') not in _KINDS:
                continue
            status = e.get('status')
            if status not in ('approved', 'pinned', 'pending'):
                continue
            summ = (e.get('summary') or '').strip()
            if not summ:
                continue
            tk = _tokens(summ)
            hit = None
            for rec in seen:
                inter = len(tk & rec[0])
                union = len(tk | rec[0]) or 1
                if inter / union > 0.6:
                    hit = rec
                    break
            if hit:
                hit[2].add(key)
                hit[3] = hit[3] or status == 'pinned'
                hit[4] = hit[4] or status in ('approved', 'pinned')
                hit[5] = max(hit[5], e.get('confidence', 0))
            else:
                seen.append([tk, summ, {key}, status == 'pinned',
                             status in ('approved', 'pinned'),
                             e.get('confidence', 0)])
    out = []
    for _tk, summ, projs, pinned, reviewed, conf in seen:
        n = len(projs)
        promoted = bool(pinned
                        or (reviewed and n >= MIN_PROJECTS)
                        or n >= MIN_PROJECTS_UNREVIEWED)
        out.append({'text': summ, 'projects': n, 'pinned': pinned,
                    'reviewed': reviewed, 'confidence': conf,
                    'score': n * 10 + (5 if pinned else 0) + conf,
                    'promoted': promoted})
    out.sort(key=lambda r: -r['score'])
    return out


def collect_conventions():
    """The rules that qualify for the global CLAUDE.md.

    Returns [{'text','score','projects','pinned'}] — objects, not tuples: the
    old (summary, score) pair JSON-serialized as an array and the GUI rendered
    it through JSON.stringify, so the one time this feature produced output it
    displayed as `["...",30]`."""
    return [{'text': r['text'], 'score': r['score'],
             'projects': r['projects'], 'pinned': r['pinned']}
            for r in _candidates() if r['promoted']][:MAX_CONVENTIONS]


def near_misses(limit=8):
    """What did NOT qualify, and the one fact that would change that."""
    out = []
    for r in _candidates():
        if r['promoted']:
            continue
        need = (MIN_PROJECTS if r['reviewed'] else MIN_PROJECTS_UNREVIEWED) - r['projects']
        out.append({
            'text': r['text'], 'projects': r['projects'],
            'why': (f"seen in {r['projects']} project"
                    f"{'' if r['projects'] == 1 else 's'} — "
                    + (f"needs {need} more, or pin it to promote it now"
                       if need > 0 else "pin it to promote it now")),
        })
    return out[:limit]


def pin_convention(text):
    """Pin every lesson that clusters with `text`, wherever it lives.

    Pinning is the manual override the promotion rules already respect, so this
    is the one action that turns a near-miss into a convention immediately. It
    pins in EVERY project the rule appears in, because the clustering is what
    made it a candidate and pinning one copy would leave the others to decay.
    Returns the number of lessons pinned."""
    want = _tokens(text or '')
    if not want:
        return 0
    n = 0
    for _key, g, ppath, folder in _iter_project_graphs():
        touched = False
        for e in g.get('entities', []):
            if e.get('type') != 'lesson' or e.get('kind') not in _KINDS:
                continue
            tk = _tokens(e.get('summary') or '')
            union = len(tk | want) or 1
            if len(tk & want) / union > 0.6 and e.get('status') != 'pinned':
                e['status'] = 'pinned'
                touched = True
                n += 1
        if touched:
            from .memory import save_memory
            save_memory(ppath, folder, g)
    return n


def build_block():
    convs = collect_conventions()
    if not convs:
        return ''
    lines = ["## Conventions (claudectl — learned across your projects)"]
    for c in convs:
        line = f"- {c['text']}"
        if tokens_estimate('\n'.join(lines + [line])) > MAX_TOKENS:
            break
        lines.append(line)
    return '\n'.join(lines)


def sync_to_global(cfgdir=None):
    """Write/replace the CONVENTIONS block in ~/.claude/CLAUDE.md. Only that
    block is touched. Returns True if written. Gated by conventions_to_global."""
    from .config import load_settings, _CONV_START, _CONV_END, global_claude_md_for
    global_claude_md = global_claude_md_for(cfgdir)
    if not load_settings().get('conventions_to_global', True):
        return False
    block_body = build_block()
    old = ''
    if os.path.isfile(global_claude_md):
        try:
            old = open(global_claude_md, encoding='utf-8', errors='ignore').read()
        except Exception:
            old = ''
    if not block_body:
        # nothing to promote — strip any existing block, leave the rest
        if _CONV_START in old and _CONV_END in old:
            new = (old[:old.index(_CONV_START)]
                   + old[old.index(_CONV_END) + len(_CONV_END):]).rstrip('\n') + '\n'
        else:
            return False
    else:
        from .config import generated_note
        note = generated_note('conventions that recur across your projects',
                              "claudectl's Global CLAUDE.md page")
        section = f"{_CONV_START}\n{note}\n{block_body}\n{_CONV_END}\n"
        if _CONV_START in old and _CONV_END in old:
            new = (old[:old.index(_CONV_START)] + section
                   + old[old.index(_CONV_END) + len(_CONV_END):])
        elif old.strip():
            new = old.rstrip('\n') + '\n\n' + section
        else:
            new = section
    if new == old:
        return True
    os.makedirs(os.path.dirname(global_claude_md), exist_ok=True)
    if not _c.write_atomic(global_claude_md, new):
        _c.log.error('conventions: global write failed')
        return False
    return True
