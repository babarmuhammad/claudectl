"""Insulate the suite from the environment it happens to be run in.

`config.get_config_dir()` resolves `CLAUDE_CONFIG_DIR` env > setting > default,
because claudectl sets that variable when it launches a session under a named
account and everything running inside that session has to agree about which
account it is. The consequence is that running pytest from INSIDE a Claude Code
session inherits the account of whoever is running it — which is exactly how
this file came to exist: the suite was green from a plain shell and red from a
session on a non-default account, in `test_get_config_dir_override`,
`test_get_config_dir_expands` and `test_all_config_dirs_merges_accounts_and_dedups`.

The pop is at MODULE level, not only in the fixture, and that ordering is
load-bearing — mutation-verified, and the split is exact. Removing it leaves
the two `test_config` tests passing, because they call `get_config_dir()` at
test time and the fixture's `delenv` has already run. It leaves
`test_all_config_dirs_merges_accounts_and_dedups` FAILING, because that one
compares against `config.config_dir` — an import-time constant, computed
before any fixture exists. pytest imports conftest before any test module, so
a module-level pop is the only thing that runs early enough to reach it.

The autouse fixture then covers the per-test case; `monkeypatch.setenv` inside a
test still wins, because fixtures run before the test body.
"""
import glob
import os
import urllib.parse
import tempfile

import pytest

#: Ambient Claude Code state that must never reach a test. Cleared at import so
#: the import-time constants in `config` are computed from a clean environment.
_AMBIENT = ('CLAUDE_CONFIG_DIR',)

for _var in _AMBIENT:
    os.environ.pop(_var, None)

#: claudectl's own real files, redirected into a throwaway directory for the
#: WHOLE session rather than per test. A monkeypatch is undone at teardown, and
#: several of these are written by background threads (the failover proxy, the
#: memory worker) that outlive the test that started them — which is exactly how
#: the real `claudectl.json` kept being rewritten after the per-test guard below
#: had already been lifted. Pinning the module attributes at import means a late
#: thread has nowhere real to write.
_TMP_STATE = tempfile.mkdtemp(prefix='claudectl-tests-')

from claude_sessions import config as _config      # noqa: E402
from claude_sessions import hooks as _hooks        # noqa: E402
from claude_sessions import stats as _stats        # noqa: E402

_config.settings_file = os.path.join(_TMP_STATE, 'claudectl.json')
_hooks.settings_path = os.path.join(_TMP_STATE, 'settings.json')
_stats.cache_file = os.path.join(_TMP_STATE, 'stats-cache.json')


@pytest.fixture(autouse=True)
def _no_ambient_claude_env(monkeypatch):
    for var in _AMBIENT:
        monkeypatch.delenv(var, raising=False)
    # No test may raise a desktop notification. Same choke-point discipline as
    # `_no_real_editor` below: the job runner notifies from its `finally`, which
    # every job in the suite reaches, so blocking the SPAWN is the only place
    # that cannot be forgotten by a new caller.
    monkeypatch.setenv('CLAUDECTL_NO_NOTIFY', '1')


@pytest.fixture(autouse=True)
def _no_real_editor(monkeypatch, request):
    """No test may open a real editor window.

    The Sandbox already stubbed `config.open_in_editor` and three modules that
    had imported it by value — but `agents`, `skills`, `mcp` and `hooks` had
    imported it by value too, so those screens spawned a real Notepad++ that
    took the foreground in the middle of a test run. Enumerating the callers is
    what failed; this blocks the spawn itself, so a new caller (or a test that
    never builds a Sandbox) cannot reintroduce it.

    A test that wants to exercise the real launcher asks for it with
    @pytest.mark.real_editor.
    """
    if request.node.get_closest_marker('real_editor'):
        return
    from claude_sessions import config
    opened = []
    monkeypatch.setattr(config, '_spawn_editor',
                        lambda exe, path: (opened.append((exe, path)), True)[1])
    return opened


def pytest_configure(config):
    config.addinivalue_line(
        'markers', 'real_editor: allow this test to spawn a real editor process')


# ── nothing the USER owns may be written by a test ───────────
# This is not hypothetical: a run of this suite replaced the real
# `~/.claude/settings.json` statusLine with the literal `'x'` a test uses as a
# stand-in, so Claude Code drew no statusline until it was repaired by hand.
# Enumerating the tests that could reach a real path is the approach that
# failed (the same lesson as `_no_real_editor` above), so both halves below
# guard the CHOKE POINT instead.

def _real_user_files():
    """Files a test must never modify: every account's settings.json and
    claudectl's real `claudectl.json` — the files claudectl itself WRITES, so a
    change to one during a test is a leak worth restoring.

    Claude Code's own `.claude.json` is deliberately NOT here. claudectl only
    ever reads it (`clientstate` is read-only by design), so a change to it
    during a test is by definition another program's — and this fixture's
    remedy is to overwrite the file with its pre-test bytes, which would roll
    back the live session that legitimately wrote it. Running the suite inside
    an active Claude Code session hit that twice. `test_nothing_writes_claude_code_state`
    below is the stronger and concurrency-proof replacement: it proves the
    absence of a writer instead of watching for one.
    """
    home = os.path.expanduser('~')
    out = []
    for d in glob.glob(os.path.join(home, '.claude*')):
        if os.path.isdir(d):
            out.append(os.path.join(d, 'settings.json'))
    try:
        from claude_sessions import config
        out.append(config.settings_file)
    except Exception:
        pass
    return [os.path.abspath(p) for p in out]


def _snapshot(paths):
    snap = {}
    for p in paths:
        try:
            with open(p, 'rb') as f:
                snap[p] = f.read()
        except OSError:
            snap[p] = None
    return snap


@pytest.fixture(autouse=True)
def _no_writes_outside_the_sandbox(monkeypatch, tmp_path_factory):
    """Layer 1 — every settings writer routes through `config.write_atomic`
    (`test_no_settings_writer_bypasses_the_atomic_helper` enforces that), so one
    wrapper rejects a write to any path outside the run's temp area. The whole
    TEMP tree is allowed, not just this test's tmp_path: a test may legitimately
    use `tempfile.mkdtemp()`, and nothing there belongs to the user.

    Layer 2 — the real files are snapshotted and RESTORED if a write slipped
    past layer 1 (a plain `open(path,'w')`, a subprocess), so a leak fails the
    test that caused it instead of surviving the run.
    """
    from claude_sessions import config
    real_write = config.write_atomic
    allowed = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
    basetemp = os.path.normcase(os.path.realpath(str(tmp_path_factory.getbasetemp())))

    def guarded(path, text, **kw):
        p = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        if not (p.startswith(allowed) or p.startswith(basetemp)):
            raise AssertionError(
                'test tried to write a real file outside the pytest temp area: %s' % path)
        return real_write(path, text, **kw)

    monkeypatch.setattr(config, 'write_atomic', guarded)

    paths = _real_user_files()
    before = _snapshot(paths)
    yield
    after = _snapshot(paths)
    damaged = [p for p in paths if after.get(p) != before.get(p)]
    for p in damaged:
        if before.get(p) is not None:
            with open(p, 'wb') as f:
                f.write(before[p])
        elif after.get(p) is not None:
            os.unlink(p)
    assert not damaged, 'test modified real user files (restored): %s' % damaged


@pytest.fixture(autouse=True)
def _no_process_global_cache_leaks_between_tests():
    """Two caches live for the life of the process on purpose, and both would
    otherwise make a test's result depend on which test ran before it.

    `mcp._status_cache` holds `claude mcp list` for 30s (that subprocess is 1.7s
    and /api/dashboard polls every 10s). `paths._path_cache` holds resolved —
    and, since it also caches misses, UNRESOLVED — project folders. Autouse
    rather than in `Sandbox`, because the tests that hit these are exactly the
    ones that never build one.
    """
    from claude_sessions import mcp, paths
    mcp._status_cache.clear()
    paths._path_cache.clear()
    yield
    mcp._status_cache.clear()
    paths._path_cache.clear()


@pytest.fixture(autouse=True)
def _stats_cache_is_never_the_real_one(monkeypatch, tmp_path_factory):
    """`stats.cache_file` is an import-time path inside the real account dir.
    A test that does not build a Sandbox inherited it, so three dashboard tests
    were reading the user's own session cache and writing it back pruned."""
    from claude_sessions import stats
    monkeypatch.setattr(stats, 'cache_file',
                        str(tmp_path_factory.mktemp('stats') / 'stats-cache.json'))
    stats._disk_cache = None
    stats._cache_dirty = False


@pytest.fixture(autouse=True)
def _model_catalogue_is_never_the_real_one(monkeypatch, tmp_path_factory):
    """`models._cache_path()` is `<config_dir>/claudectl-models.json` — the
    account's REAL live catalogue, refreshed daily by a background thread.

    Same class of leak as `stats.cache_file` above, with a sharper edge: this
    one decides what `config.models()` answers, so a test comparing the roster
    against the bundled floor passes or fails depending on what Anthropic
    shipped and whether the poller had run yet.
    `test_model_card_rows_covers_roster_with_swe` went red the hour a new Fable
    landed in the cache and was green in CI the whole time, because a clean
    machine has no catalogue and falls back to the floor.

    A test that WANTS a catalogue redirects `_cache_path` itself
    (`tests/test_models.py`) — its monkeypatch runs after this fixture and wins.
    """
    from claude_sessions import models
    p = str(tmp_path_factory.mktemp('catalogue') / 'claudectl-models.json')
    monkeypatch.setattr(models, '_cache_path', lambda: p)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test reaches a REMOTE host; loopback is left alone.

    `versions.released()` fetches the npm registry, and the endpoint-floor test
    calls every route with junk parameters — so without this the suite would
    make a real request per run, be slow offline and go red on a plane. It
    cannot be a blanket block: half the GUI tests drive their own server over
    `urlopen` against 127.0.0.1. A test that wants a remote fetch patches
    `urlopen` itself, which wins because fixtures run first.
    """
    import urllib.request
    real = urllib.request.urlopen

    def guarded(url, *a, **kw):
        full = getattr(url, 'full_url', url)
        host = urllib.parse.urlsplit(str(full)).hostname or ''
        if host in ('127.0.0.1', 'localhost', '::1', '0.0.0.0'):
            return real(url, *a, **kw)
        raise OSError('the test suite does not reach the network: %s' % str(full)[:80])
    monkeypatch.setattr(urllib.request, 'urlopen', guarded)
