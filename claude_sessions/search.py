"""Cross-project session search (names + AI titles + previews)."""

import os

from .config import C_RESET, C_DIM, C_SRCH, C_SEL
from .sessions import load_name, format_age
from .stats import iter_all_sessions, save_disk_cache
from . import render


def build_search_index(entries, silent=True):
    """Pure index build shared by the TUI search and the GUI: one dict per
    session with a lowercase `haystack` to filter on."""
    index = []
    partial = False
    for item in iter_all_sessions(entries, 'INDEXING SESSIONS', silent=silent):
        if item is None:
            partial = True
            break
        mtime, ppath, enc, sid, stats, cfgdir = item
        folder = os.path.join(cfgdir, 'projects', enc)
        name = load_name(folder, sid) or stats.get('title', '')
        display = name or stats.get('preview', '') or sid[:8]
        haystack = ' '.join([
            name, stats.get('title', ''), stats.get('preview', ''),
            os.path.basename(ppath) or ppath,
        ]).lower()
        index.append({'mtime': mtime, 'path': ppath, 'enc': enc, 'sid': sid,
                      'display': display, 'haystack': haystack,
                      'project': os.path.basename(ppath) or ppath,
                      'age': format_age(mtime).strip(), 'cfgdir': cfgdir})
    save_disk_cache()
    index.sort(reverse=True, key=lambda r: r['mtime'])
    return index, partial


def global_search(entries):
    """Search every session across all projects (every known account).
    entries: [(mtime, actual_path, encoded_name, cfgdir)] from main.run.
    Returns None (cancel) or ('resume', project_path, encoded_name, sid, cfgdir)."""
    from . import ui

    # ── index phase (incremental, ESC = partial) ──────────────
    rows, partial = build_search_index(entries, silent=False)
    # (mtime, ppath, enc, sid, display_name, haystack, cfgdir) tuples for the
    # positional accesses below
    index = [(r['mtime'], r['path'], r['enc'], r['sid'], r['display'],
              r['haystack'], r['cfgdir']) for r in rows]

    # ── interactive phase ─────────────────────────────────────
    query = ''
    nav = 0
    while True:
        q = query.lower().strip()
        matches = [r for r in index if all(w in r[5] for w in q.split())] if q else index
        max_rows = max(5, render.frame_height() - 9)
        shown = matches[:max_rows]
        nav = min(nav, max(0, len(shown) - 1))

        title = 'SEARCH ALL SESSIONS' + (' (partial index)' if partial else '')
        frame = [render.header('CLAUDECTL', title), '',
                 f"  {C_SRCH}[ {query}▌ ]{C_RESET}  {C_DIM}{len(matches)} match(es){C_RESET}", '']
        for i, (mtime, ppath, enc, sid, display, _, _cfgdir) in enumerate(shown):
            label = render.cols(
                [os.path.basename(ppath) or ppath,
                 f"{C_DIM}{format_age(mtime).strip()}{C_RESET}",
                 display],
                [18, 7, None])
            frame.append(render.row(label, selected=(i == nav)))
        if len(matches) > len(shown):
            frame.append(f"  {C_DIM}… {len(matches) - len(shown)} more — refine the search{C_RESET}")
        frame += ['', render.hint_keys([('type', 'to search (space = AND)'),
                                         ('↑↓', 'navigate'), ('ENTER', 'resume'),
                                         ('ESC', 'back')])]
        render.render_frame(frame)

        ev = ui.wait_event()
        if ev[0] == 'esc':
            if query:
                query = ''
                nav = 0
            else:
                return None
        elif ev[0] == 'enter':
            if shown:
                mtime, ppath, enc, sid, _, _, cfgdir = shown[nav]
                return ('resume', ppath, enc, sid, cfgdir)
        elif ev[0] == 'up':
            if shown:
                nav = (nav - 1) % len(shown)
        elif ev[0] == 'down':
            if shown:
                nav = (nav + 1) % len(shown)
        elif ev[0] == 'back':
            query = query[:-1]
            nav = 0
        elif ev[0] == 'char':
            query += ev[1]
            nav = 0
