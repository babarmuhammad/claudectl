"""Subscription usage limits (5-hour window / weekly) for the main screen.

Queries the same OAuth usage endpoint Claude Code's /usage command uses,
authenticated with the local Claude Code OAuth token. Fetched once per run
on a background thread; absent/expired credentials degrade to no line.
"""

import os
import json
import threading
import urllib.request
from datetime import datetime, timezone

from . import config as _c

USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'

_lock    = threading.Lock()
_started = False
_ready   = False
_data    = None


def _read_token():
    try:
        p = os.path.join(_c.config_dir, '.credentials.json')
        with open(p, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return (d.get('claudeAiOauth') or {}).get('accessToken')
    except Exception:
        return None


def fetch_usage():
    """GET the OAuth usage endpoint. Returns parsed dict or None."""
    token = _read_token()
    if not token:
        return None
    req = urllib.request.Request(USAGE_URL, headers={
        'Authorization': f'Bearer {token}',
        'anthropic-beta': 'oauth-2025-04-20',
        'Content-Type': 'application/json',
        'User-Agent': 'claudectl',
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))
    except Exception:
        return None


def _background():
    global _data, _ready
    try:
        d = fetch_usage()
    except Exception:
        _c.log.exception('usage fetch failed')
        d = None
    with _lock:
        _data = d
        _ready = True


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
        return 'daily'
    if 'seven_day_opus' in k:
        return 'wk-opus'
    if 'seven_day_sonnet' in k:
        return 'wk-sonnet'
    if 'seven' in k or 'week' in k:
        return 'weekly'
    return key[:8]


def _limit_label(item):
    """Label a `limits[]` entry by its kind/group."""
    k = str(item.get('kind', '')).lower()
    g = str(item.get('group', '')).lower()
    if 'opus' in k:
        return 'wk-opus'
    if 'sonnet' in k:
        return 'wk-sonnet'
    if g == 'weekly' or 'week' in k:
        return 'weekly'
    if g == 'session' or 'session' in k or 'five' in k or '5h' in k:
        return 'daily'
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

    order = {'daily': 0, 'weekly': 1, 'wk-sonnet': 2, 'wk-opus': 3}
    out.sort(key=lambda w: order.get(w[0], 9))
    return out


def usage_status_line():
    """Footer line: '  Plan: 5h 32% → 14:30 · week 61% → Tue 09:00'.
    Empty string until data is in (or when unavailable)."""
    _ensure_started()
    with _lock:
        ready, data = _ready, _data
    if not ready:
        return f'  {_c.C_DIM}Plan usage: checking...{_c.C_RESET}'
    from . import render
    windows = _extract_windows(data)
    if not windows:
        return ''
    parts = []
    for label, pct, resets in windows[:3]:
        col = _pct_color(pct)
        seg = (f"{_c.C_DIM}{label}{_c.C_RESET} "
               f"{render.meter(pct, width=10, color=col)} "
               f"{col}{pct:.0f}%{_c.C_RESET}")
        if resets:
            seg += f" {_c.C_DIM}→ {_fmt_reset(resets)}{_c.C_RESET}"
        parts.append(seg)
    return '  ' + f'  {_c.C_DIM}·{_c.C_RESET}  '.join(parts)
