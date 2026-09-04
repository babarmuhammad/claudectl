"""Usage statistics: persistent stats cache, cost estimation, dashboard screens."""

import os
import json

# waiver: the stats cache is per account by design (its numbers are that
# account's sessions), and the multi-account roll-up walks all_config_dirs.
from .config import (COST_PER_MTOK, CACHE_READ_MULT, CACHE_WRITE_MULT,
                     load_settings, projects_dir, config_dir,
                     C_RESET, C_DIM, C_BOLD, C_TITLE)
from .sessions import (get_session_stats, scan_sessions, format_age, load_name,
                       _is_anthropic_model, _used_omni)
from . import sessions as _sessions
from . import config as _c
from . import render
from . import store

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
    from . import jsonstore
    _disk_cache = jsonstore.load(cache_file, expect=dict)
    return _disk_cache


def save_disk_cache():
    global _cache_dirty
    if not _cache_dirty or _disk_cache is None:
        return
    # snapshot first: a GUI job thread writing to the cache while this iterated
    # raised "dictionary changed size during iteration" and lost the whole save
    snapshot = dict(_disk_cache)
    pruned = {p: v for p, v in snapshot.items() if os.path.exists(p)}
    _c.write_json_atomic(cache_file, pruned)
    _cache_dirty = False


def get_session_stats_cached(jsonl_path):
    """Stats dict via in-memory cache → disk cache → full parse.

    The ladder itself now lives in `sessions._parse_session`, which is the ONLY
    thing that parses a transcript — so every caller gets it, including
    `scan_sessions`, which runs before this function on the cold path and used to
    make the disk half unreachable. Kept as a name because ~20 call sites and
    several tests use it, and because it still says something true: this is the
    accessor you want.
    """
    return get_session_stats(jsonl_path)


# ── cost estimation ──────────────────────────────────────────

def _cost_table():
    table = dict(COST_PER_MTOK)
    user = load_settings().get('cost_table', {})
    if isinstance(user, dict):
        for k, v in user.items():
            if isinstance(v, dict) and 'in' in v and 'out' in v:
                table[k] = v
    return table


_FREE_RATES = {'in': 0.0, 'out': 0.0}
_GUESS_RATES = {'in': 5.0, 'out': 25.0}


def _rates_for(model, table):
    for pattern, rates in table.items():
        if pattern in model:
            return rates, True
    if not _is_anthropic_model(model):
        return _FREE_RATES, False        # OmniRoute free-tier model: costs nothing
    return _GUESS_RATES, False           # unknown Claude id: opus-tier guess


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

def iter_all_sessions(entries, title='SCANNING SESSIONS', silent=False):
    """Yield (mtime, project_path, encoded, sid, stats, cfgdir) for every
    session of every project. Shows a progress frame; ESC stops early
    (yields partial). entries: [(mtime, actual_path, encoded_name, cfgdir)]
    as built by main.run. silent=True (GUI server threads): no progress
    frame, no keyboard peeking — a plain data generator."""
    if not silent:
        from . import ui   # lazy — avoid import cycle

    total = len(entries)
    stopped = False
    peeking = not silent   # stop inspecting input after first non-ESC key, so
                           # keys queued for the next screen keep their order
    try:
        for pi, (_, ppath, enc, cfgdir) in enumerate(entries, 1):
            if stopped:
                break
            folder = store.project_folder(cfgdir, enc)
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
                yield (mtime, ppath, enc, f[:-6], stats, cfgdir)
            if not silent:
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


def assemble_project_usage(entries, silent=True):
    """Per-project usage rows (dicts), cost-sorted — the pure aggregation
    behind usage_dashboard, shared with the GUI."""
    per_project = {}
    for item in iter_all_sessions(entries, 'USAGE STATS', silent=silent):
        if item is None:
            break
        mtime, ppath, enc, sid, stats, cfgdir = item
        p = per_project.setdefault(enc, {
            'path': ppath, 'name': os.path.basename(ppath) or ppath,
            'sessions': 0, 'msgs': 0, 'cfgdir': cfgdir, 'enc': enc,
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
    rows = []
    for enc, p in per_project.items():
        cost, exact = estimate_cost(p['usage_by_model'])
        p['cost'] = round(cost, 2)
        p['exact'] = exact
        rows.append(p)
    rows.sort(key=lambda p: p['cost'], reverse=True)
    return rows


def _merge_ubm(dst, src):
    for m, mu in (src or {}).items():
        agg = dst.setdefault(m, {'in': 0, 'out': 0, 'cache_read': 0, 'cache_create': 0})
        for k in agg:
            agg[k] += mu.get(k, 0)


def _omni_tokens(usage_by_model):
    return sum(sum(mu.values()) for m, mu in (usage_by_model or {}).items()
               if not _is_anthropic_model(m))


def _omni_saved(usage_by_model):
    """What the free-tier (non-Anthropic) tokens would have cost at Opus rates."""
    total = 0.0
    for m, u in (usage_by_model or {}).items():
        if _is_anthropic_model(m):
            continue
        total += (u.get('in', 0) * _GUESS_RATES['in']
                  + u.get('out', 0) * _GUESS_RATES['out']
                  + u.get('cache_read', 0) * _GUESS_RATES['in'] * CACHE_READ_MULT
                  + u.get('cache_create', 0) * _GUESS_RATES['in'] * CACHE_WRITE_MULT) / 1e6
    return total


#: A session whose transcript was touched inside this window counts as live.
#: Ten minutes is deliberately generous — Claude Code writes the transcript per
#: turn, so a session where you are reading a long answer or typing a prompt has
#: a stale mtime while very much being in use. Too tight and the Activity card
#: flickers to zero mid-conversation, which is the failure it exists to avoid.
LIVE_WINDOW = 600


def assemble_breakdown(entries, days=14, silent=True, recent=6):
    """Everything the home dashboard shows, from ONE transcript scan:
    per-day tokens split by account (oldest→newest, exactly `days` entries),
    per-account totals, per-project totals with the OmniRoute flag, and the
    grand totals. Day/account/total rows are window-scoped; project rows span
    all history, since that list doubles as the recency browser."""
    from datetime import datetime, timedelta
    from .config import all_config_dirs

    now = datetime.now()
    win = [(now - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days - 1, -1, -1)]
    acct_of = {d: n for n, d in all_config_dirs()}
    day_b = {d: {'tokens': 0, 'sessions': 0, 'ubm': {}, 'accounts': {}} for d in win}
    acct_b, proj_b, recent_b = {}, {}, []

    # ── live activity, across every account ──
    # What the dashboard's Activity card should have been reading all along. It
    # was wired to claudectl's own background-job count, which is ~always zero,
    # so the card reported the tool's idleness on a dashboard simultaneously
    # showing hundreds of millions of tokens.
    #
    # Both of these are free: the loop below already has every session's mtime,
    # account and message count. No extra scan, no deeper parse.
    now_ts = now.timestamp()
    hour_b = [0] * 24                       # sessions touched, per hour, last 24h
    live_by_acct = {}                       # sessions touched within LIVE_WINDOW

    for item in iter_all_sessions(entries, 'DASHBOARD', silent=silent):
        if item is None:
            break
        mtime, ppath, enc, _sid, stats, cfgdir = item
        acct = acct_of.get(cfgdir) or os.path.basename(cfgdir)
        ubm = stats.get('usage_by_model') or {}
        tokens = sum(_sum_usage(stats).values())

        p = proj_b.setdefault(enc, {
            'path': ppath, 'name': os.path.basename(ppath) or ppath, 'enc': enc,
            'cfgdir': cfgdir, 'accounts': [], 'sessions': 0, 'msgs': 0,
            'tokens': 0, 'omni_tokens': 0, 'omni': False, 'mtime': mtime, 'ubm': {},
            'by_day': {}})
        p['sessions'] += 1
        p['msgs'] += stats.get('count', 0)
        p['tokens'] += tokens
        p['omni_tokens'] += _omni_tokens(ubm)
        p['omni'] = p['omni'] or _used_omni(stats)
        p['mtime'] = max(p['mtime'], mtime)
        if acct not in p['accounts']:
            p['accounts'].append(acct)
        _merge_ubm(p['ubm'], ubm)
        # real last-activity across every account. The launch-history store
        # (last-session.json) only knows sessions claudectl itself started.
        # claudectl's own headless one-shots (lesson distilling, memory graph
        # building, title extraction) land in the same transcript store and are
        # always a couple of turns — they are not sessions the user resumes
        if stats.get('count', 0) > 3:
            recent_b.append({'sid': _sid, 'path': ppath, 'encoded': enc, 'cfgdir': cfgdir,
                             'account': acct, 'mtime': mtime, 'msgs': stats.get('count', 0),
                             'title': stats.get('title') or _sid[:8],
                             'omni': _used_omni(stats)})
            # same `count > 3` gate as above, and for the same reason: claudectl's
            # own headless one-shots (lesson distilling, graph building, title
            # extraction) land in this transcript store and would otherwise read
            # as "you are working" every time memory refreshed itself
            age = now_ts - mtime
            if 0 <= age < 86400:
                hour_b[min(23, int(age // 3600))] += 1
            if 0 <= age < LIVE_WINDOW:
                live_by_acct[acct] = live_by_acct.get(acct, 0) + 1

        day = _day_of(stats, mtime)
        if day not in day_b:
            continue                       # outside the window
        b = day_b[day]
        b['tokens'] += tokens
        b['sessions'] += 1
        b['accounts'][acct] = b['accounts'].get(acct, 0) + tokens
        p['by_day'][day] = p['by_day'].get(day, 0) + tokens
        _merge_ubm(b['ubm'], ubm)
        a = acct_b.setdefault(acct, {'account': acct, 'tokens': 0, 'sessions': 0,
                                     'omni_tokens': 0, 'ubm': {}})
        a['tokens'] += tokens
        a['sessions'] += 1
        a['omni_tokens'] += _omni_tokens(ubm)
        _merge_ubm(a['ubm'], ubm)

    day_rows = []
    for d in win:
        b = day_b[d]
        day_rows.append({'date': d, 'tokens': b['tokens'], 'sessions': b['sessions'],
                         'cost': round(estimate_cost(b['ubm'])[0], 2),
                         'omni_tokens': _omni_tokens(b['ubm']),
                         'accounts': b['accounts']})
    acct_rows = sorted(
        ({'account': a['account'], 'tokens': a['tokens'], 'sessions': a['sessions'],
          'omni_tokens': a['omni_tokens'],
          'cost': round(estimate_cost(a['ubm'])[0], 2)} for a in acct_b.values()),
        key=lambda r: r['tokens'], reverse=True)
    proj_rows = []
    for p in proj_b.values():
        cost, exact = estimate_cost(p['ubm'])
        p.pop('ubm')
        p.update(cost=round(cost, 2), exact=exact,
                 age=format_age(p['mtime']).strip(),
                 sparkline=[p['by_day'].get(d, 0) for d in win])
        p.pop('by_day')
        proj_rows.append(p)
    proj_rows.sort(key=lambda r: r['mtime'], reverse=True)
    all_ubm = {}
    for a in acct_b.values():
        _merge_ubm(all_ubm, a['ubm'])
    recent_b.sort(key=lambda r: r['mtime'], reverse=True)
    return {'days': day_rows, 'accounts': acct_rows, 'projects': proj_rows,
            'recent': recent_b[:recent],
            # oldest→newest, so the sparkline reads left-to-right like every
            # other series in the app
            'hours': list(reversed(hour_b)),
            'live': {'total': sum(live_by_acct.values()),
                     'by_account': live_by_acct,
                     'window': LIVE_WINDOW},
            'totals': {'tokens': sum(r['tokens'] for r in day_rows),
                       'cost': round(estimate_cost(all_ubm)[0], 2),
                       'sessions': sum(r['sessions'] for r in acct_rows),
                       'omni_tokens': sum(r['omni_tokens'] for r in acct_rows),
                       'omni_saved': round(_omni_saved(all_ubm), 2)}}


def assemble_session_usage(proj_folder):
    """Per-session usage rows for one project across every account — the
    pure aggregation behind project_usage_screen, shared with the GUI."""
    from .sessions import project_session_folders
    from .config import all_config_dirs

    acct_by_dir = {os.path.normcase(os.path.abspath(store.projects_root(d))): n
                   for n, d in all_config_dirs()}

    def _acct_of(folder):
        parent = os.path.normcase(os.path.abspath(os.path.dirname(folder)))
        return acct_by_dir.get(parent, '')

    rows = []
    seen_sids = set()
    for folder in project_session_folders(proj_folder):
        acct = _acct_of(folder)
        for (mtime, sid, preview, count) in scan_sessions(folder):
            if sid in seen_sids:
                continue
            seen_sids.add(sid)
            stats = get_session_stats_cached(os.path.join(folder, f"{sid}.jsonl"))
            cost, exact = estimate_cost(stats.get('usage_by_model'))
            u = _sum_usage(stats)
            name = load_name(folder, sid) or stats.get('title') or preview or sid[:8]
            rows.append({'mtime': mtime, 'sid': sid, 'name': name,
                         'account': acct, 'age': format_age(mtime).strip(),
                         'msgs': count, 'usage': u,
                         'cost': round(cost, 2), 'exact': exact})
    rows.sort(key=lambda r: r['mtime'], reverse=True)
    save_disk_cache()
    return rows


# ── per-day aggregation ──────────────────────────────────────

def _day_of(stats, mtime):
    from datetime import datetime
    ts = stats.get('last_ts') or stats.get('first_ts') or mtime
    try:
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
    except Exception:
        return ''


def usage_by_day(entries, days=14, silent=False):
    """[(date_str, usage_dict, cost_usd, n_sessions)] newest-first, last `days`
    calendar days. A session is bucketed by its last activity day."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    buckets = {}
    for item in iter_all_sessions(entries, 'DAILY USAGE', silent=silent):
        if item is None:
            break
        mtime, _ppath, _enc, _sid, stats, _cfgdir = item
        day = _day_of(stats, mtime)
        if not day or day < cutoff:
            continue
        b = buckets.setdefault(day, {
            'usage': {'in': 0, 'out': 0, 'cache_read': 0, 'cache_create': 0},
            'usage_by_model': {}, 'sessions': 0})
        b['sessions'] += 1
        u = _sum_usage(stats)
        for k in b['usage']:
            b['usage'][k] += u[k]
        for m, mu in (stats.get('usage_by_model') or {}).items():
            agg = b['usage_by_model'].setdefault(
                m, {'in': 0, 'out': 0, 'cache_read': 0, 'cache_create': 0})
            for k in agg:
                agg[k] += mu.get(k, 0)
    out = []
    for day in sorted(buckets, reverse=True):
        b = buckets[day]
        cost, _exact = estimate_cost(b['usage_by_model'])
        out.append((day, b['usage'], cost, b['sessions']))
    return out


def today_tokens():
    """Today's total tokens from the disk cache ONLY (no parsing — cheap
    enough for a main-screen badge)."""
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    total = 0
    for _path, rec in _load_disk_cache().items():
        stats = rec.get('stats') or {}
        ts = stats.get('last_ts')
        try:
            if ts and datetime.fromtimestamp(ts).strftime('%Y-%m-%d') == today:
                u = _sum_usage(stats)
                total += u['in'] + u['out']
        except Exception:
            continue
    return total


def daily_usage_screen(entries):
    """Last-14-days table with bars; today highlighted; plan-window % joined."""
    from . import ui
    from . import usage as usage_mod
    from datetime import datetime
    rows = usage_by_day(entries)
    today = datetime.now().strftime('%Y-%m-%d')
    peak = max((r[1]['in'] + r[1]['out'] for r in rows), default=1) or 1

    plan = ''
    try:
        with usage_mod._lock:
            data = usage_mod._data
        wins = usage_mod._extract_windows(data) if data else []
        if wins:
            plan = '   '.join(f"{lbl} {pct:.0f}%" for lbl, pct, _r in wins[:3])
    except Exception:
        pass

    while True:
        head = render.cols(
            [f"{C_BOLD}day{C_RESET}", f"{C_BOLD}sess{C_RESET}", f"{C_BOLD}in{C_RESET}",
             f"{C_BOLD}out{C_RESET}", f"{C_BOLD}cache{C_RESET}", f"{C_BOLD}est.${C_RESET}", ''],
            [12, 5, 8, 8, 9, 8, None],
            aligns=['left', 'right', 'right', 'right', 'right', 'right', 'left'])
        frame = [render.header('CLAUDECTL', 'DAILY USAGE (last 14 days)'), '',
                 '  ' + head, render.hline()]
        for day, u, cost, n in rows:
            tot = u['in'] + u['out']
            bar_w = max(1, int(18 * tot / peak)) if tot else 0
            bar = '█' * bar_w
            mark = f"{C_BOLD}" if day == today else C_DIM
            label = render.cols(
                [day + (' ←' if day == today else ''), str(n), fmt_tok(u['in']),
                 fmt_tok(u['out']), fmt_tok(u['cache_read']), f"{cost:.2f}", bar],
                [12, 5, 8, 8, 9, 8, None],
                aligns=['left', 'right', 'right', 'right', 'right', 'right', 'left'])
            frame.append(f"  {mark}{label}{C_RESET}")
        if not rows:
            frame.append(f"  {C_DIM}(no sessions in the last 14 days){C_RESET}")
        frame.append(render.hline())
        if plan:
            frame.append(f"  {C_DIM}plan windows now:{C_RESET} {plan}")
        frame += ['', render.hint_keys([('ESC', 'back')])]
        render.render_frame(frame)
        if ui.wait_event()[0] == 'esc':
            return


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
        mtime, ppath, enc, sid, stats, cfgdir = item
        # key by encoded name (== real project path) so the SAME project under
        # several accounts collapses into ONE row instead of one per account
        key = enc
        p = per_project.setdefault(key, {
            'path': ppath, 'sessions': 0, 'msgs': 0, 'cfgdir': cfgdir, 'enc': enc,
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
    for key, p in per_project.items():
        cost, exact = estimate_cost(p['usage_by_model'])
        proj_rows.append((cost, key, p, exact))
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
        for i, (cost, key, p, exact) in enumerate(proj_rows):
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
                                    ('d', 'daily view'), ('ESC', 'back')])]
        render.render_frame(frame)

        ev = ui.wait_event()
        if ev[0] == 'up' and proj_rows:
            nav = (nav - 1) % len(proj_rows)
        elif ev[0] == 'down' and proj_rows:
            nav = (nav + 1) % len(proj_rows)
        elif ev[0] == 'enter' and proj_rows:
            _, enc, p, _ = proj_rows[nav]
            project_usage_screen(store.project_folder(p['cfgdir'], enc),
                                 os.path.basename(p['path']) or p['path'])
        elif ev[0] == 'char' and ev[1] == 'd':
            daily_usage_screen(entries)
        elif ev[0] == 'esc':
            return


def project_usage_screen(proj_folder, project_name):
    """Per-session usage rows for one project — across EVERY account that has
    sessions for it; foreign-account rows are tagged inline with the account."""
    from . import ui

    sess_rows = []
    for r in assemble_session_usage(proj_folder):
        name = r['name']
        if r['account'] and r['account'] != 'default':
            name = f"{name}  [{r['account']}]"
        sess_rows.append((r['mtime'], name, r['msgs'], r['usage'],
                          r['cost'], r['exact']))

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
