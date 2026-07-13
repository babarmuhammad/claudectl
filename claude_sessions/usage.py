"""Subscription usage limits (5-hour window / weekly) for the main screen.

Queries the same OAuth usage endpoint Claude Code's /usage command uses,
authenticated with the local Claude Code OAuth token. Fetched once per run
on a background thread; absent/expired credentials degrade to no line.
"""

import os
import json
import time
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone

from . import config as _c

USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'

_REFRESH_SEC = 300   # re-poll cadence on success (usage changes slowly; avoid rate limits)
_RETRY_BASE  = 30    # first backoff after a failed fetch; doubles each fail
_RETRY_MAX   = 600   # backoff ceiling
_MAX_FAILS   = 3     # give up (blank line) only after this many failures with no data yet

_lock       = threading.Lock()
_started    = False
_ready      = False
_data       = None   # active account's usage (back-compat: single-account + stats)
_acct_state = {}     # cfgdir -> {'name','email','data'} for every configured account
_retry_after = 0     # seconds requested by a 429 Retry-After header (0 = none)


def _read_token(cfgdir=None):
    try:
        p = os.path.join(cfgdir or _c.config_dir, '.credentials.json')
        with open(p, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return (d.get('claudeAiOauth') or {}).get('accessToken')
    except Exception:
        return None


def _account_email(cfgdir=None):
    """Best-effort account email/label from the account's stored credentials."""
    for fn in ('.credentials.json',):
        try:
            with open(os.path.join(cfgdir or _c.config_dir, fn), encoding='utf-8') as f:
                d = json.load(f)
            oauth = d.get('claudeAiOauth') or {}
            acc = oauth.get('account') or {}
            for k in ('email_address', 'emailAddress', 'email'):
                if oauth.get(k):
                    return oauth[k]
                if acc.get(k):
                    return acc[k]
        except Exception:
            continue
    return ''


def _targets():
    """[(name, abs cfgdir)] to poll — the default account plus configured ones,
    deduped, default first."""
    s = _c.load_settings()
    out = [('default', os.path.join(_c._USERPROFILE, '.claude'))]
    for a in s.get('accounts', []):
        d = a.get('dir') if isinstance(a, dict) else None
        if d:
            out.append((a.get('name') or d, os.path.expanduser(os.path.expandvars(d))))
    seen, uniq = set(), []
    for n, d in out:
        rd = os.path.normcase(os.path.abspath(d))
        if rd not in seen:
            seen.add(rd)
            uniq.append((n, d))
    return uniq


def fetch_usage(cfgdir=None):
    """GET the OAuth usage endpoint for an account. Returns parsed dict or None."""
    token = _read_token(cfgdir)
    if not token:
        return None
    req = urllib.request.Request(USAGE_URL, headers={
        'Authorization': f'Bearer {token}',
        'anthropic-beta': 'oauth-2025-04-20',
        'Content-Type': 'application/json',
        'User-Agent': 'claudectl',
    })
    global _retry_after
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            _retry_after = 0
            return json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        if e.code == 429:                      # honor Retry-After; don't hammer
            try:
                _retry_after = max(_retry_after, int(e.headers.get('Retry-After') or 0))
            except (TypeError, ValueError):
                _retry_after = 0
        return None
    except Exception:
        return None


def _background():
    """Poll every configured account forever: refresh live values, retry
    transient failures, never clobber good data with a None. Updates per-account
    state + the active account's `_data` (back-compat)."""
    global _data, _ready
    fails = 0
    while True:
        targets = _targets()
        active = os.path.normcase(os.path.abspath(_c.config_dir))
        any_ok = False
        for name, d in targets:
            try:
                data = fetch_usage(d)
            except Exception:
                _c.log.exception('usage fetch failed')
                data = None
            with _lock:
                st = _acct_state.setdefault(d, {})
                st['name'] = name
                if data is not None:
                    st['data'] = data
                    if not st.get('email'):
                        st['email'] = _account_email(d)
                    any_ok = True
                    if os.path.normcase(os.path.abspath(d)) == active:
                        _data = data
                _ready = True
            time.sleep(1)            # small gap between accounts (avoid a burst)
        with _lock:
            if not any_ok:
                fails += 1
        if any_ok:
            fails = 0
            sleep = _REFRESH_SEC
        else:                        # exponential backoff, never faster than a 429 Retry-After
            sleep = min(_RETRY_BASE * (2 ** max(0, fails - 1)), _RETRY_MAX)
            sleep = max(sleep, _retry_after)
        time.sleep(sleep)


def _ensure_started():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_background, daemon=True).start()


def _fmt_reset(iso):
    """ISO timestamp → short local time ('14:30' today, else 'Tue 09:00')."""
    try:
        dt = datetime.fromisoformat(str(iso).replace('Z', '+00:00')).astimezone()
    except Exception:
        return '?'
    now = datetime.now(dt.tzinfo)
    if dt.date() == now.date():
        return dt.strftime('%H:%M')
    return dt.strftime('%a %H:%M')


def _pct_color(pct):
    if pct >= 80:
        return _c.C_ERR
    if pct >= 50:
        return _c.C_WARN
    return _c.C_OK


def _window_label(key):
    k = key.lower()
    if 'five' in k or '5h' in k:
        return 'session'
    if 'seven' in k or 'week' in k:
        return 'weekly'
    return key[:8]


def _limit_label(item):
    """Label a `limits[]` entry. New usage shape: kind = session | weekly_all |
    weekly_scoped (per-model, with scope.model.display_name, e.g. Fable)."""
    k = str(item.get('kind', '')).lower()
    g = str(item.get('group', '')).lower()
    scope = item.get('scope') or {}
    model = (scope.get('model') or {}) if isinstance(scope, dict) else {}
    if k == 'weekly_scoped' or model.get('display_name'):
        return model.get('display_name') or 'wk-model'
    if k == 'session' or g == 'session' or 'five' in k or '5h' in k:
        return 'session'
    if k == 'weekly_all' or g == 'weekly' or 'week' in k:
        return 'weekly'
    return (k or g)[:8]


def _extract_windows(data):
    """Find limit windows in the response, tolerant of shape variations.
    Returns [(label, pct, resets_at_iso)] ordered daily-first.

    `utilization`/`percent` are already 0-100 percentages from this endpoint —
    they are NOT divided or rescaled here. (An earlier 0..1 heuristic wrongly
    multiplied small values by 100, pinning low usage to 100%.)"""
    if not isinstance(data, dict):
        return []

    def norm(v):
        try:
            return max(0.0, min(float(v), 100.0))
        except (TypeError, ValueError):
            return None

    out = []
    # Prefer the authoritative `limits` array (explicit percent + group).
    limits = data.get('limits')
    if isinstance(limits, list):
        for item in limits:
            if not isinstance(item, dict):
                continue
            pct = norm(item.get('percent'))
            if pct is None:
                continue
            out.append((_limit_label(item), pct, item.get('resets_at')))

    # Fallback: top-level per-window dicts carrying `utilization`.
    if not out:
        for key, val in data.items():
            if not isinstance(val, dict):
                continue
            pct = norm(val.get('utilization'))
            if pct is None:
                continue
            out.append((_window_label(key), pct, val.get('resets_at')))

    order = {'session': 0, 'weekly': 1}   # session, all-models weekly, then per-model
    out.sort(key=lambda w: order.get(w[0], 5))
    return out


def _account_grid(accts):
    """Aligned multi-account table. Columns = limit windows (session, weekly,
    then per-model) present with any usage; rows = accounts. Reset times are
    dropped here for alignment (see them in the daily-usage screen)."""
    from . import render
    rows = []                       # (label, {col: (pct, reset)})
    seen_cols, cols = set(), []
    for name, email, adata in accts:
        w = _extract_windows(adata)
        if not w:
            continue
        d = {}
        for lbl, pct, reset in w:
            d[lbl] = (pct, reset)
            if lbl not in seen_cols:
                seen_cols.add(lbl)
                cols.append(lbl)
        rows.append((email or name or '?', d))
    if not rows:
        return ''
    # drop columns that are 0/absent for every account (e.g. an unused model)
    cols = [c for c in cols if any(r[1].get(c, (0,))[0] for r in rows)]
    if not cols:
        cols = list(seen_cols)[:1]
    order = {'session': 0, 'weekly': 1}
    cols.sort(key=lambda c: (order.get(c, 5), c))

    name_w = min(22, max(len(r[0]) for r in rows))
    cell_w = 8                      # meter width
    hdr = '  ' + ' ' * name_w + '  ' + '  '.join(f"{_c.C_DIM}{c[:cell_w + 5]:<{cell_w + 5}}{_c.C_RESET}"
                                                 for c in cols)
    out = [hdr]
    for label, d in rows:
        cells = []
        for c in cols:
            if c in d:
                pct, _r = d[c]
                col = _pct_color(pct)
                cells.append(f"{render.meter(pct, width=cell_w, color=col)}{col}{pct:>4.0f}%{_c.C_RESET}")
            else:
                cells.append(' ' * (cell_w + 5))
        out.append(f"  {_c.C_TITLE}{render.trunc(label, name_w):<{name_w}}{_c.C_RESET}  "
                   + '  '.join(cells))
    return '\n'.join(out)


def _one_account_line(windows, prefix=''):
    from . import render
    parts = []
    for label, pct, resets in windows[:4]:
        col = _pct_color(pct)
        seg = (f"{_c.C_DIM}{label}{_c.C_RESET} "
               f"{render.meter(pct, width=10, color=col)} "
               f"{col}{pct:.0f}%{_c.C_RESET}")
        if resets:
            seg += f" {_c.C_DIM}→ {_fmt_reset(resets)}{_c.C_RESET}"
        parts.append(seg)
    body = f'  {_c.C_DIM}·{_c.C_RESET}  '.join(parts)
    return (f"  {prefix}{body}" if prefix else f"  {body}")


def usage_status_line():
    """Plan-usage banner. One bar per configured account (labeled by email/name)
    when 2+ accounts exist; a single unlabeled bar otherwise. Empty until data
    is in (or unavailable)."""
    _ensure_started()
    with _lock:
        ready = _ready
        data = _data
        accts = [(v.get('name', ''), v.get('email', ''), v.get('data'))
                 for v in _acct_state.values() if v.get('data')]
    if not ready:
        return f'  {_c.C_DIM}Plan usage: checking...{_c.C_RESET}'

    # 2+ accounts → an ALIGNED grid: one row per account, one column per limit
    # window, so session/weekly/per-model line up vertically across accounts.
    if len(accts) >= 2:
        grid = _account_grid(accts)
        if grid:
            return grid

    # single account (back-compat)
    windows = _extract_windows(data)
    if not windows:
        return ''
    line = _one_account_line(windows)
    # optional daily-token alert badge (cache-only, cheap)
    try:
        alert = _c.load_settings().get('daily_token_alert', 0) or 0
        if alert:
            from .stats import today_tokens, fmt_tok
            tot = today_tokens()
            if tot >= alert:
                line += f"  {_c.C_DIM}·{_c.C_RESET}  {_c.C_WARN}today {fmt_tok(tot)} tok ⚠{_c.C_RESET}"
    except Exception:
        pass
    return line
