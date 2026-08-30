"""claudectl GUI — a local web app served from the stdlib, zero deps.

Runs a ThreadingHTTPServer bound to 127.0.0.1 on a free port, opens the
default browser, and serves a single-page app (markup in gui_html.py).
All data comes from the same pure helpers the TUI uses; launching a session
spawns claude.exe in a NEW console window (a browser can't host a terminal),
reusing main.build_launch_command for exact TUI parity.

Security: the server binds loopback only, and _guard() enforces three things
on every /api request — an allowlisted Host, no browser fetch-metadata, and a
per-run secret in X-Claudectl. A custom header alone is NOT enough: it stops a
plain cross-origin fetch, but under DNS rebinding the attacker's own origin IS
this server, so it may send any header it likes with no preflight. The Host
check is what actually closes that, and TOKEN closes the case of another local
process that guessed the port.
"""

import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import config as _c
from . import themes as _themes
from . import session_menu as _session_menu
from .config import (load_settings, save_settings,
                     EFFORTS, PERMS, PERM_LABELS,
                     THINKING_CAPS, THINKING_LABELS)
from .paths import find_actual_path
from .sessions import _is_anthropic_model, _used_omni   # noqa: F401 (re-exported)
from . import store


def all_config_dirs():
    # via the module (not by-value) so the test sandbox's patch is honored
    return _c.all_config_dirs()


# ── data assembly (pure, reused by tests) ────────────────────

def list_projects():
    """Grouped project rows across all accounts, newest-first.
    [{'path','name','encoded','mtime','accounts':[names],'primary_cfgdir'}]"""
    entries = []
    for _acct_name, acct_dir in all_config_dirs():
        pdir = store.projects_root(acct_dir)
        if not os.path.isdir(pdir):
            continue
        for name in os.listdir(pdir):
            proj = os.path.join(pdir, name)
            if not os.path.isdir(proj):
                continue
            actual = find_actual_path(name, folder=proj)
            if not actual:
                continue
            entries.append((os.path.getmtime(proj), actual, name, acct_dir))

    order = {d: i for i, (_n, d) in enumerate(all_config_dirs())}
    names = {d: n for n, d in all_config_dirs()}
    groups = {}
    for mtime, actual, enc, acct_dir in entries:
        g = groups.setdefault(enc, {'path': actual, 'dirs': set(), 'mtime': mtime})
        g['dirs'].add(acct_dir)
        g['mtime'] = max(g['mtime'], mtime)
    pd = load_settings().get('project_defaults') or {}
    from .sessions import format_age
    out = []
    for enc, g in groups.items():
        dirs = sorted(g['dirs'], key=lambda d: order.get(d, 999))
        out.append({'path': g['path'],
                    'name': os.path.basename(g['path']) or g['path'],
                    'encoded': enc, 'mtime': g['mtime'],
                    'last_active': format_age(g['mtime']).strip(),
                    'accounts': [names.get(d, os.path.basename(d)) for d in dirs],
                    'primary_cfgdir': dirs[0],
                    'auto_memory': bool((pd.get(enc) or {}).get('auto_memory')),
                    # the TUI's `!` badge condition, verbatim (session_menu.py):
                    # two isfile() per project, negligible beside find_actual_path
                    'set_up': (os.path.isfile(os.path.join(g['path'], 'CLAUDE.md'))
                               or os.path.isfile(os.path.join(
                                   g['path'], '.claudectl', 'memory', 'graph.json')))})
    out.sort(key=lambda r: r['mtime'], reverse=True)
    return out


def list_sessions(encoded):
    """Sessions of a project across every account, newest-first.
    [{'sid','title','preview','age','count','account','cfgdir','tokens','omni'}]"""
    from .sessions import (account_folders_for, scan_sessions, load_name,
                           get_session_title, format_age)
    from .stats import get_session_stats_cached, _sum_usage, fmt_tok
    out = []
    for acct_name, folder in account_folders_for(encoded):
        cfgdir = os.path.dirname(os.path.dirname(folder))
        for mtime, sid, preview, count in scan_sessions(folder):
            jsonl = os.path.join(folder, f'{sid}.jsonl')
            title = load_name(folder, sid) or get_session_title(jsonl) or ''
            tokens = ''
            omni = False
            try:
                st = get_session_stats_cached(jsonl)
                tot = sum(_sum_usage(st).values())
                if tot:
                    tokens = fmt_tok(tot)
                omni = _used_omni(st)
            except Exception:
                pass
            out.append({'sid': sid, 'title': title, 'preview': preview,
                        'age': format_age(mtime).strip(), 'mtime': mtime,
                        'count': count, 'account': acct_name,
                        'cfgdir': cfgdir, 'tokens': tokens, 'omni': omni})
    out.sort(key=lambda r: r['mtime'], reverse=True)
    return out


def theme_palettes():
    """Full GUI palette per theme, straight from the authored hex tables.

    Nothing is derived here any more: themes.PALETTES holds every surface as
    real hex, so a named scheme actually looks like itself. The metadata keys
    (label/family/mode/motion) ride along for the settings gallery and the
    ambient motion layer."""
    return {name: dict(pal) for name, pal in _themes.PALETTES.items()}


def state_payload():
    """Everything the SPA needs on load."""
    from .sessions import load_recent_sessions, load_name, get_session_title, format_age
    s = load_settings()
    recent = []
    for r in load_recent_sessions(5):
        enc = r.get('encoded_name', '')
        pf = store.project_folder(r.get('cfgdir'), enc) if enc else ''
        jsonl = os.path.join(pf, f"{r['session_id']}.jsonl")
        recent.append({'project': os.path.basename(r['project_path']) or r['project_path'],
                       'path': r['project_path'], 'encoded': r.get('encoded_name', ''),
                       'sid': r['session_id'],
                       'name': (load_name(pf, r['session_id'])
                                or get_session_title(jsonl)
                                or r.get('preview', '') or r['session_id'][:8]),
                       'age': format_age(r['timestamp']).strip() if r.get('timestamp') else '',
                       'cfgdir': r.get('cfgdir') or _c.config_dir})
    from . import models as _m
    _models, _model_labels = _c.models(s.get('default_model', ''),
                                       s.get('default_subagent_model', ''),
                                       s.get('extract_model', ''))
    return {
        'projects': list_projects(),
        'recent': recent,
        'accounts': [{'name': n, 'dir': d} for n, d in all_config_dirs()],
        'active_cfgdir': _c.config_dir,
        'options': {
            # LIVE roster (models.py), floor-backed. The three saved pins ride
            # along so a retired model still renders in the picker instead of
            # reading back as 'default' — the GUI writes ids[idx] on save too.
            'efforts': EFFORTS, 'models': _models, 'model_labels': _model_labels,
            'perms': PERMS, 'perm_labels': PERM_LABELS,
            'perm_profiles': _c.PERM_PROFILES,
            # (level, message) per mode x model — the pair the TUI renders under
            # the permission row. Small enough to precompute; unlike advise() it
            # does not multiply by effort.
            'perm_notes': {p: {m: list(_c.perm_note(p, m)) for m in _models}
                           for p in PERMS},
            'thinking': THINKING_CAPS, 'thinking_labels': THINKING_LABELS,
            'model_cards': _c.model_card_rows(),
            'effort_profiles': _c.EFFORT_PROFILES,
            'presets': [[n, d, f] for n, d, f in _c.LAUNCH_PRESETS],
            'advice': {m: {e: list(_c.advise(m, e)) for e in EFFORTS} for m in _models},
            # ids, not sentences: the launch picker asks only whether the
            # one model in front of you is still offered.
            'model_retired': _m.retired_pins(),
            'frontier': [list(r) for r in _c.frontier_rows()],
        },
        'defaults': {'effort': s.get('default_effort', ''),
                     'model': s.get('default_model', ''),
                     'perm': s.get('default_permission', ''),
                     'max_thinking': s.get('default_max_thinking', ''),
                     'subagent_model': s.get('default_subagent_model', '')},
        'ui_mode': s.get('ui_mode', 'tui'),
        'editor': s.get('editor', ''),
        'claude_exe': s.get('claude_exe', ''),
        'claude_config_dir': s.get('claude_config_dir', ''),
        'headless_budget_usd': s.get('headless_budget_usd', 0),
        'memory_budget': s.get('memory_budget', 600),
        'gui_shell': s.get('gui_shell', 'auto'),
        'auto_update': s.get('auto_update', 'notify'),
        'plan_model': s.get('plan_model', ''),
        'exec_model': s.get('exec_model', ''),
        'extract_model': s.get('extract_model', ''),
        'provider_base_url': s.get('provider_base_url', ''),
        'provider_has_key': bool(s.get('provider_api_key')),
        'provider_exec_model': s.get('provider_exec_model', ''),
        'provider_kind': s.get('provider_kind', ''),
        'failover_models': s.get('failover_models', []),
        'failover_port': s.get('failover_port', 20129),
        'failover_quiet': bool(s.get('failover_quiet')),
        'theme': s.get('theme', 'default'),
        'motion': _motion_level(s),
        # collapsed sidebar nav groups — a list of group names, so adding or
        # renaming a group never needs a migration: an unknown name is simply
        # not matched by any group and is carried through untouched.
        'nav_collapsed': [str(x) for x in (s.get('nav_collapsed') or [])],
        # 0 = never dragged; the CSS default stays in charge
        'side_w': int(s.get('side_w') or 0),
        'nav_h': int(s.get('nav_h') or 0),
        # '' = wear whatever skin the selected palette names as its default
        'skin': s.get('skin', ''),
        # '' = classic mode (palette x skin). A name = that world, locked.
        'world': s.get('world', '') if s.get('world') in _themes.WORLDS else '',
        'stage': _stage_tier(s),
        # 0 = follow whatever the look asks for; 40..100 = an explicit override
        'surface': _surface(s),
        'otel': {'enabled': bool(s.get('otel_enabled')),
                 'endpoint': s.get('otel_endpoint', ''),
                 'protocol': s.get('otel_protocol', 'http/protobuf'),
                 'headers': s.get('otel_headers', '')},
        'themes': theme_palettes(),
        'skins': {n: dict(v) for n, v in _themes.SKINS.items()},
        'worlds': {n: dict(v) for n, v in _themes.WORLDS.items()},
        'classic_skins': list(_themes.CLASSIC_SKINS),
        # the terminal UI's own key list, shipped so the GUI help page and
        # ui._session_key_lines are two renderings of ONE list instead of two
        # hand-typed tables that drift (the GUI's already had).
        'tui_keys': [list(r) for r in _session_menu.key_rows()],
    }


def _motion_level(s):
    """How much motion the GUI runs: 'full' | 'subtle' | 'off'.

    One knob replacing four (theme_motion = which of 26 background renderers,
    theme_motion_scope, theme_motion_bg, theme_motion_intensity). Those keys are
    still *read* so a settings.json written before the overhaul resolves to
    something sensible instead of silently switching a user who had turned the
    old animation off back on. Only 'motion' is written from here on."""
    if s.get('motion') in ('full', 'subtle', 'off'):
        return s['motion']
    # legacy: either off-switch meant "I don't want this moving"
    if s.get('theme_motion') == 'off' or s.get('theme_motion_scope') == 'off':
        return 'off'
    # legacy 'panels' scope = the band only, no gauges in the chrome — the
    # nearest equivalent now is informational motion without the flourishes
    if s.get('theme_motion_scope') == 'panels':
        return 'subtle'
    return 'full'


def _surface(s):
    """How opaque the panels are, as a percentage — or 0 for "ask the look".

    Every look ships an `op` chosen for its own scene, but how much background
    you want showing through your working surfaces is a taste question and a
    per-monitor one, so it is exposed. Clamped at 40: below that the text starts
    fighting the scene behind it whatever the look says."""
    try:
        v = int(s.get('surface') or 0)
    except (TypeError, ValueError):
        return 0
    return 0 if v <= 0 else max(40, min(100, v))


def _stage_tier(s):
    """How much of the animated background runs: 'cinematic' | 'lite' | 'off'.

    Separate from `motion` on purpose. `motion` governs the UI's own
    transitions, which everyone wants some of; the stage is a second GPU surface
    in a shell with a documented surface-tearing history, so it needs its own
    switch that can be turned down without also flattening the interface.

    motion:off still forces it off — someone who has asked for no animation has
    not asked for a 3D background."""
    if _motion_level(s) == 'off':
        return 'off'
    t = s.get('stage')
    # Default is `lite`, not `cinematic`. The first cut shipped bloom on by
    # default and the verdict was "overstimulating, confonde" — both users then
    # picked the one skin with no background at all. The scene still runs; it
    # just stops competing with the text. Bloom is opt-in now.
    return t if t in _themes.STAGE_TIERS else 'lite'


def launch_session(path, encoded, choice, opts):
    """Spawn claude.exe in a NEW console window. Returns (ok, error)."""
    from .main import build_launch_command
    from .paths import resolve_dir
    # `path` arrives in a request body and becomes a subprocess cwd; validate
    # before spawning, not after.
    if not resolve_dir(path):
        return False, 'not a directory: %s' % (path or '(empty)')
    try:
        args, env, proj_folder = build_launch_command(path, encoded, choice, opts)
    except RuntimeError as e:
        return False, str(e)
    title = f'claude — {os.path.basename(path) or path}'
    # CREATE_NEW_CONSOLE directly (inside proc.spawn_terminal): the old
    # `cmd /c start …` chain, spawned from a windowless GUI process, produced a
    # broken/transparent console window under Windows Terminal.
    from . import proc
    _p, err = proc.spawn_terminal(args, cwd=path, env=env, title=title,
                                  keep_open=args is None)
    if err:
        return False, err
    try:
        from . import workspace
        workspace.update_manifest(path, proj_folder, 'launch', choice=choice,
                                  opts={k: opts.get(k) for k in ('effort', 'model', 'perm')})
    except Exception:
        pass
    return True, ''


def rename_session(encoded, cfgdir, sid, name):
    from .sessions import save_name
    folder = store.project_folder(cfgdir, encoded)
    if not os.path.isdir(folder):
        return False
    save_name(folder, sid, name)
    return True


# ── HTTP layer ───────────────────────────────────────────────

#: Per-run secret. Handed to the SPA by rewriting the page at send time, so it
#: never lands on disk, in a URL the user might paste, or in a previous run's
#: browser history. Tests read this rather than hardcoding a value.
TOKEN = secrets.token_urlsafe(24)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):   # silence per-request stderr noise
        pass

    def _send(self, code, body, ctype='application/json', cache=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', f'{ctype}; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache or 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        # Everything the SPA loads is served from this origin; 'unsafe-inline'
        # is needed because app.js/app.css are inlined into the page string and
        # the markup uses onclick= attributes throughout.
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _drain(self, n):
        """Discard n bytes of request body in fixed-size chunks."""
        left = n
        while left > 0:
            chunk = self.rfile.read(min(65536, left))
            if not chunk:
                return
            left -= len(chunk)

    def _host_ok(self):
        """A DNS-rebound request reaches us with the attacker's hostname in Host
        — this is the check that stops it. Runs even on the unguarded routes."""
        host = (self.headers.get('Host') or '').strip().lower()
        port = self.server.server_address[1]
        return host in ('127.0.0.1:%d' % port, 'localhost:%d' % port,
                        '[::1]:%d' % port)

    def _guard(self):
        """Reject anything a cross-origin page could send (see module doc)."""
        if not self._host_ok():
            self._send(403, {'error': 'bad host'})
            return False
        if not hmac.compare_digest(self.headers.get('X-Claudectl') or '', TOKEN):
            self._send(403, {'error': 'missing or bad X-Claudectl header'})
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._host_ok():
            self._send(403, {'error': 'bad host'})
            return
        if u.path == '/':
            from .gui_html import PAGE
            page = PAGE.replace('__CLAUDECTL_TOKEN__', TOKEN)
            self._send(200, page.encode('utf-8'), ctype='text/html')
            return
        if u.path == '/graph':
            # Opened with window.open(), so it cannot carry a header — the token
            # rides the query string instead.
            if not hmac.compare_digest(q.get('k') or '', TOKEN):
                self._send(403, {'error': 'missing or bad token'})
                return
            self._serve_graph(q)
            return
        if u.path.startswith('/vendor/'):
            self._serve_vendor(u.path[len('/vendor/'):])
            return
        if not self._guard():
            return
        if u.path.startswith('/api/job/'):
            from .gui_api import job_status
            st = job_status(u.path.rsplit('/', 1)[1])
            self._send(200 if st else 404, st or {'error': 'no such job'})
            return
        from . import gui_api
        fn = _LOCAL_GET.get(u.path) or gui_api.GET_ROUTES.get(u.path)
        if fn is None:
            self._send(404, {'error': 'not found'})
            return
        self._api('GET', u.path, fn, q, None)

    def _api(self, verb, path, fn, q, body):
        """One place where an endpoint's result — or its failure — becomes a
        response. The hardcoded routes used to bypass this and let an exception
        reach BaseHTTPRequestHandler, which answers with an HTML traceback the
        SPA's resp.json() then chokes on."""
        from . import gui_api
        import time
        t0 = time.time()
        try:
            out = gui_api.call(fn, q, body)
        except gui_api.BadRequest as e:
            self._send(400, {'error': str(e)})
            return
        except ValueError as e:
            # store.project_folder rejecting a name that could not have come
            # from the encoder is the caller's fault, not ours
            self._send(400, {'error': str(e)})
            return
        except Exception as e:
            _c.log.exception('gui api %s %s failed', verb, path)
            self._send(500, {'error': str(e)})
            return
        dt = time.time() - t0
        if dt > 0.5:
            _c.log.warning('gui api %s %s slow: %.2fs', verb, path, dt)
        self._send(200, out)

    def _serve_vendor(self, rel):
        """Vendored browser libraries (three.js, anime.js) — see gui_html.

        Deliberately BEFORE _guard(): a <script src> cannot attach the
        X-Claudectl header, so a guarded route would 403 every module fetch.
        Nothing is exposed by that — these are public MIT libraries, byte-identical
        to their npm originals, and the allowlist is a dict built by walking
        web/vendor at import, so a traversal attempt is simply a miss.

        Cached hard: ~890KB of pinned, immutable library code should be fetched
        once, not on every reload."""
        from .gui_html import vendor_asset
        got = vendor_asset(rel)
        if got is None:
            self._send(404, {'error': 'not found'})
            return
        data, ctype = got
        self._send(200, data, ctype=ctype,
                   cache='public, max-age=604800, immutable')

    def _serve_graph(self, q):
        try:
            from . import connections
            path, enc = q.get('path', ''), q.get('enc', '')
            if not path or not os.path.isdir(path):
                self._send(400, {'error': 'path is not a directory'})
                return
            if enc and (os.sep in enc or '/' in enc or enc in ('.', '..')):
                self._send(400, {'error': 'bad enc'})
                return
            proj_folder = store.project_folder(None, enc) if enc else None
            g = connections.build_hierarchy(path, proj_folder)
            try:
                mem = connections.build_memory_hierarchy(path, proj_folder)
            except Exception:
                mem = None
            self._send(200, connections.render_html(g, memory=mem).encode('utf-8'),
                       ctype='text/html')
        except Exception as e:
            self._send(500, {'error': str(e)})

    def do_POST(self):
        if not self._guard():
            return
        try:
            n = int(self.headers.get('Content-Length', 0))
        except Exception:
            self._send(400, {'error': 'bad content-length'})
            return
        if n > MAX_BODY:
            # Content-Length was trusted verbatim and read unbounded: one
            # request could pin as much memory as it liked.
            #
            # The oversized body still has to be drained in bounded chunks
            # before answering. Replying and closing while the client is still
            # writing resets the connection, and the client then sees a socket
            # error instead of the 413 — which is how this first showed up: the
            # test passed alone and failed in a full run.
            self._drain(min(n, MAX_DRAIN))
            self.close_connection = True
            self._send(413, {'error': 'body too large'})
            return
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            self._send(400, {'error': 'bad json'})
            return
        if not isinstance(body, dict):
            self._send(400, {'error': 'body must be a JSON object'})
            return
        u = urlparse(self.path)
        if u.path.startswith('/api/job/') and u.path.endswith('/decide'):
            from .gui_api import job_decide
            jid = u.path.split('/')[3]
            ok = job_decide(jid, bool(body.get('apply')))
            self._send(200 if ok else 404, {'ok': ok})
        elif u.path.startswith('/api/job/') and u.path.endswith('/cancel'):
            from .gui_api import job_cancel
            jid = u.path.split('/')[3]
            ok = job_cancel(jid)
            self._send(200 if ok else 404, {'ok': ok})
            return
        from . import gui_api
        fn = _LOCAL_POST.get(u.path) or gui_api.POST_ROUTES.get(u.path)
        if fn is None:
            self._send(404, {'error': 'not found'})
            return
        self._api('POST', u.path, fn, {}, body)


# ── the endpoints that live here rather than in gui_api ──────
# Same (q, body) -> dict shape as every other route, so they go through the
# same parameter checks and the same error mapping. Held apart from gui_api's
# tables only because they call this module's launch/rename/settings helpers.

def _api_state(q, body):
    return state_payload()


def _api_sessions(q, body):
    return {'sessions': list_sessions(q.get('enc', ''))}


def _api_launch(q, body):
    opts = {'effort': '', 'model': '', 'perm': '', 'name': '',
            'worktree': '', 'agent': '', 'agents_json': '', 'cfgdir': '',
            'max_thinking': '', 'subagent_model': ''}
    opts.update({k: str(v) for k, v in (body.get('opts') or {}).items()
                 if k in opts})
    ok, err = launch_session(body.get('path', ''), body.get('enc', ''),
                             body.get('choice', 'new'), opts)
    if not ok:
        # a bad path or an unlaunchable choice is the caller's mistake; it used
        # to come back as a 500 that read like the server had broken
        from .gui_api import BadRequest
        raise BadRequest(err)
    return {'ok': True, 'error': ''}


def _api_rename(q, body):
    ok = rename_session(body.get('enc', ''), body.get('cfgdir', ''),
                        body.get('sid', ''), body.get('name', ''))
    if not ok:
        from .gui_api import BadRequest
        raise BadRequest('could not rename that session')
    return {'ok': True}


#: DERIVED from the one canonical registry, minus the keys with an explicit
#: owner. A sixth hand-maintained table would be a copy of _DEFAULT_SETTINGS
#: that falls behind it — `claude_config_dir` and the paths were accepted here
#: and had no control for exactly that reason.
_SETTING_KEYS = tuple(sorted(set(_c._DEFAULT_SETTINGS) - _c.INTERNAL_SETTINGS))

#: settings that name a file or directory: refused when they do not exist, the
#: way the TUI refuses them (ui.py), so a save cannot silently pin something
#: broken and leave every launch failing with no clue why.
_PATH_SETTINGS = {'editor': 'file', 'claude_exe': 'file',
                  'claude_config_dir': 'dir'}


def _api_settings(q, body):
    from .gui_api import BadRequest
    s = load_settings()
    if body.get('ui_mode') in ('tui', 'gui'):
        s['ui_mode'] = body['ui_mode']
    for k, kind in _PATH_SETTINGS.items():
        v = os.path.expanduser(os.path.expandvars(str(body.get(k) or '').strip()))
        if v and not (os.path.isfile(v) if kind == 'file' else os.path.isdir(v)):
            raise BadRequest('%s: no such %s — %s' % (k, kind, v[:120]))
    for k in _SETTING_KEYS:
        if k in body:
            s[k] = body[k]
    # a dollar cap on claudectl's own headless calls: its own owner because it
    # is the one float, and an unclamped one silently disables the cap
    if 'headless_budget_usd' in body:
        try:
            s['headless_budget_usd'] = max(0.0, min(1000.0,
                                                    float(body['headless_budget_usd'] or 0)))
        except (TypeError, ValueError):
            raise BadRequest('headless_budget_usd must be a number')
    # failover_models is user input that a detached daemon reads back —
    # sanitize at this trust boundary rather than in the daemon.
    if 'failover_models' in body:
        raw = body['failover_models']
        if isinstance(raw, str):
            raw = raw.replace(',', '\n').split('\n')
        s['failover_models'] = [
            str(m).strip() for m in (raw or []) if str(m).strip()][:8]
    # Chrome geometry is user input that decides layout on the NEXT boot, so a
    # junk value would render an unusable window with no obvious way back.
    # Clamped and typed here rather than trusted from the client.
    if 'nav_collapsed' in body:
        raw = body['nav_collapsed']
        s['nav_collapsed'] = ([str(x)[:40] for x in raw][:16]
                              if isinstance(raw, list) else [])
    for key, lo, hi in (('side_w', 210, 520), ('nav_h', 34, 900)):
        if key in body:
            try:
                v = int(body[key] or 0)
            except (TypeError, ValueError):
                v = 0
            s[key] = 0 if v <= 0 else max(lo, min(hi, v))
    # api_key only overwritten when the user actually typed a new one — never
    # blanked by a settings-save round-trip that omits it because the frontend
    # never receives the raw key back to resubmit
    if body.get('provider_api_key'):
        s['provider_api_key'] = body['provider_api_key']
    save_settings(s)
    return {'ok': True}


_LOCAL_GET = {'/api/state': _api_state, '/api/sessions': _api_sessions}
_LOCAL_POST = {'/api/launch': _api_launch, '/api/rename': _api_rename,
               '/api/settings': _api_settings}


#: the biggest thing the SPA legitimately posts is an edited system prompt or a
#: plan; a megabyte is far above that and far below "pin all the memory"
MAX_BODY = 1024 * 1024

#: how much of an over-sized body is drained before the connection is dropped.
#: Bounded, so this is not itself the unbounded read it exists to prevent.
MAX_DRAIN = 8 * 1024 * 1024

#: ThreadingHTTPServer starts one thread per connection with no ceiling. The
#: SPA opens a handful; anything past this is a client that has gone wrong.
MAX_CONNECTIONS = 32


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def server_close(self):
        # The two background loops outlive the server otherwise: closing the
        # socket says nothing to a thread parked in a sleep.
        try:
            from .gui_api import stop_auto_memory_scheduler
            stop_auto_memory_scheduler()
            from .usage import stop_background
            stop_background()
        except Exception:
            _c.log.exception('gui: stopping background loops failed')
        super().server_close()

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def process_request(self, request, client_address):
        if not self._slots.acquire(timeout=5):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)


def make_server(port=0):
    """Bind 127.0.0.1:<port> (0 = ephemeral). Returns the server object."""
    return _Server(('127.0.0.1', port), _Handler)


#: a Chromium that accepts --app=<url>, for the chromeless standalone window.
#: Absolute paths on Windows (Edge is not on PATH there); a name lookup
#: elsewhere. Falls back to a plain browser tab when none is found.
if os.name == 'nt':
    _EDGE_PATHS = (
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    )
elif sys.platform == 'darwin':
    _EDGE_PATHS = (
        '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    )
else:
    import shutil as _shutil
    _EDGE_PATHS = tuple(p for p in (
        _shutil.which('microsoft-edge'), _shutil.which('google-chrome'),
        _shutil.which('chromium'), _shutil.which('chromium-browser')) if p)


def run_gui(open_browser=True):
    """Show the GUI as a desktop app. Shell preference (settings gui_shell):
    'auto' tries PyQt6 native window → Edge app-mode window → browser tab.
    Blocks until the window closes / Ctrl+C. Entry for `claudectl --gui`."""
    shell = load_settings().get('gui_shell', 'auto')

    from .gui_api import start_auto_memory_scheduler
    start_auto_memory_scheduler()   # opt-in per-project background memory refresh

    if shell in ('auto', 'qt'):
        try:
            from .gui_qt import run_desktop
            run_desktop()
            return
        except ImportError:
            if shell == 'qt' and sys.stdout:
                print('  PyQt6 not installed — falling back', flush=True)
        except Exception:
            _c.log.exception('gui: qt shell failed')

    srv = make_server()
    port = srv.server_address[1]
    url = f'http://127.0.0.1:{port}/'
    if sys.stdout:   # None under pythonw (desktop shortcut launch)
        try:
            # the --gui branch runs before the TUI's UTF-8 console setup,
            # so make non-ASCII safe on cp1252 consoles
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
        print(f'  claudectl GUI  →  {url}   (Ctrl+C to stop)', flush=True)

    def _open():
        if shell in ('auto', 'edge'):
            edge = next((p for p in _EDGE_PATHS if os.path.isfile(p)), '')
            if edge:
                try:   # chromeless standalone window, own taskbar entry
                    subprocess.Popen([edge, f'--app={url}'])
                    return
                except Exception:
                    pass
        webbrowser.open(url)

    if open_browser:
        threading.Timer(0.3, _open).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
