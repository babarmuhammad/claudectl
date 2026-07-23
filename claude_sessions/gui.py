"""claudectl GUI — a local web app served from the stdlib, zero deps.

Runs a ThreadingHTTPServer bound to 127.0.0.1 on a free port, opens the
default browser, and serves a single-page app (markup in gui_html.py).
All data comes from the same pure helpers the TUI uses; launching a session
spawns claude.exe in a NEW console window (a browser can't host a terminal),
reusing main.build_launch_command for exact TUI parity.

Security: the server only binds loopback, and every /api request must carry
the X-Claudectl header. Browsers won't attach custom headers cross-origin
without a CORS preflight (which we never approve), so a malicious web page
can't drive the launch endpoint from another tab.
"""

import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import config as _c
from .config import (load_settings, save_settings,
                     EFFORTS, MODELS, MODEL_LABELS, PERMS, PERM_LABELS,
                     THINKING_CAPS, THINKING_LABELS)
from .paths import find_actual_path


def all_config_dirs():
    # via the module (not by-value) so the test sandbox's patch is honored
    return _c.all_config_dirs()


# ── data assembly (pure, reused by tests) ────────────────────

def list_projects():
    """Grouped project rows across all accounts, newest-first.
    [{'path','name','encoded','mtime','accounts':[names],'primary_cfgdir'}]"""
    entries = []
    for _acct_name, acct_dir in all_config_dirs():
        pdir = os.path.join(acct_dir, 'projects')
        if not os.path.isdir(pdir):
            continue
        for name in os.listdir(pdir):
            proj = os.path.join(pdir, name)
            if not os.path.isdir(proj):
                continue
            actual = find_actual_path(name)
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
                    'auto_memory': bool((pd.get(enc) or {}).get('auto_memory'))})
    out.sort(key=lambda r: r['mtime'], reverse=True)
    return out


def list_sessions(encoded):
    """Sessions of a project across every account, newest-first.
    [{'sid','title','preview','age','count','account','cfgdir','tokens'}]"""
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
            try:
                st = get_session_stats_cached(jsonl)
                tot = sum(_sum_usage(st).values())
                if tot:
                    tokens = fmt_tok(tot)
            except Exception:
                pass
            out.append({'sid': sid, 'title': title, 'preview': preview,
                        'age': format_age(mtime).strip(), 'mtime': mtime,
                        'count': count, 'account': acct_name,
                        'cfgdir': cfgdir, 'tokens': tokens})
    out.sort(key=lambda r: r['mtime'], reverse=True)
    return out


def _x256_hex(sgr):
    """First xterm-256 foreground index in an SGR string → '#rrggbb'."""
    import re
    m = re.search(r'38;5;(\d+)', sgr)
    if not m:
        # 16-color codes (e.g. mono/default fallbacks) → sensible fixed map
        m16 = {'\033[97m': '#f0f6ff', '\033[96m': '#7dcfff', '\033[96;1m': '#7dcfff',
               '\033[93m': '#f7e05a', '\033[1m': '#f0f6ff', '\033[7m': '#8aa0c0'}
        return m16.get(sgr, '#7dcfff')
    n = int(m.group(1))
    if n < 16:
        base = [(0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16),
                (36, 114, 200), (188, 63, 188), (17, 168, 205), (229, 229, 229),
                (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67),
                (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255)]
        r, g, b = base[n]
    elif n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        r, g, b = steps[n // 36], steps[(n // 6) % 6], steps[n % 6]
    else:
        v = 8 + (n - 232) * 10
        r = g = b = v
    return f'#{r:02x}{g:02x}{b:02x}'


def _hex_hue(hexc):
    import colorsys
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[0]


def _hsl(h, s, l):
    import colorsys
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return f'#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}'


def theme_palettes():
    """Full GUI palette per TUI theme. Accents come straight from the TUI
    colors; every surface (bg, panels, borders, text) is re-derived from the
    accent hue so switching theme restyles the whole app, not just buttons."""
    out = {}
    for name, pal in _c.THEMES.items():
        accent = _x256_hex(pal.get('C_ACCENT', ''))
        h = _hex_hue(accent)
        s = 0.0 if name == 'mono' else 0.30
        light = name == 'catppuccin-latte'
        if light:
            surf = {'bg': _hsl(h, .25, .94), 'bg2': _hsl(h, .25, .90),
                    'panel': _hsl(h, .30, .97), 'panel2': _hsl(h, .25, .92),
                    'line': _hsl(h, .20, .80), 'txt': _hsl(h, .35, .15),
                    'dim': _hsl(h, .15, .40), 'dim2': _hsl(h, .12, .55),
                    'code': _hsl(h, .20, .88)}
        else:
            surf = {'bg': _hsl(h, s, .055), 'bg2': _hsl(h, s, .075),
                    'panel': _hsl(h, s, .10), 'panel2': _hsl(h, s, .13),
                    'line': _hsl(h, s * .8, .19), 'txt': _hsl(h, .30, .90),
                    'dim': _hsl(h, .14, .60), 'dim2': _hsl(h, .12, .42),
                    'code': _hsl(h, s, .04)}
        out[name] = {'accent': accent,
                     'accent2': _x256_hex(pal.get('C_TITLE', '')),
                     'ok': _x256_hex(pal.get('C_OK', '')),
                     'warn': _x256_hex(pal.get('C_WARN', '')),
                     **surf}
    return out


def state_payload():
    """Everything the SPA needs on load."""
    from .sessions import load_recent_sessions, load_name, get_session_title, format_age
    s = load_settings()
    recent = []
    for r in load_recent_sessions(5):
        pf = os.path.join(r.get('cfgdir') or _c.config_dir, 'projects',
                          r.get('encoded_name', ''))
        jsonl = os.path.join(pf, f"{r['session_id']}.jsonl")
        recent.append({'project': os.path.basename(r['project_path']) or r['project_path'],
                       'path': r['project_path'], 'encoded': r.get('encoded_name', ''),
                       'sid': r['session_id'],
                       'name': (load_name(pf, r['session_id'])
                                or get_session_title(jsonl)
                                or r.get('preview', '') or r['session_id'][:8]),
                       'age': format_age(r['timestamp']).strip() if r.get('timestamp') else '',
                       'cfgdir': r.get('cfgdir') or _c.config_dir})
    return {
        'projects': list_projects(),
        'recent': recent,
        'accounts': [{'name': n, 'dir': d} for n, d in all_config_dirs()],
        'active_cfgdir': _c.config_dir,
        'options': {
            'efforts': EFFORTS, 'models': MODELS, 'model_labels': MODEL_LABELS,
            'perms': PERMS, 'perm_labels': PERM_LABELS,
            'thinking': THINKING_CAPS, 'thinking_labels': THINKING_LABELS,
            'model_cards': _c.model_card_rows(),
            'effort_profiles': _c.EFFORT_PROFILES,
            'presets': [[n, d, f] for n, d, f in _c.LAUNCH_PRESETS],
            'advice': {m: {e: list(_c.advise(m, e)) for e in EFFORTS} for m in MODELS},
            'frontier': [list(r) for r in _c.frontier_rows()],
        },
        'defaults': {'effort': s.get('default_effort', ''),
                     'model': s.get('default_model', ''),
                     'perm': s.get('default_permission', ''),
                     'max_thinking': s.get('default_max_thinking', ''),
                     'subagent_model': s.get('default_subagent_model', '')},
        'ui_mode': s.get('ui_mode', 'tui'),
        'gui_shell': s.get('gui_shell', 'auto'),
        'plan_model': s.get('plan_model', ''),
        'exec_model': s.get('exec_model', ''),
        'extract_model': s.get('extract_model', ''),
        'omniroute_base_url': s.get('omniroute_base_url', ''),
        'omniroute_has_key': bool(s.get('omniroute_api_key')),
        'omniroute_exec_model': s.get('omniroute_exec_model', ''),
        'theme': s.get('theme', 'default'),
        'themes': theme_palettes(),
    }


def launch_session(path, encoded, choice, opts):
    """Spawn claude.exe in a NEW console window. Returns (ok, error)."""
    from .main import build_launch_command
    try:
        args, env, proj_folder = build_launch_command(path, encoded, choice, opts)
    except RuntimeError as e:
        return False, str(e)
    title = f'claude — {os.path.basename(path) or path}'
    # CREATE_NEW_CONSOLE directly: the old `cmd /c start …` chain, spawned
    # from a windowless GUI process, produced a broken/transparent console
    # window under Windows Terminal. argv-list form (no shell=True) so
    # nothing user-controlled can break out; `title` via cmd builtin.
    # `title` arg always contains spaces (the 'claude — ' prefix) so
    # list2cmdline quotes it and any & in the project name stays literal.
    if args is None:   # plain terminal: stays open by design
        cmd = ['cmd', '/k', 'title', title]
    else:              # session: window always closes when claude exits, any
        # exit code — Ctrl+C and other normal ways of ending a session return
        # non-zero on Windows, and a `|| pause` here left the window stuck
        # open waiting on a keypress on every such exit, not just real crashes.
        cmd = ['cmd', '/c', 'title', title, '&&'] + args
    try:
        subprocess.Popen(cmd, cwd=path, env=env,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
    except Exception as e:
        return False, str(e)
    try:
        from . import workspace
        workspace.update_manifest(path, proj_folder, 'launch', choice=choice,
                                  opts={k: opts.get(k) for k in ('effort', 'model', 'perm')})
    except Exception:
        pass
    return True, ''


def rename_session(encoded, cfgdir, sid, name):
    from .sessions import save_name
    folder = os.path.join(cfgdir, 'projects', encoded)
    if not os.path.isdir(folder):
        return False
    save_name(folder, sid, name)
    return True


# ── HTTP layer ───────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):   # silence per-request stderr noise
        pass

    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', f'{ctype}; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _guard(self):
        """Reject anything a cross-origin page could send (see module doc)."""
        if self.headers.get('X-Claudectl') != '1':
            self._send(403, {'error': 'missing X-Claudectl header'})
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == '/':
            from .gui_html import PAGE
            self._send(200, PAGE.encode('utf-8'), ctype='text/html')
            return
        if u.path == '/graph':
            self._serve_graph(q)
            return
        if not self._guard():
            return
        if u.path == '/api/state':
            self._send(200, state_payload())
        elif u.path == '/api/sessions':
            self._send(200, {'sessions': list_sessions(q.get('enc', ''))})
        elif u.path.startswith('/api/job/'):
            from .gui_api import job_status
            st = job_status(u.path.rsplit('/', 1)[1])
            self._send(200 if st else 404, st or {'error': 'no such job'})
        else:
            from . import gui_api
            fn = gui_api.GET_ROUTES.get(u.path)
            if fn is None:
                self._send(404, {'error': 'not found'})
                return
            try:
                import time
                t0 = time.time()
                out = fn(q, None)
                dt = time.time() - t0
                if dt > 0.5:
                    _c.log.warning('gui api GET %s slow: %.2fs', u.path, dt)
                self._send(200, out)
            except Exception as e:
                _c.log.exception('gui api GET %s failed', u.path)
                self._send(500, {'error': str(e)})

    def _serve_graph(self, q):
        try:
            from . import connections
            path, enc = q.get('path', ''), q.get('enc', '')
            proj_folder = os.path.join(_c.config_dir, 'projects', enc) if enc else None
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
            body = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            self._send(400, {'error': 'bad json'})
            return
        u = urlparse(self.path)
        if u.path == '/api/launch':
            opts = {'effort': '', 'model': '', 'perm': '', 'name': '',
                    'worktree': '', 'agent': '', 'agents_json': '', 'cfgdir': '',
                    'max_thinking': '', 'subagent_model': ''}
            opts.update({k: str(v) for k, v in (body.get('opts') or {}).items()
                         if k in opts})
            ok, err = launch_session(body.get('path', ''), body.get('enc', ''),
                                     body.get('choice', 'new'), opts)
            self._send(200 if ok else 500, {'ok': ok, 'error': err})
        elif u.path == '/api/rename':
            ok = rename_session(body.get('enc', ''), body.get('cfgdir', ''),
                                body.get('sid', ''), body.get('name', ''))
            self._send(200 if ok else 500, {'ok': ok})
        elif u.path == '/api/settings':
            s = load_settings()
            if body.get('ui_mode') in ('tui', 'gui'):
                s['ui_mode'] = body['ui_mode']
            for k in ('default_effort', 'default_model', 'default_permission',
                      'default_max_thinking', 'default_subagent_model',
                      'extract_model', 'review_model', 'review_min_confidence',
                      'gui_shell', 'theme', 'editor', 'claude_exe',
                      'plan_model', 'exec_model', 'omniroute_base_url',
                      'omniroute_exec_model'):
                if k in body:
                    s[k] = body[k]
            # api_key only overwritten when the user actually typed a new one —
            # never blanked by a settings-save round-trip that omits it because
            # the frontend never receives the raw key back to resubmit
            if body.get('omniroute_api_key'):
                s['omniroute_api_key'] = body['omniroute_api_key']
            save_settings(s)
            self._send(200, {'ok': True})
        elif u.path.startswith('/api/job/') and u.path.endswith('/decide'):
            from .gui_api import job_decide
            jid = u.path.split('/')[3]
            ok = job_decide(jid, bool(body.get('apply')))
            self._send(200 if ok else 404, {'ok': ok})
        elif u.path.startswith('/api/job/') and u.path.endswith('/cancel'):
            from .gui_api import job_cancel
            jid = u.path.split('/')[3]
            ok = job_cancel(jid)
            self._send(200 if ok else 404, {'ok': ok})
        else:
            from . import gui_api
            fn = gui_api.POST_ROUTES.get(u.path)
            if fn is None:
                self._send(404, {'error': 'not found'})
                return
            try:
                self._send(200, fn({}, body))
            except Exception as e:
                _c.log.exception('gui api POST %s failed', u.path)
                self._send(500, {'error': str(e)})


def make_server(port=0):
    """Bind 127.0.0.1:<port> (0 = ephemeral). Returns the server object."""
    return ThreadingHTTPServer(('127.0.0.1', port), _Handler)


_EDGE_PATHS = (
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
)


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
