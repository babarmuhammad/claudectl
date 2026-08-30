"""Failover proxy tests.

Fake upstream is a real HTTP server on 127.0.0.1:0 (never a fixed port), and the
proxy under test is failover.make_server(0) — so nothing here binds
failover_port and no detached process is ever spawned. The spawn call itself is
one proven line copied from memory.spawn_background_worker and is deliberately
not exercised: a subprocess test would be flaky and would leak console windows.
"""

import http.client
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from claude_sessions import config as _c
from claude_sessions import failover


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch, tmp_path):
    """lock_path()/log_path() hang off settings_file — without this the suite
    writes into the user's real ~/.claude/failover.log and failover.lock."""
    monkeypatch.setattr(_c, 'settings_file', str(tmp_path / 'claudectl.json'))


# ── fake upstream ────────────────────────────────────────────

class _Upstream:
    """Serves canned replies keyed on the request body's `model`.

    plan[model] = (status, body_bytes, headers_dict) or the string 'stream' for a
    Content-Length-less (SSE-shaped) response.
    """

    def __init__(self, plan):
        self.plan = plan
        self.seen = []            # every (path, model) that arrived
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def _handle(self):
                n = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(n) if n else b''
                model = None
                try:
                    model = json.loads(raw).get('model')
                except Exception:
                    pass
                outer.seen.append((self.path, model))
                entry = outer.plan.get(model, (200, b'{"ok":true}', {}))
                if entry == 'stream':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    for part in (b'event: a\n', b'data: 1\n\n', b'data: 2\n\n'):
                        self.wfile.write(part)
                        self.wfile.flush()
                    self.close_connection = True
                    return
                status, payload, extra = entry
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                for k, v in extra.items():
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _handle
            do_POST = _handle

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self):
        return 'http://127.0.0.1:%d' % self.port

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class _Proxy:
    def __init__(self):
        self.srv = failover.make_server(0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def post(self, path, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            'http://127.0.0.1:%d%s' % (self.port, path), data=data,
            method='POST', headers={'Content-Type': 'application/json',
                                    'Authorization': 'Bearer k'})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def get(self, path):
        try:
            req = urllib.request.Request(
                'http://127.0.0.1:%d%s' % (self.port, path),
                headers={'Authorization': 'Bearer k'})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _wire(monkeypatch, upstream, models):
    monkeypatch.setattr(_c, 'load_settings', lambda: {
        'provider_base_url': upstream.url,
        'provider_api_key': 'k',
        'failover_models': models,
        'failover_port': 20129,
    })


# ── candidates() is pure ─────────────────────────────────────

def test_candidates_puts_primary_first_and_dedupes():
    s = {'failover_models': ['b', 'a', 'c', '', 'b']}
    assert failover.candidates('a', s) == ['a', 'b', 'c']


def test_candidates_with_no_fallbacks_is_just_primary():
    assert failover.candidates('a', {}) == ['a']


def test_enabled_ignores_blank_entries():
    assert failover.enabled({'failover_models': ['', '  ']}) is False
    assert failover.enabled({'failover_models': ['x']}) is True


# ── the retry boundary ───────────────────────────────────────

def test_retries_next_candidate_on_401(monkeypatch):
    up = _Upstream({
        'dead': (401, b'{"error":{"message":"Model dead is not supported"}}', {}),
        'live': (200, b'{"ok":"live"}', {}),
    })
    _wire(monkeypatch, up, ['live'])
    px = _Proxy()
    try:
        status, body, headers = px.post('/v1/messages', {'model': 'dead'})
        assert status == 200
        assert b'live' in body
        assert headers.get('X-Claudectl-Model') == 'live'
        assert headers.get('X-Claudectl-Attempts') == '2'
        assert [m for _p, m in up.seen] == ['dead', 'live']
    finally:
        px.close()
        up.close()


def test_rewrites_model_per_attempt(monkeypatch):
    up = _Upstream({'a': (400, b'{}', {}), 'b': (400, b'{}', {}),
                    'c': (200, b'{"ok":1}', {})})
    _wire(monkeypatch, up, ['b', 'c'])
    px = _Proxy()
    try:
        assert px.post('/v1/messages', {'model': 'a'})[0] == 200
        assert [m for _p, m in up.seen] == ['a', 'b', 'c']
    finally:
        px.close()
        up.close()


def test_last_candidate_error_is_relayed_not_retried(monkeypatch):
    up = _Upstream({'a': (400, b'{"error":{"message":"first"}}', {}),
                    'b': (429, b'{"error":{"message":"last"}}', {})})
    _wire(monkeypatch, up, ['b'])
    px = _Proxy()
    try:
        status, body, _h = px.post('/v1/messages', {'model': 'a'})
        assert status == 429
        assert b'last' in body
        assert len(up.seen) == 2
    finally:
        px.close()
        up.close()


def test_single_candidate_is_plain_passthrough(monkeypatch):
    up = _Upstream({'a': (401, b'{"error":{"message":"nope"}}', {})})
    _wire(monkeypatch, up, [])
    px = _Proxy()
    try:
        status, body, headers = px.post('/v1/messages', {'model': 'a'})
        assert status == 401
        assert b'nope' in body
        assert 'X-Claudectl-Model' not in headers
        assert len(up.seen) == 1
    finally:
        px.close()
        up.close()


def test_body_without_model_skips_failover(monkeypatch):
    up = _Upstream({None: (200, b'{"ok":1}', {})})
    _wire(monkeypatch, up, ['b', 'c'])
    px = _Proxy()
    try:
        assert px.post('/v1/messages', {'messages': []})[0] == 200
        assert len(up.seen) == 1
    finally:
        px.close()
        up.close()


def test_count_tokens_also_fails_over(monkeypatch):
    up = _Upstream({'a': (401, b'{}', {}), 'b': (200, b'{"input_tokens":3}', {})})
    _wire(monkeypatch, up, ['b'])
    px = _Proxy()
    try:
        status, _b, headers = px.post('/v1/messages/count_tokens', {'model': 'a'})
        assert status == 200
        assert headers.get('X-Claudectl-Attempts') == '2'
    finally:
        px.close()
        up.close()


def test_get_models_is_single_shot_passthrough(monkeypatch):
    up = _Upstream({None: (200, b'{"data":[]}', {})})
    _wire(monkeypatch, up, ['b', 'c'])
    px = _Proxy()
    try:
        assert px.get('/v1/models')[0] == 200
        assert len(up.seen) == 1          # never retried, it is a catalog listing
    finally:
        px.close()
        up.close()


# ── the streaming contract ───────────────────────────────────

def test_streaming_response_omits_content_length_and_arrives_intact(monkeypatch):
    up = _Upstream({'a': 'stream'})
    _wire(monkeypatch, up, ['b'])
    px = _Proxy()
    try:
        status, body, headers = px.post('/v1/messages', {'model': 'a'})
        assert status == 200
        assert 'Content-Length' not in headers
        assert headers.get('Content-Type') == 'text/event-stream'
        assert body == b'event: a\ndata: 1\n\ndata: 2\n\n'
    finally:
        px.close()
        up.close()


# ── total-budget exhaustion ──────────────────────────────────

def test_all_candidates_failing_returns_502_naming_them(monkeypatch):
    up = _Upstream({})
    up.close()                            # nothing listening -> every attempt errors
    _wire(monkeypatch, up, ['b', 'c'])
    px = _Proxy()
    try:
        status, body, _h = px.post('/v1/messages', {'model': 'a'})
        assert status == 502
        error = json.loads(body)['error']
        assert error['type'] == 'failover_exhausted'
        attempts = error['attempts']
        assert [a['model'] for a in attempts] == ['a', 'b', 'c']
    finally:
        px.close()


def test_all_candidates_fail_returns_502_not_a_hang(monkeypatch):
    """Step 10: exhaustion must answer with a definitive 502 (never hang). The
    client-side timeout is the assertion that matters — if the proxy ever blocks
    instead of answering, this test fails with a timeout rather than hanging the
    suite forever."""
    up = _Upstream({})
    up.close()                            # nothing listening -> every attempt errors
    _wire(monkeypatch, up, ['x', 'y', 'z'])
    px = _Proxy()
    try:
        req = urllib.request.Request(
            'http://127.0.0.1:%d/v1/messages' % px.port,
            data=json.dumps({'model': 'a'}).encode(), method='POST',
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer k'})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                status, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read()
        assert status == 502
        assert b'failover_exhausted' in body
    finally:
        px.close()


def test_upstream_down_on_passthrough_returns_502(monkeypatch):
    up = _Upstream({})
    up.close()
    _wire(monkeypatch, up, [])
    px = _Proxy()
    try:
        assert px.post('/v1/messages', {'model': 'a'})[0] == 502
    finally:
        px.close()


# ── readiness marker ─────────────────────────────────────────

def test_marker_path_served_locally_and_not_forwarded(monkeypatch):
    up = _Upstream({})
    _wire(monkeypatch, up, [])
    px = _Proxy()
    try:
        status, body, _h = px.get(failover._MARKER_PATH)
        assert status == 200
        assert body.strip() == failover._MARKER
        assert up.seen == []
        assert failover.is_ready(px.port) is True
    finally:
        px.close()
        up.close()


def test_is_ready_false_when_nothing_listening():
    up = _Upstream({})
    port = up.port
    up.close()
    assert failover.is_ready(port, timeout=1) is False


# ── lock file lifecycle ──────────────────────────────────────

def test_stale_lock_is_evicted(monkeypatch):
    os.makedirs(os.path.dirname(failover.lock_path()), exist_ok=True)
    with open(failover.lock_path(), 'w', encoding='utf-8') as f:
        json.dump({'pid': 999999999, 'port': 20129, 'started': 0}, f)
    monkeypatch.setattr(failover, '_pid_alive', lambda pid: False)
    ok, _msg = failover.stop_running()
    assert ok
    assert not os.path.isfile(failover.lock_path())


def test_write_and_read_lock_roundtrip():
    failover._write_lock(20129)
    data = failover._read_lock()
    assert data['port'] == 20129
    assert data['pid'] == os.getpid()
    failover._clear_lock()
    assert failover._read_lock() is None


def test_log_and_lock_live_beside_settings_file():
    root = os.path.dirname(_c.settings_file)
    assert os.path.dirname(failover.log_path()) == root
    assert os.path.dirname(failover.lock_path()) == root


# ── config wiring ────────────────────────────────────────────

def test_provider_env_repoints_base_url_when_candidates_configured():
    s = {'provider_exec_model': 'auto/coding',
         'provider_base_url': 'http://localhost:20128',
         'provider_api_key': 'k',
         'failover_models': ['x'], 'failover_port': 20129}
    assert _c.provider_env(s)['ANTHROPIC_BASE_URL'] == 'http://127.0.0.1:20129'


def test_provider_env_leaves_base_url_alone_without_candidates():
    s = {'provider_exec_model': 'auto/coding',
         'provider_base_url': 'http://localhost:20128',
         'provider_api_key': 'k', 'failover_models': []}
    assert _c.provider_env(s)['ANTHROPIC_BASE_URL'] == 'http://localhost:20128'


def test_council_calls_bypass_the_proxy(monkeypatch):
    """The exec session goes through the proxy; council voices must not — they
    run before the proxy is started, so proxying them would silently disable the
    council whenever failover is configured."""
    from claude_sessions import plan_execute

    real = 'http://localhost:20128'
    s = {'provider_exec_model': 'auto/coding', 'provider_base_url': real,
         'provider_api_key': 'k', 'failover_models': ['x'], 'failover_port': 20129}
    monkeypatch.setattr(_c, 'load_settings', lambda: s)
    omni = _c.provider_env(s)
    assert omni['ANTHROPIC_BASE_URL'] == 'http://127.0.0.1:20129'

    seen = {}

    def fake_run(args, input_text=None, cwd=None, env=None, **kw):
        seen['url'] = (env or {}).get('ANTHROPIC_BASE_URL')
        return 'ok'

    monkeypatch.setattr(_c, 'get_claude_exe', lambda: 'claude.exe')
    import claude_sessions.gui_api as ga
    monkeypatch.setattr(ga, '_run_cancellable', fake_run)

    plan_execute._headless('m', 'p', os.getcwd(), omni_env=omni, cfgdir='')
    assert seen['url'] == real


# ── the proxy forwards on the user's key, so it must authenticate ──

def _raw(port, path='/v1/messages', method='POST', extra=None, body=b'{"model":"a"}',
         host=None):
    """One request with full control over the headers, incl. Host — urllib always
    sets Host to the connected address, which is exactly what we need to spoof."""
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    h = {'Content-Length': str(len(body)), 'Content-Type': 'application/json'}
    h['Host'] = host or ('127.0.0.1:%d' % port)
    h.update(extra or {})
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    out = (r.status, r.read())
    conn.close()
    return out


def test_browser_originated_request_is_refused(monkeypatch):
    """The whole CSRF class: a page in any open tab can POST here with a
    CORS-simple content type and no preflight to stop it. Every browser sends at
    least one fetch-metadata header; Claude Code's client sends none."""
    up = _Upstream({'a': (200, b'{"ok":1}', {})})
    _wire(monkeypatch, up, [])
    px = _Proxy()
    try:
        for hdr in ('Origin', 'Referer', 'Sec-Fetch-Site', 'Sec-Fetch-Mode'):
            status, body = _raw(px.port, extra={'Authorization': 'Bearer k',
                                               hdr: 'https://evil.example'})
            assert status == 403, hdr
            assert b'browser-originated' in body, hdr
    finally:
        px.close()
        up.close()


def test_foreign_host_header_is_refused(monkeypatch):
    """DNS rebinding: the attacker's origin resolves to 127.0.0.1, so the request
    is same-origin to THEM and may carry any header — but Host is still theirs."""
    up = _Upstream({'a': (200, b'{"ok":1}', {})})
    _wire(monkeypatch, up, [])
    px = _Proxy()
    try:
        status, body = _raw(px.port, host='evil.example:%d' % px.port,
                            extra={'Authorization': 'Bearer k'})
        assert status == 403 and b'bad host' in body
    finally:
        px.close()
        up.close()


def test_wrong_and_missing_credentials_are_refused(monkeypatch):
    up = _Upstream({'a': (200, b'{"ok":1}', {})})
    _wire(monkeypatch, up, [])
    px = _Proxy()
    try:
        assert _raw(px.port)[0] == 403                                   # none
        assert _raw(px.port, extra={'Authorization': 'Bearer nope'})[0] == 403
        assert _raw(px.port, extra={'x-api-key': 'nope'})[0] == 403
        assert _raw(px.port, extra={'Authorization': 'Bearer k'})[0] == 200
        assert _raw(px.port, extra={'x-api-key': 'k'})[0] == 200         # either spelling
    finally:
        px.close()
        up.close()


def test_readiness_marker_needs_no_key_but_still_needs_the_host(monkeypatch):
    """ensure_running() probes the marker before any key is in play, so it must
    stay reachable — but not to a rebound origin."""
    up = _Upstream({})
    _wire(monkeypatch, up, [])
    px = _Proxy()
    try:
        status, body = _raw(px.port, path=failover._MARKER_PATH, method='GET', body=b'')
        assert status == 200 and body == failover._MARKER
        assert _raw(px.port, path=failover._MARKER_PATH, method='GET', body=b'',
                    host='evil.example:%d' % px.port)[0] == 403
    finally:
        px.close()
        up.close()


def test_only_one_caller_may_spawn_the_daemon(monkeypatch, tmp_path):
    """Two callers arriving together (two GUI tabs, or TUI + GUI) both used to
    conclude "not running" and both spawn; the loser's bind then failed."""
    assert failover._claim_spawn(20129) is True
    assert failover._claim_spawn(20129) is False        # claim is held
    failover._clear_lock()
    assert failover._claim_spawn(20129) is True         # released


def test_a_stale_claim_from_a_dead_run_is_evicted(monkeypatch, tmp_path):
    import json as _json
    p = failover.lock_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        _json.dump({'pid': 999999, 'port': 20129,
                    'started': 0}, f)                  # ancient, pid long gone
    monkeypatch.setattr(failover, '_pid_alive', lambda pid: False)
    assert failover._claim_spawn(20129) is True


def test_a_failed_spawn_releases_the_claim(monkeypatch, tmp_path):
    """Otherwise the first crash wedges failover for the rest of the machine's
    uptime — nothing would ever retry."""
    import subprocess as _sp
    monkeypatch.setattr(failover, 'is_ready', lambda port, timeout=2: False)
    monkeypatch.setattr(_sp, 'Popen',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('boom')))
    ok, msg = failover.ensure_running({'failover_port': 20129,
                                       'failover_models': ['x'],
                                       'failover_quiet': True})
    assert not ok and 'could not start' in msg
    assert failover._read_lock() is None
