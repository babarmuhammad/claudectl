"""Usage statistics: persistent stats cache, cost estimation, dashboard screens."""

import os
import json

from .config import (COST_PER_MTOK, CACHE_READ_MULT, CACHE_WRITE_MULT,
                     load_settings, projects_dir, config_dir,
                     C_RESET, C_DIM, C_BOLD, C_TITLE)
from .sessions import get_session_stats, scan_sessions, format_age, load_name
from . import sessions as _sessions
from . import render

cache_file = os.path.join(config_dir, 'claudectl-stats-cache.json')

_disk_cache  = None    # path -> {'key': [mtime_ns, size], 'stats': {...}}
_cache_dirty = False

# Sessions larger than this are still parsed, but flagged so the UI can warn.
BIG_FILE_BYTES = 50 * 1024 * 1024


# ── persistent stats cache ───────────────────────────────────

def _load_disk_cache():
    global _disk_cache
    if _disk_cache is not None:
        return _disk_cache
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        _disk_cache = data if isinstance(data, dict) else {}
    except Exception:
        _disk_cache = {}
    return _disk_cache


def save_disk_cache():
    global _cache_dirty
    if not _cache_dirty or _disk_cache is None:
        return
    pruned = {p: v for p, v in _disk_cache.items() if os.path.exists(p)}
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(pruned, f)
        _cache_dirty = False
    except Exception:
        pass


def get_session_stats_cached(jsonl_path):
    """Stats dict via in-memory cache → disk cache → full parse."""
    global _cache_dirty
    try:
        st = os.stat(jsonl_path)
    except OSError:
        return get_session_stats(jsonl_path)   # returns empty stats
    key = [st.st_mtime_ns, st.st_size]

    mem = _sessions._info_cache.get(jsonl_path)
    if mem and list(mem[0]) == key:
        return mem[1]

    disk = _load_disk_cache().get(jsonl_path)
    if disk and disk.get('key') == key and isinstance(disk.get('stats'), dict):
        stats = disk['stats']
        _sessions._info_cache[jsonl_path] = (tuple(key), stats)   # warm memory
        return stats

    stats = get_session_stats(jsonl_path)
    _load_disk_cache()[jsonl_path] = {'key': key, 'stats': stats}
    _cache_dirty = True
    return stats


# ── cost estimation ──────────────────────────────────────────

def _cost_table():
    table = dict(COST_PER_MTOK)
    user = load_settings().get('cost_table', {})
    if isinstance(user, dict):
        for k, v in user.items():
            if isinstance(v, dict) and 'in' in v and 'out' in v:
                table[k] = v
    return table


def _rates_for(model, table):
    for pattern, rates in table.items():
        if pattern in model:
            return rates, True
    return {'in': 5.0, 'out': 25.0}, False   # unknown model: opus-tier guess


def estimate_cost(usage_by_model):
    """Returns (usd: float, exact: bool). exact=False if any model rate was guessed."""
    table = _cost_table()
    total = 0.0
    exact = True
    for model, u in (usage_by_model or {}).items():
        rates, known = _rates_for(model, table)
        if not known:
            exact = False
        total += (u.get('in', 0) * rates['in']
                  + u.get('out', 0) * rates['out']
                  + u.get('cache_read', 0) * rates['in'] * CACHE_READ_MULT
                  + u.get('cache_create', 0) * rates['in'] * CACHE_WRITE_MULT) / 1e6
    return total, exact


def _sum_usage(stats):
    t = {'in': 0, 'out': 0, 'cache_read': 0, 'cache_create': 0}
    for u in (stats.get('usage_by_model') or {}).values():
        for k in t:
            t[k] += u.get(k, 0)
    return t


def fmt_tok(n):
    if n >= 1_000_000:
        return f"{n/1e6:.1f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}k"
    return str(n)


# ── incremental scan with progress ───────────────────────────

def iter_all_sessions(entries, title='SCANNING SESSIONS'):
    """Yield (mtime, project_path, encoded, sid, stats) for every session of
    every project. Shows a progress frame; ESC stops early (yields partial).
    entries: [(mtime, actual_path, encoded_name)] as built by main.run."""
    from . import ui   # lazy — avoid import cycle

    total = len(entries)
    stopped = False
    peeking = True   # stop inspecting input after first non-ESC key, so
                     # keys queued for the next screen keep their order
    try:
        for pi, (_, ppath, enc) in enumerate(entries, 1):
            if stopped:
                break
            folder = os.path.join(projects_dir, enc)
            names = [f for f in (os.listdir(folder) if os.path.isdir(folder) else [])
                     if f.endswith('.jsonl')]
            for f in names:
                # peek for ESC; first non-ESC key is preserved for the next
                # screen and ends the peeking (keeps queued input in order)
                if peeking:
                    ev = ui.poll_event()
                    if ev:
                        if ev[0] == 'esc':
                            stopped = True
                            break
                        ui.push_event(ev)
                        peeking = False
                fpath = os.path.join(folder, f)
                try:
                    mtime = os.path.getmtime(fpath)
                except OSError:
                    continue
                stats = get_session_stats_cached(fpath)
                yield (mtime, ppath, enc, f[:-6], stats)
            render.render_frame([
                render.header('CLAUDECTL', title),
                '',
                f"  Scanning project {pi}/{total} — {render.trunc(os.path.basename(ppath) or ppath, 40)}",
                '',
                render.hint_keys([('ESC', 'stop early (partial results)')]),
            ])
        if stopped:
            yield None   # sentinel: partial
    finally:
        # guarantees parsed stats hit the disk cache even if the consumer
        # abandons the generator mid-scan
        save_disk_cache()


# ── dashboard screens ────────────────────────────────────────

def usage_dashboard(entries):
    """Global usage stats: per-project table, ENTER drills into sessions."""
    from . import ui

    rows = []      # (cost, label_parts dict)
    partial = False
    per_project = {}
    for item in iter_all_sessions(entries, 'USAGE STATS'):
        if item is None:
            partial = True
            break
        mtime, ppath, enc, sid, stats = item
        p = per_project.setdefault(enc, {
            'path': ppath, 'sessions': 0, 'msgs': 0,
            'usage': {'in': 0, 'out': 0, 'cache_read': 0, 'cache_create': 0},
            'usage_by_model': {},
        })
        p['sessions'] += 1
        p['msgs'] += stats.get('count', 0)
        u = _sum_usage(stats)
        for k in p['usage']:
            p['usage'][k] += u[k]
        for m, mu in (stats.get('usage_by_model') or {}).items():
            agg = p['usage_by_model'].setdefault(
                m, {'in': 0, 'out': 0, 'cache_read': 0, 'cache_create': 0})
            for k in agg:
                agg[k] += mu.get(k, 0)

    proj_rows = []
    for enc, p in per_project.items():
        cost, exact = estimate_cost(p['usage_by_model'])
        proj_rows.append((cost, enc, p, exact))
    proj_rows.sort(reverse=True, key=lambda r: r[0])

    nav = 0
    while True:
        head = render.cols(
            [f"{C_BOLD}project{C_RESET}", f"{C_BOLD}sess{C_RESET}", f"{C_BOLD}msgs{C_RESET}",
             f"{C_BOLD}in{C_RESET}", f"{C_BOLD}out{C_RESET}", f"{C_BOLD}cache{C_RESET}",
             f"{C_BOLD}est.${C_RESET}"],
            [None, 6, 7, 8, 8, 9, 9],
            aligns=['left', 'right', 'right', 'right', 'right', 'right', 'right'])
        total_cost = sum(r[0] for r in proj_rows)
        frame = [render.header('CLAUDECTL', 'USAGE STATS' + (' (partial)' if partial else '')),
                 '', '  ' + head, render.hline()]
        for i, (cost, enc, p, exact) in enumerate(proj_rows):
            u = p['usage']
            label = render.cols(
                [os.path.basename(p['path']) or p['path'], str(p['sessions']),
                 str(p['msgs']), fmt_tok(u['in']), fmt_tok(u['out']),
                 fmt_tok(u['cache_read']),
                 f"{'~' if not exact else ''}{cost:.2f}"],
                [None, 6, 7, 8, 8, 9, 9],
                aligns=['left', 'right', 'right', 'right', 'right', 'right', 'right'])
            frame.append(render.row(label, selected=(i == nav)))
        frame += [render.hline(),
                  f"  {C_DIM}total est. cost:{C_RESET} {C_BOLD}${total_cost:.2f}{C_RESET}   {C_DIM}(API-rate estimate; cache-aware){C_RESET}",
                  '',
                  render.hint_keys([('↑↓', 'navigate'), ('ENTER', 'project detail'),
                                    ('ESC', 'back')])]
        render.render_frame(frame)

        ev = ui.wait_event()
        if ev[0] == 'up' and proj_rows:
            nav = (nav - 1) % len(proj_rows)
        elif ev[0] == 'down' and proj_rows:
            nav = (nav + 1) % len(proj_rows)
        elif ev[0] == 'enter' and proj_rows:
            _, enc, p, _ = proj_rows[nav]
            project_usage_screen(os.path.join(projects_dir, enc),
                                 os.path.basename(p['path']) or p['path'])
        elif ev[0] == 'esc':
            return


def project_usage_screen(proj_folder, project_name):
    """Per-session usage rows for one project."""
    from . import ui

    sess_rows = []
    for (mtime, sid, preview, count) in scan_sessions(proj_folder):
        stats = get_session_stats_cached(os.path.join(proj_folder, f"{sid}.jsonl"))
        cost, exact = estimate_cost(stats.get('usage_by_model'))
        u = _sum_usage(stats)
        name = load_name(proj_folder, sid) or stats.get('title') or preview or sid[:8]
        sess_rows.append((mtime, name, count, u, cost, exact))
    save_disk_cache()

    nav = 0
    while True:
        head = render.cols(
            [f"{C_BOLD}age{C_RESET}", f"{C_BOLD}session{C_RESET}", f"{C_BOLD}msgs{C_RESET}",
             f"{C_BOLD}in{C_RESET}", f"{C_BOLD}out{C_RESET}", f"{C_BOLD}est.${C_RESET}"],
            [7, None, 6, 8, 8, 8],
            aligns=['left', 'left', 'right', 'right', 'right', 'right'])
        frame = [render.header('CLAUDECTL', project_name, 'USAGE'),
                 '', '  ' + head, render.hline()]
        for i, (mtime, name, count, u, cost, exact) in enumerate(sess_rows):
            label = render.cols(
                [format_age(mtime).strip(), name, str(count),
                 fmt_tok(u['in']), fmt_tok(u['out']),
                 f"{'~' if not exact else ''}{cost:.2f}"],
                [7, None, 6, 8, 8, 8],
                aligns=['left', 'left', 'right', 'right', 'right', 'right'])
            frame.append(render.row(label, selected=(i == nav)))
        frame += ['', render.hint_keys([('↑↓', 'navigate'), ('ESC', 'back')])]
        render.render_frame(frame)

        ev = ui.wait_event()
        if ev[0] == 'up' and sess_rows:
            nav = (nav - 1) % len(sess_rows)
        elif ev[0] == 'down' and sess_rows:
            nav = (nav + 1) % len(sess_rows)
        elif ev[0] == 'esc':
            return
