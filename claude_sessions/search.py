"""Cross-project session search (names + AI titles + previews)."""

import os

from .config import projects_dir, C_RESET, C_DIM, C_SRCH, C_SEL
from .sessions import load_name, format_age
from .stats import iter_all_sessions, save_disk_cache
from . import render


def global_search(entries):
    """Search every session across all projects.
    entries: [(mtime, actual_path, encoded_name)] from main.run.
    Returns None (cancel) or ('resume', project_path, encoded_name, sid)."""
    from . import ui

    # ── index phase (incremental, ESC = partial) ──────────────
    index = []     # (mtime, ppath, enc, sid, display_name, haystack)
    partial = False
    for item in iter_all_sessions(entries, 'INDEXING SESSIONS'):
        if item is None:
            partial = True
            break
        mtime, ppath, enc, sid, stats = item
        folder = os.path.join(projects_dir, enc)
        name = load_name(folder, sid) or stats.get('title', '')
        display = name or stats.get('preview', '') or sid[:8]
        haystack = ' '.join([
            name, stats.get('title', ''), stats.get('preview', ''),
            os.path.basename(ppath) or ppath,
        ]).lower()
        index.append((mtime, ppath, enc, sid, display, haystack))
    save_disk_cache()
    index.sort(reverse=True, key=lambda r: r[0])

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
        for i, (mtime, ppath, enc, sid, display, _) in enumerate(shown):
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
                mtime, ppath, enc, sid, _, _ = shown[nav]
                return ('resume', ppath, enc, sid)
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
