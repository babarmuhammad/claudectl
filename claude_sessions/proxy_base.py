"""Shared machinery for claudectl's local proxy daemons.

Two of them exist and they are deliberately separate programs: `failover.py`
relays bytes verbatim and only ever rewrites a request's `model`, while
`gateway.py` owns response framing because it translates between wire formats.
Merging them would put a re-serializer inside the module whose whole contract is
that it does not have one.

What they genuinely share is the boring half — a detached child process, a lock
file, a readiness handshake and a request guard — and that half is subtle enough
that a second hand-written copy would be a second set of the same bugs. So it
lives here once, parameterized by name.

The guard is the part worth reading. Both daemons forward the USER'S upstream
credential, so an unauthenticated request to either spends their quota, and both
listen on a fixed port that is published in this source file. "It's loopback"
excludes nobody: any page in any open tab can POST with `Content-Type:
text/plain` — a CORS-simple request, no preflight to block it — and any other
local process can too.
"""

import hmac
import json
import os
import time
import urllib.request

from . import config as _c

READY_TIMEOUT = 15


class Daemon:
    """One named background proxy: its lock file, port, readiness marker and
    spawn/stop lifecycle.

    *name* separates everything — two daemons must not share a lock file, and
    the readiness marker is per-name on purpose: a bare connectivity check would
    happily trust any process squatting the port, including the OTHER daemon.
    """

    def __init__(self, name, serve_flag, label=None):
        self.name = name
        self.serve_flag = serve_flag
        self.label = label or name
        self.marker_path = '/__claudectl_%s__/health' % name
        self.marker = ('claudectl-%s' % name).encode('ascii')

    # ── lock file ──

    def lock_path(self):
        return os.path.join(os.path.dirname(_c.settings_file), '%s.lock' % self.name)

    def read_lock(self):
        p = self.lock_path()
        if not os.path.isfile(p):
            return None
        try:
            with open(p, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def write_lock(self, port):
        p = self.lock_path()
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                json.dump({'pid': os.getpid(), 'port': int(port),
                           'started': time.time()}, f)
        except Exception:
            pass

    def clear_lock(self):
        try:
            os.remove(self.lock_path())
        except Exception:
            pass

    def claim_spawn(self, port):
        """True if THIS caller may spawn the daemon; False if someone else already is.

        O_EXCL is the whole point: clearing the lock and then spawning lets two
        callers arriving together (two GUI tabs, or the TUI and the GUI at once)
        both conclude "not running" and both spawn, and the loser's server then
        fails to bind. The claim is written before the spawn, not by the child
        after it starts."""
        p = self.lock_path()
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
                data = self.read_lock()
                fresh = data and time.time() - (data.get('started') or 0) < READY_TIMEOUT
                if fresh or (data and pid_alive(data.get('pid')) is True):
                    return False        # someone else is on it; go wait
                self.clear_lock()       # stale claim from a dead run — retry once
                continue
            except Exception:
                return True             # can't lock at all; better to spawn than not
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            return True
        return True

    # ── readiness ──

    def is_ready(self, port, timeout=2):
        try:
            with urllib.request.urlopen(
                    'http://127.0.0.1:%d%s' % (int(port), self.marker_path),
                    timeout=timeout) as r:
                return r.read(64).strip() == self.marker
        except Exception:
            return False

    def serves_marker(self, handler):
        """True when *handler* just answered the readiness probe. Called from the
        daemon's own do_GET before any guard, since the probe cannot carry a key."""
        if handler.path != self.marker_path:
            return False
        body = self.marker
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/plain')
        handler.send_header('Content-Length', str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return True

    # ── lifecycle ──

    def ensure(self, port, quiet=False, on_ready=None):
        """(ok, message). Never raises. Reuses a live daemon; evicts a stale lock
        and respawns. *on_ready* supplies the success message."""
        import subprocess
        import sys
        port = int(port)

        def _ok():
            return True, (on_ready() if on_ready else 'running')

        if self.is_ready(port):
            return _ok()

        data = self.read_lock()
        if data and pid_alive(data.get('pid')) is True and int(data.get('port', 0)) == port:
            # Alive but not answering the marker yet — give it a moment.
            deadline = time.time() + 5
            while time.time() < deadline:
                if self.is_ready(port):
                    return _ok()
                time.sleep(0.3)

        if not self.claim_spawn(port):
            deadline = time.time() + READY_TIMEOUT
            while time.time() < deadline:
                if self.is_ready(port):
                    return _ok()
                time.sleep(0.3)
            return False, '%s did not become ready on port %d' % (self.label, port)

        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        env['PYTHONPATH'] = pkg_parent + (
            os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        cmd = [sys.executable, '-m', 'claude_sessions', self.serve_flag, str(port)]
        try:
            if quiet:
                subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, env=env, cwd=pkg_parent,
                    creationflags=(getattr(subprocess, 'DETACHED_PROCESS', 0)
                                   | getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
            else:
                # Pass NO std handles: specifying even one sets
                # STARTF_USESTDHANDLES, which makes the child inherit the
                # PARENT's stdout/stderr and defeats CREATE_NEW_CONSOLE — the log
                # would then be written into the TUI's alternate screen buffer
                # and corrupt it.
                subprocess.Popen(
                    cmd, env=env, cwd=pkg_parent,
                    creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        except Exception as e:
            _c.log.exception('%s: spawn failed', self.name)
            self.clear_lock()       # release the claim, or nothing may ever retry
            return False, 'could not start %s: %s' % (self.label, e)

        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline:
            if self.is_ready(port):
                return _ok()
            time.sleep(0.4)
        return False, ('%s did not come up on port %d — port may be in use by '
                       'another process' % (self.label, port))

    def stop(self):
        """(ok, message). Terminates the daemon named in the lock file."""
        data = self.read_lock()
        self.clear_lock()
        pid = (data or {}).get('pid')
        if not pid:
            return True, 'no %s recorded' % self.label
        if pid_alive(pid) is False:
            return True, '%s already gone' % self.label
        if os.name == 'nt':
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                h = k32.OpenProcess(0x0001, False, int(pid))   # PROCESS_TERMINATE
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


def pid_alive(pid):
    """True/False, or None when undeterminable (caller decides)."""
    from . import proc
    return proc.pid_alive(pid)


def guard(handler, key, marker_path, label):
    """True to proceed; otherwise a 403 has already been written.

    Three checks, cheapest first:

      Host    — a DNS-rebound request carries the attacker's hostname, so an
                allowlist here is the only real rebinding defense.
      browser — every browser sends at least one of Origin/Referer/Sec-Fetch-*
                on every fetch; Claude Code's HTTP client sends none. One check
                kills the whole browser-origin class, key or no key.
      bearer  — when a key is configured, require it. config.provider_env hands
                it to the session as ANTHROPIC_AUTH_TOKEN, which Claude Code
                sends as `Authorization: Bearer`.
    """
    host = (handler.headers.get('Host') or '').strip().lower()
    port = handler.server.server_address[1]
    if host not in ('127.0.0.1:%d' % port, 'localhost:%d' % port,
                    '[::1]:%d' % port):
        return deny(handler, label, 'bad host')
    for h in ('Origin', 'Sec-Fetch-Site', 'Sec-Fetch-Mode', 'Referer'):
        if handler.headers.get(h):
            return deny(handler, label, 'browser-originated request')
    key = (key or '').strip()
    if key and handler.path != marker_path:
        got = (handler.headers.get('x-api-key')
               or (handler.headers.get('Authorization') or '').removeprefix('Bearer ')
               or '').strip()
        if not hmac.compare_digest(got, key):
            return deny(handler, label, 'bad credentials')
    return True


def deny(handler, label, why):
    write_json(handler, 403, {'type': 'error', 'error': {
        'type': 'permission_error',
        'message': 'claudectl %s: refused (%s)' % (label, why)}})
    # The guard runs before the request body is read, so this connection is
    # desynchronized for keep-alive — the unread body would be parsed as the
    # next request line. Close instead of leaving a socket that only fails later.
    handler.close_connection = True
    return False


def write_json(handler, code, obj):
    body = json.dumps(obj).encode('utf-8')
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except Exception:
        pass
