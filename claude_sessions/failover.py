"""Failover proxy — retry a dead model instead of hanging on it.

Claude Code sends every conversation turn as a fresh POST /v1/messages, and when
one fails it retries the SAME request against the SAME model ~10x with backoff.
So a model deregistered upstream ("minimax-m3-free is not supported", HTTP 401)
or a tool schema the backing provider rejects (Gemini 400s on a JSON-Schema node
carrying `properties` without `"type": "object"` — e.g. Notion MCP's rich_text)
makes a session look frozen forever. Nothing retried a *different* model: Claude
Code has no such concept, and claudectl only set ANTHROPIC_BASE_URL and walked
away.

This proxy sits between claude.exe and the real upstream. It does NOT translate
protocols — OmniRoute already speaks the Anthropic Messages API natively, so the
job is purely to forward bytes and, when a turn errors BEFORE any response body
byte has reached the client, rewrite the request's `model` and try the next
candidate. Request-level retry IS per-turn failover, because every turn is its
own request.

Runs as a DETACHED child process for the same reason as
memory.spawn_background_worker: the GUI launches sessions with
CREATE_NEW_CONSOLE (gui.py) and detached `cmd /c start` (gui_api.py), and the
user can then close claudectl. An in-process thread would die with it and leave
every live session with connection-refused on every turn — strictly worse than
the bug being fixed. Unlike that worker this spawns with CREATE_NEW_CONSOLE, not
CREATE_NO_WINDOW: the original complaint was not "a model died", it was "I could
not see that a model died", so the routing log is the feature.
"""

import hmac
import http.client
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as _c

_CONNECT_TIMEOUT = 10      # upstream is loopback; this is a safety net only
_FIRST_BYTE_TIMEOUT = 45   # per candidate, waiting for status+headers
_TOTAL_BUDGET = 90         # wall clock across ALL candidates for one request
_READY_TIMEOUT = 15        # how long ensure_running() waits for the daemon
_CHUNK = 65536
_LOG_MAX = 1 << 20

# A bare connectivity check would happily trust any process squatting the port,
# so readiness is proven by a marker only this server serves.
_MARKER_PATH = '/__claudectl_failover__/health'
_MARKER = b'claudectl-failover'

_HOP_BY_HOP = frozenset((
    'host', 'content-length', 'connection', 'proxy-connection', 'keep-alive',
    'transfer-encoding', 'te', 'upgrade', 'accept-encoding',
    'x-api-key', 'authorization',
))

_print_lock = threading.Lock()


def lock_path():
    return os.path.join(os.path.dirname(_c.settings_file), 'failover.lock')


def log_path():
    return os.path.join(os.path.dirname(_c.settings_file), 'failover.log')


def _emit(line):
    with _print_lock:
        try:
            print(line, flush=True)
        except Exception:
            pass
        try:
            p = log_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            mode = 'a'
            if os.path.isfile(p) and os.path.getsize(p) > _LOG_MAX:
                mode = 'w'
            with open(p, mode, encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass


def candidates(primary, s):
    """Ordered model ids to try: the requested one first, then the configured
    fallbacks, deduped with order preserved. Strips because the settings file is
    hand-editable and a whitespace-only entry is truthy."""
    out, seen = [], set()
    for m in [primary] + list(s.get('failover_models') or []):
        m = str(m or '').strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def enabled(s=None):
    s = _c.load_settings() if s is None else s
    return bool([m for m in (s.get('failover_models') or []) if str(m or '').strip()])


def base_url(s=None):
    s = _c.load_settings() if s is None else s
    return 'http://127.0.0.1:%d' % int(s.get('failover_port') or 20129)


# ── lifecycle ────────────────────────────────────────────────

def _pid_alive(pid):
    """True/False, or None when undeterminable (caller decides)."""
    from . import proc
    return proc.pid_alive(pid)


def is_ready(port, timeout=2):
    try:
        with urllib.request.urlopen(
                'http://127.0.0.1:%d%s' % (int(port), _MARKER_PATH),
                timeout=timeout) as r:
            return r.read(64).strip() == _MARKER
    except Exception:
        return False


def _read_lock():
    p = lock_path()
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_lock(port):
    p = lock_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({'pid': os.getpid(), 'port': int(port),
                       'started': time.time()}, f)
    except Exception:
        pass


def _clear_lock():
    try:
        os.remove(lock_path())
    except Exception:
        pass


def _claim_spawn(port):
    """True if THIS caller may spawn the daemon; False if someone else already is.

    O_EXCL is the whole point: the old code cleared the lock and then spawned, so
    two callers arriving together (two GUI tabs hitting plan_launch, or the TUI
    and the GUI at once) could both conclude "not running" and both spawn, and the
    loser's server then failed to bind the port. The claim is written before the
    spawn, not by the child after it starts."""
    p = lock_path()
    payload = json.dumps({'pid': os.getpid(), 'port': int(port),
                          'started': time.time()}).encode('utf-8')
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    except Exception:
        pass
    for _attempt in (0, 1):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            data = _read_lock()
            fresh = data and time.time() - (data.get('started') or 0) < _READY_TIMEOUT
            if fresh or (data and _pid_alive(data.get('pid')) is True):
                return False            # someone else is on it; go wait
            _clear_lock()               # stale claim from a dead run — retry once
            continue
        except Exception:
            return True                 # can't lock at all; better to spawn than not
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        return True
    return True


def ensure_running(s=None):
    """(ok, base_url) or (False, message). Never raises. Reuses a live daemon;
    evicts a stale lock and respawns."""
    import subprocess
    import sys
    s = _c.load_settings() if s is None else s
    port = int(s.get('failover_port') or 20129)

    if is_ready(port):
        return True, base_url(s)

    data = _read_lock()
    if data and _pid_alive(data.get('pid')) is True and int(data.get('port', 0)) == port:
        # Alive but not answering the marker yet — give it a moment.
        deadline = time.time() + 5
        while time.time() < deadline:
            if is_ready(port):
                return True, base_url(s)
            time.sleep(0.3)

    if not _claim_spawn(port):
        deadline = time.time() + _READY_TIMEOUT
        while time.time() < deadline:
            if is_ready(port):
                return True, base_url(s)
            time.sleep(0.3)
        return False, 'failover proxy did not become ready on port %d' % port

    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env['PYTHONPATH'] = pkg_parent + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    cmd = [sys.executable, '-m', 'claude_sessions', '--failover-serve', str(port)]
    try:
        if s.get('failover_quiet'):
            subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=env, cwd=pkg_parent,
                creationflags=(getattr(subprocess, 'DETACHED_PROCESS', 0)
                               | getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
        else:
            # Pass NO std handles: specifying even one sets STARTF_USESTDHANDLES,
            # which makes the child inherit the PARENT's stdout/stderr and defeats
            # CREATE_NEW_CONSOLE -- the log would then be written into the TUI's
            # alternate screen buffer and corrupt it.
            subprocess.Popen(
                cmd, env=env, cwd=pkg_parent,
                creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
    except Exception as e:
        _c.log.exception('failover: spawn failed')
        _clear_lock()           # release the claim, or nothing may ever retry
        return False, 'could not start failover proxy: %s' % e

    deadline = time.time() + _READY_TIMEOUT
    while time.time() < deadline:
        if is_ready(port):
            return True, base_url(s)
        time.sleep(0.4)
    return False, ('failover proxy did not come up on port %d — port may be in '
                   'use by another process' % port)


def stop_running():
    """(ok, message). Terminates the daemon named in the lock file."""
    data = _read_lock()
    _clear_lock()
    pid = (data or {}).get('pid')
    if not pid:
        return True, 'no failover proxy recorded'
    if _pid_alive(pid) is False:
        return True, 'failover proxy already gone'
    if os.name == 'nt':
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
            if not h:
                return False, 'could not open pid %s' % pid
            try:
                ok = bool(k32.TerminateProcess(h, 0))
            finally:
                k32.CloseHandle(h)
            return ok, 'stopped' if ok else 'could not stop pid %s' % pid
        except Exception as e:
            return False, str(e)
    try:
        import signal
        os.kill(int(pid), signal.SIGTERM)
        return True, 'stopped'
    except Exception as e:
        return False, str(e)


def serve_cli(port):
    s = _c.load_settings()
    port = int(port or s.get('failover_port') or 20129)
    try:
        srv = make_server(port)
    except Exception as e:
        _emit('claudectl failover: cannot bind port %d: %s' % (port, e))
        return 1
    _write_lock(port)
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW('claudectl failover :%d' % port)
    except Exception:
        pass
    cands = ', '.join([m for m in (s.get('failover_models') or []) if str(m).strip()]) or '(none)'
    _emit('')
    _emit('claudectl failover  :%d -> %s' % (port, s.get('provider_base_url') or '?'))
    _emit('candidates: %s' % cands)
    _emit('%-8s  %-28s %-22s %7s  %s' % ('time', 'request', 'model', 'took', 'result'))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _clear_lock()
    return 0


# ── proxy ────────────────────────────────────────────────────

def make_server(port=0):
    """Bind 127.0.0.1:<port> (0 = ephemeral, used by tests)."""
    return ThreadingHTTPServer(('127.0.0.1', port), _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    # ── plumbing ──

    def _guard(self):
        """This proxy forwards on the USER'S upstream key, so an unauthenticated
        request here spends their quota. The port is fixed and published in the
        source, so "it's loopback" excludes nobody: any page in any open tab can
        POST /v1/messages with Content-Type: text/plain — a CORS-simple request,
        no preflight to block it — and any other local process can too.

        Three checks, cheapest first:
          Host      — a DNS-rebound request carries the attacker's hostname, so
                      an allowlist here is the only real rebinding defense.
          Sec-Fetch/Origin — every browser sends at least one of these on every
                      fetch; Claude Code's HTTP client sends none. One check
                      kills the whole browser-origin class, key or no key.
          bearer    — when a key is configured, require it. config.provider_env
                      already hands it to the session as ANTHROPIC_AUTH_TOKEN,
                      which Claude Code sends as `Authorization: Bearer`.
        """
        host = (self.headers.get('Host') or '').strip().lower()
        port = self.server.server_address[1]
        if host not in ('127.0.0.1:%d' % port, 'localhost:%d' % port,
                        '[::1]:%d' % port):
            return self._deny('bad host')
        for h in ('Origin', 'Sec-Fetch-Site', 'Sec-Fetch-Mode', 'Referer'):
            if self.headers.get(h):
                return self._deny('browser-originated request')
        key = (self._settings().get('provider_api_key') or '').strip()
        if key and self.path != _MARKER_PATH:
            got = (self.headers.get('x-api-key')
                   or (self.headers.get('Authorization') or '').removeprefix('Bearer ')
                   or '').strip()
            if not hmac.compare_digest(got, key):
                return self._deny('bad credentials')
        return True

    def _deny(self, why):
        self._json(403, {'type': 'error', 'error': {
            'type': 'permission_error',
            'message': 'claudectl failover: refused (%s)' % why}})
        return False

    def _settings(self):
        return _c.load_settings()

    def _upstream(self, s):
        return (s.get('provider_base_url') or '').rstrip('/')

    def _fwd_headers(self, length, s):
        h = {k: v for k, v in self.headers.items()
             if k.lower() not in _HOP_BY_HOP}
        h['Content-Length'] = str(length)
        key = s.get('provider_api_key') or ''
        if key:
            h['Authorization'] = 'Bearer ' + key
            h['x-api-key'] = key
        return h

    def _open(self, s, body, timeout=_FIRST_BYTE_TIMEOUT):
        """Send upstream and read status+headers ONLY. The response body is left
        untouched — returning from here is the last point at which a retry is
        still possible."""
        u = urllib.parse.urlsplit(self._upstream(s))
        cls = (http.client.HTTPSConnection if u.scheme == 'https'
               else http.client.HTTPConnection)
        conn = cls(u.hostname, u.port, timeout=_CONNECT_TIMEOUT)
        conn.request(self.command, self.path, body=body,
                     headers=self._fwd_headers(len(body or b''), s))
        if conn.sock is not None:
            conn.sock.settimeout(timeout)
        return conn, conn.getresponse()

    def _json(self, code, payload):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _relay(self, resp, conn, extra):
        """Stream the committed response through verbatim. The ONLY place that
        writes a proxied body — once here, no retry is possible."""
        self.send_response(resp.status)
        streaming = resp.getheader('Content-Length') is None
        for k, v in resp.getheaders():
            if k.lower() in ('transfer-encoding', 'connection'):
                continue
            self.send_header(k, v)
        for k, v in extra.items():
            self.send_header(k, v)
        if streaming:
            # ponytail: close-delimited SSE relay rather than re-emitting chunked
            # framing. Costs one handshake per turn, and Claude Code already opens
            # a fresh request per turn. Upgrade path: emit real chunked framing.
            self.close_connection = True
        self.end_headers()
        if conn.sock is not None:
            conn.sock.settimeout(None)      # long turns; no retry left to protect
        try:
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass                            # client left; long past the commit point
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _passthrough(self, body):
        s = self._settings()
        t0 = time.monotonic()
        try:
            conn, resp = self._open(s, body)
        except Exception as e:
            self._note('-', time.monotonic() - t0, 'UNREACHABLE %s' % e)
            self._json(502, {'type': 'error', 'error': {
                'type': 'api_error',
                'message': 'claudectl failover: upstream unreachable: %s' % e}})
            return
        self._relay(resp, conn, {})

    def _learn(self, model, ok, detail):
        """Feed real outcomes back into the shared model cache — free, whereas
        probing for the same knowledge spends free-tier quota."""
        try:
            from . import omniroute
            omniroute.record_result(model, ok, detail)
        except Exception:
            pass                    # never let bookkeeping break a live turn

    def _note(self, model, secs, result):
        _emit('%-8s  %-28s %-22s %6.1fs  %s' % (
            time.strftime('%H:%M:%S'),
            '%s %s' % (self.command, self.path[:22]),
            str(model)[:22], secs, result))

    # ── verbs ──

    def do_GET(self):
        if not self._guard():
            return
        if self.path == _MARKER_PATH:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(_MARKER)))
            self.end_headers()
            self.wfile.write(_MARKER)
            return
        self._passthrough(None)

    def do_POST(self):
        if not self._guard():
            return
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        body = self.rfile.read(n) if n else b''
        if self.path.split('?')[0] in ('/v1/messages', '/v1/messages/count_tokens'):
            self._failover(body)
        else:
            self._passthrough(body)

    # ── the point of the whole module ──

    def _failover(self, body):
        s = self._settings()
        try:
            parsed = json.loads(body)
        except Exception:
            self._passthrough(body)
            return
        if not isinstance(parsed, dict) or 'model' not in parsed:
            self._passthrough(body)
            return

        cands = candidates(parsed['model'], s)
        if len(cands) <= 1:
            self._passthrough(body)
            return

        t0 = time.monotonic()
        tried = []
        for i, model in enumerate(cands):
            if i and time.monotonic() - t0 > _TOTAL_BUDGET:
                tried.append({'model': model, 'error': 'skipped: budget exhausted'})
                break
            parsed['model'] = model
            out = json.dumps(parsed).encode('utf-8')
            a0 = time.monotonic()
            last = (i == len(cands) - 1)
            try:
                conn, resp = self._open(s, out)
            except Exception as e:
                took = time.monotonic() - a0
                why = 'TIMEOUT' if isinstance(e, (TimeoutError, OSError)) and 'timed out' in str(e).lower() else str(e)[:60]
                self._note(model, took, '%s -> next' % (why or 'ERROR'))
                tried.append({'model': model, 'error': why})
                continue
            if resp.status >= 400 and not last:
                detail = b''
                try:
                    detail = resp.read(4096)     # error bodies are small JSON, never SSE
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                took = time.monotonic() - a0
                msg = _brief(detail)
                self._note(model, took, 'HTTP %s %s -> next' % (resp.status, msg))
                tried.append({'model': model, 'status': resp.status, 'error': msg})
                _c.log.warning('failover: %s -> HTTP %s (%s), next candidate',
                               model, resp.status, msg)
                self._learn(model, False, 'HTTP %s: %s' % (resp.status, msg))
                continue
            self._note(model, time.monotonic() - a0,
                       '%s  served' % resp.status if resp.status < 400
                       else 'HTTP %s (last candidate)' % resp.status)
            self._learn(model, resp.status < 400, 'HTTP %s' % resp.status)
            self._relay(resp, conn, {'X-Claudectl-Model': model,
                                     'X-Claudectl-Attempts': str(i + 1)})
            return

        self._note('-', time.monotonic() - t0, 'ALL %d CANDIDATES FAILED' % len(tried))
        # 502 + a distinguishable error type so callers can tell "proxy gave up"
        # apart from a per-model upstream error, and so the client always
        # receives a response on exhaustion — a silent close is how the `claude`
        # CLI ends up hanging forever.
        self._json(502, {'type': 'error', 'error': {
            'type': 'failover_exhausted',
            'message': 'claudectl failover: every candidate model failed',
            'attempts': tried}})


def _brief(raw):
    try:
        d = json.loads(raw)
        m = (d.get('error') or {}).get('message') or d.get('message') or ''
        if m:
            return str(m)[:70]
    except Exception:
        pass
    return raw.decode('utf-8', 'replace').strip()[:70]
