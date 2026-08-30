from claude_sessions import config as c
from claude_sessions import omniroute


def test_provider_env_disabled_by_default():
    assert c.provider_env({}) == {}
    assert c.provider_env({'provider_exec_model': ''}) == {}


def test_provider_env_returns_anthropic_override_when_configured():
    s = {'provider_exec_model': 'glm-4.6',
         'provider_base_url': 'http://localhost:20128',
         'provider_api_key': 'secret-token'}
    env = c.provider_env(s)
    assert env == {'ANTHROPIC_BASE_URL': 'http://localhost:20128',
                    'ANTHROPIC_AUTH_TOKEN': 'secret-token',
                    'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
                    'CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING': '1'}


def test_provider_env_leaves_tool_search_alone_unless_asked():
    """Claude Code disables MCP tool search on a non-first-party base URL and
    re-enabling it only works if the upstream forwards tool_reference blocks --
    so it is the user's assertion, never implied by configuring a provider."""
    s = {'provider_exec_model': 'glm-4.6', 'provider_base_url': 'http://x',
         'provider_api_key': 'k'}
    assert 'ENABLE_TOOL_SEARCH' not in c.provider_env(s)
    s['provider_tool_search'] = True
    assert c.provider_env(s)['ENABLE_TOOL_SEARCH'] == 'true'


def test_omniroute_client_degrades_quietly_when_unreachable():
    # no server on this port -- every call must fail closed, never raise
    dead = 'http://127.0.0.1:1'
    assert omniroute.is_reachable(dead, timeout=1) is False
    assert omniroute.list_models(dead) == []
    assert omniroute.catalog(dead) == []
    assert omniroute.health(dead) == {'providers': [], 'lockouts': [], 'summary': {}}
    assert omniroute.fetch_both(dead) == ([], {'providers': [], 'lockouts': [], 'summary': {}})


def test_ensure_running_reports_missing_binary(monkeypatch):
    import shutil
    dead = 'http://127.0.0.1:1'
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    ok, msg = omniroute.ensure_running(dead, timeout=1)
    assert ok is False and 'npm install' in msg


def test_ensure_running_skips_start_when_already_up(monkeypatch):
    monkeypatch.setattr(omniroute, 'is_reachable', lambda *a, **k: True)
    ok, msg = omniroute.ensure_running('http://localhost:20128')
    assert (ok, msg) == (True, 'already running')


def test_health_summary_carries_provider_counts(monkeypatch):
    # confirmed against a live instance: /v1/models lists a static catalog
    # regardless of connected providers -- providerSummary is the real signal
    fake_health = {'providerSummary': {'catalogCount': 257, 'configuredCount': 1,
                                        'activeCount': 1}}
    monkeypatch.setattr(omniroute, '_get', lambda base, path, key, timeout=5: fake_health)
    assert omniroute.health('http://localhost:20128')['summary'] == \
        {'catalogCount': 257, 'configuredCount': 1, 'activeCount': 1}


def test_cli_connections_missing_binary_returns_empty(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    assert omniroute.cli_connections() == []


def test_cli_connections_parses_providers_list_json(monkeypatch):
    # shape confirmed against a live `omniroute providers list --json`
    fake = {'providers': [{'id': 'abc123', 'provider': 'opencode', 'name': 'OpenCode Account 1',
                           'testStatus': 'error',
                           'lastError': 'Connection OpenCode Account 1 has no API key configured.'}]}
    monkeypatch.setattr(omniroute, '_cli', lambda args, timeout=15: fake)
    conns = omniroute.cli_connections()
    assert conns == [{'id': 'abc123', 'provider': 'opencode', 'name': 'OpenCode Account 1',
                      'status': 'error',
                      'error': 'Connection OpenCode Account 1 has no API key configured.'}]


def test_cli_test_connection_reports_unavailable_cli(monkeypatch):
    monkeypatch.setattr(omniroute, '_cli', lambda args, timeout=15: None)
    ok, msg = omniroute.cli_test_connection('opencode')
    assert ok is False and 'not available' in msg


def test_test_live_unreachable_fails_closed():
    ok, used, msg = omniroute.test_live('http://127.0.0.1:1')
    assert ok is False and used == ''


def test_test_live_reports_routed_model_on_success(monkeypatch):
    # shape confirmed against a live server: auto/coding -> big-pickle via
    # OpenCode, surfaced in the x-omniroute-model/-provider response headers
    class FakeResp:
        headers = {'x-omniroute-model': 'big-pickle', 'x-omniroute-provider': 'oc'}
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(omniroute.urllib.request, 'urlopen', lambda req, timeout=30: FakeResp())
    ok, used, msg = omniroute.test_live('http://localhost:20128', 'auto/coding')
    assert ok is True and used == 'big-pickle' and 'oc' in msg


def _fake_http(monkeypatch, payloads):
    """Serve canned JSON per URL path suffix (shapes taken from a live v3.8.48)."""
    import io
    import json

    def _open(req, timeout=30):
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        for suffix, payload in payloads.items():
            if url.endswith(suffix):
                return io.BytesIO(json.dumps(payload).encode())
        raise AssertionError('unexpected URL %s' % url)
    monkeypatch.setattr(omniroute.urllib.request, 'urlopen',
                        lambda req, timeout=30: _open(req, timeout))


_HEALTH = {
    'providerHealth': {
        'nvidia': {'state': 'CLOSED', 'failures': 3, 'lastFailure': 'x'},
        'mimocode': {'state': 'HALF_OPEN', 'failures': 44, 'lastFailure': 'y'},
        'deadprov': {'state': 'OPEN', 'failures': 99, 'lastFailure': 'z'},
    },
    'lockouts': [{'provider': 'nvidia', 'model': 'nvidia/locked-one',
                  'reason': 'quota_exhausted', 'remainingMs': 60000}],
    'providerSummary': {'catalogCount': 257, 'configuredCount': 3, 'activeCount': 2},
}

_CATALOG = {'data': [
    {'id': 'auto/coding', 'owned_by': 'combo', 'context_length': 1048576,
     'capabilities': {'tool_calling': True}},
    {'id': 'nvidia/good', 'owned_by': 'nvidia', 'context_length': 128000,
     'name': 'Good', 'capabilities': {'tool_calling': True}},
    {'id': 'nvidia/locked-one', 'owned_by': 'nvidia', 'context_length': 128000,
     'capabilities': {'tool_calling': True}},
    {'id': 'nvidia/no-tools', 'owned_by': 'nvidia', 'context_length': 400000,
     'capabilities': {'tool_calling': False}},
    {'id': 'nvidia/flux', 'owned_by': 'nvidia',
     'capabilities': {'tool_calling': True}},          # image gen: no context window
    {'id': 'mcode/recovering', 'owned_by': 'mimocode', 'context_length': 64000,
     'capabilities': {'tool_calling': True}},
    {'id': 'dead/x', 'owned_by': 'deadprov', 'context_length': 64000,
     'capabilities': {'tool_calling': True}},
    {'id': 'oc/minimax-m3-free', 'owned_by': 'opencode', 'context_length': 1048576,
     'capabilities': {'tool_calling': True}},          # provider never configured
]}


def test_health_parses_providers_and_lockouts(monkeypatch):
    _fake_http(monkeypatch, {'/api/monitoring/health': _HEALTH})
    h = omniroute.health('http://localhost:20128')
    assert [p['name'] for p in h['providers']][0] == 'nvidia'   # CLOSED sorts first
    assert {p['name']: p['state'] for p in h['providers']}['deadprov'] == 'OPEN'
    assert h['lockouts'][0]['model'] == 'nvidia/locked-one'
    assert h['summary']['configuredCount'] == 3


def test_health_fails_closed_when_unreachable():
    h = omniroute.health('http://127.0.0.1:1')
    assert h == {'providers': [], 'lockouts': [], 'summary': {}}


def test_usable_models_excludes_only_the_provable(monkeypatch):
    _fake_http(monkeypatch, {'/api/monitoring/health': _HEALTH,
                             '/v1/models': _CATALOG})
    usable, autos, excluded = omniroute.usable_models('http://localhost:20128')
    ids = [u['id'] for u in usable]
    assert 'nvidia/good' in ids
    assert 'mcode/recovering' in ids            # HALF_OPEN is recovering, still a candidate
    assert autos == ['auto/coding']
    assert 'dead/x' not in ids                  # circuit OPEN
    assert 'nvidia/locked-one' not in ids       # quota lockout
    assert 'nvidia/flux' not in ids             # no context window: not a chat model
    assert 'nvidia/no-tools' not in ids         # claude cannot run without tools
    flat = {m for v in excluded.values() for m in v}
    assert flat == {'dead/x', 'nvidia/locked-one', 'nvidia/flux', 'nvidia/no-tools'}


def test_usable_models_does_not_treat_unseen_provider_as_unconfigured(monkeypatch):
    """providerHealth lists providers OmniRoute has ATTEMPTED, not ones that are
    configured — confirmed live: it grew 5 -> 9 purely from probing while
    configuredCount stayed 5. So a provider missing from it must not be inferred
    to be unconfigured; probe_models() is what settles whether a model works."""
    _fake_http(monkeypatch, {'/api/monitoring/health': _HEALTH,
                             '/v1/models': _CATALOG})
    usable, _autos, excluded = omniroute.usable_models('http://localhost:20128')
    ids = [u['id'] for u in usable]
    assert 'oc/minimax-m3-free' in ids          # opencode absent from providerHealth
    assert not any('not configured' in k for k in excluded)


def test_probe_models_reports_per_model_reality(monkeypatch):
    calls = []

    def fake_live(base, model=None, api_key='', timeout=30):
        calls.append(model)
        if model == 'good':
            return True, 'served-by-x', 'routed to served-by-x'
        return False, '', 'HTTP 401: Model %s is not supported' % model
    monkeypatch.setattr(omniroute, 'test_live', fake_live)
    res = omniroute.probe_models('http://localhost:20128', ['good', 'bad', 'good'])
    assert [r['id'] for r in res] == ['good', 'bad']        # deduped, order kept
    assert res[0]['ok'] is True and res[0]['served'] == 'served-by-x'
    assert res[1]['ok'] is False and 'not supported' in res[1]['detail']
    assert res[1]['status'] == 'auth'
    assert sorted(calls) == ['bad', 'good']


def test_classify_failure_separates_timeout_from_verdicts():
    assert omniroute.classify_failure('timed out') == 'timeout'
    assert omniroute.classify_failure('HTTP 410: gone') == 'gone'
    assert omniroute.classify_failure('HTTP 403: Key limit exceeded') == 'limited'
    assert omniroute.classify_failure('HTTP 403: permission_error') == 'auth'
    assert omniroute.classify_failure('HTTP 500: boom') == 'error'


def test_probe_retries_a_timeout_once_with_a_bigger_budget(monkeypatch):
    """A timeout is our budget expiring, not a model fault — several probes queue
    inside one OmniRoute. Without the retry the same model reads 'failed' in one
    run and 'works' in the next."""
    seen = []

    def fake_live(base, model=None, api_key='', timeout=30):
        seen.append((model, timeout))
        if len([s for s in seen if s[0] == model]) == 1:
            return False, '', 'timed out'
        return True, model, 'routed to ' + model
    monkeypatch.setattr(omniroute, 'test_live', fake_live)
    res = omniroute.probe_models('http://localhost:20128', ['slow'], timeout=10)
    assert res[0]['ok'] is True and res[0]['status'] == 'works'
    assert 'on retry' in res[0]['detail']
    assert [t for _m, t in seen] == [10, 20]       # doubled budget on the retry


def test_probe_stops_once_enough_models_answer(monkeypatch):
    seen = []

    def fake_live(base, model=None, api_key='', timeout=30):
        seen.append(model)
        return True, model, 'ok'
    monkeypatch.setattr(omniroute, 'test_live', fake_live)
    ids = ['m%d' % i for i in range(12)]
    res = omniroute.probe_models('http://x', ids, want=2, workers=1, skip={})
    assert len(seen) == 2                       # stopped after two answered
    assert [r['status'] for r in res[:2]] == ['works', 'works']
    assert res[2]['status'] == 'skipped'
    assert [r['id'] for r in res] == ids        # every id still reported, in order


def test_probe_honours_total_budget(monkeypatch):
    import time as _t
    monkeypatch.setattr(omniroute, 'test_live',
                        lambda b, model=None, api_key='', timeout=30: (
                            _t.sleep(0.05), (False, '', 'HTTP 500: x'))[1])
    ids = ['m%d' % i for i in range(40)]
    res = omniroute.probe_models('http://x', ids, want=0, budget=0.06,
                                 workers=1, skip={})
    assert any(r['status'] == 'skipped' for r in res)
    assert 'time budget' in [r['detail'] for r in res if r['status'] == 'skipped'][0]


def test_probe_skips_known_dead_without_a_request(monkeypatch):
    seen = []

    def fake_live(base, model=None, api_key='', timeout=30):
        seen.append(model)
        return True, model, 'ok'
    monkeypatch.setattr(omniroute, 'test_live', fake_live)
    res = omniroute.probe_models('http://x', ['dead', 'live'], want=0,
                                 skip={'dead': 'gone'})
    assert seen == ['live']                     # never asked about 'dead'
    by = {r['id']: r for r in res}
    assert by['dead']['status'] == 'gone' and 'not re-probed' in by['dead']['detail']
    assert by['live']['ok'] is True


def test_dead_cache_roundtrip_and_recovery(monkeypatch, tmp_path):
    from claude_sessions import config as _cfg
    monkeypatch.setattr(_cfg, 'settings_file', str(tmp_path / 'claudectl.json'))
    omniroute.save_dead([{'id': 'a', 'status': 'gone', 'ok': False},
                         {'id': 'b', 'status': 'auth', 'ok': False},
                         {'id': 'c', 'status': 'timeout', 'ok': False}])
    d = omniroute.load_dead()
    assert d == {'a': 'gone', 'b': 'auth'}      # timeouts are never cached
    # a model that comes back stops being skipped
    omniroute.save_dead([{'id': 'a', 'status': 'works', 'ok': True}])
    assert omniroute.load_dead() == {'b': 'auth'}


def test_dead_cache_expires(monkeypatch, tmp_path):
    import json as _j
    import time as _t
    from claude_sessions import config as _cfg
    monkeypatch.setattr(_cfg, 'settings_file', str(tmp_path / 'claudectl.json'))
    with open(omniroute.dead_path(), 'w', encoding='utf-8') as f:
        _j.dump({'old': {'status': 'gone', 'ts': _t.time() - omniroute._DEAD_TTL - 10},
                 'new': {'status': 'gone', 'ts': _t.time()}}, f)
    assert omniroute.load_dead() == {'new': 'gone'}


def test_order_fairly_round_robins_providers():
    """Catalog order is alphabetical by provider, so one broken provider eats the
    whole probe budget and later providers are never reached — measured: 77s spent
    on gemini/groq/nvidia/oc left all 12 working openrouter models unprobed."""
    ids = ['nvidia/a', 'nvidia/b', 'nvidia/c', 'openrouter/x', 'openrouter/y', 'gemini/g']
    out = omniroute.order_fairly(ids)
    assert {o.split('/')[0] for o in out[:3]} == {'nvidia', 'openrouter', 'gemini'}
    assert sorted(out) == sorted(ids)          # nothing lost or duplicated


def test_order_fairly_puts_known_good_first():
    ids = ['nvidia/a', 'openrouter/x', 'gemini/g']
    assert omniroute.order_fairly(ids, alive=['openrouter/x'])[0] == 'openrouter/x'


def test_record_result_learns_from_a_real_turn(monkeypatch, tmp_path):
    from claude_sessions import config as _cfg
    monkeypatch.setattr(_cfg, 'settings_file', str(tmp_path / 'claudectl.json'))
    omniroute.record_result('m1', True)
    assert omniroute.load_alive() == ['m1']
    omniroute.record_result('m2', False, 'HTTP 410: gone')
    assert omniroute.load_dead() == {'m2': 'gone'}
    # a timeout is not a verdict and must not be recorded either way
    omniroute.record_result('m3', False, 'timed out')
    assert 'm3' not in omniroute.load_dead() and 'm3' not in omniroute.load_alive()


def test_record_result_is_a_noop_when_status_unchanged(monkeypatch, tmp_path):
    from claude_sessions import config as _cfg
    monkeypatch.setattr(_cfg, 'settings_file', str(tmp_path / 'claudectl.json'))
    omniroute.record_result('m1', True)
    calls = []
    monkeypatch.setattr(omniroute, 'save_dead', lambda *a, **k: calls.append(a))
    omniroute.record_result('m1', True)        # same status -> no rewrite per turn
    assert calls == []


def test_probe_does_not_retry_a_definitive_failure(monkeypatch):
    seen = []

    def fake_live(base, model=None, api_key='', timeout=30):
        seen.append(model)
        return False, '', 'HTTP 410: gone'
    monkeypatch.setattr(omniroute, 'test_live', fake_live)
    res = omniroute.probe_models('http://localhost:20128', ['dead'], timeout=10)
    assert res[0]['status'] == 'gone'
    assert seen == ['dead']                        # 410 is permanent, no retry


def test_probe_models_empty_is_noop(monkeypatch):
    monkeypatch.setattr(omniroute, 'test_live',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('called')))
    assert omniroute.probe_models('http://localhost:20128', []) == []


def test_usable_models_healthy_provider_sorts_before_recovering(monkeypatch):
    _fake_http(monkeypatch, {'/api/monitoring/health': _HEALTH,
                             '/v1/models': _CATALOG})
    usable, _a, _e = omniroute.usable_models('http://localhost:20128')
    assert usable[0]['state'] == 'CLOSED'


def test_usable_models_fails_closed_when_unreachable():
    usable, autos, excluded = omniroute.usable_models('http://127.0.0.1:1')
    assert usable == [] and autos == [] and excluded == {}


def test_cli_strips_ansi_log_preamble_before_json(monkeypatch):
    # regression: the CLI prefixes --json output with ANSI-colored log lines,
    # e.g. '\x1b[2m...Loaded env...\x1b[0m\n{...}'. The ANSI escape itself
    # contains a literal '[' that sorts BEFORE the real '{' -- treating '['
    # as a candidate start marker (as an earlier version of this function
    # did) corrupts the parse. Only '{' is a valid start here.
    raw = '  \x1b[2m\U0001f4cb Loaded env from x\x1b[0m\n{"providers": []}\n'

    class FakeCompleted:
        stdout = raw
        stderr = ''
        returncode = 0
    import shutil
    monkeypatch.setattr(shutil, 'which', lambda name: 'omniroute')
    monkeypatch.setattr(omniroute.subprocess, 'run', lambda *a, **k: FakeCompleted())
    assert omniroute._cli(['providers', 'list', '--json']) == {'providers': []}


if __name__ == '__main__':
    test_provider_env_disabled_by_default()
    test_provider_env_returns_anthropic_override_when_configured()
    test_omniroute_client_degrades_quietly_when_unreachable()
    print('ok')


def test_provider_env_with_model_param_bypasses_exec_model_gate():
    """Passing model='_' forces OmniRoute env even when provider_exec_model is
    not set — handles the GUI plan-execute modal's via='omniroute' path."""
    s = {'provider_base_url': 'http://localhost:20128',
         'provider_api_key': 'secret-token'}
    # without model param: empty because exec_model is not set
    assert c.provider_env(s) == {}
    # with model param: env vars are returned
    env = c.provider_env(s, model='_')
    assert env.get('ANTHROPIC_BASE_URL') == 'http://localhost:20128'
    assert env.get('ANTHROPIC_AUTH_TOKEN') == 'secret-token'


def test_provider_env_includes_disable_traffic_not_subagent_model():
    """omniroute_env sets CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC but no
    longer forces CLAUDE_CODE_SUBAGENT_MODEL — OmniRoute can't route a bare
    Anthropic model id, so agents must inherit the session's OmniRoute model."""
    s = {'provider_exec_model': 'auto/coding',
         'provider_base_url': 'http://localhost:20128',
         'provider_api_key': 'secret'}
    env = c.provider_env(s)
    assert 'CLAUDE_CODE_SUBAGENT_MODEL' not in env
    assert env['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'] == '1'


def test_provider_env_filters_empty_values():
    """Empty string values are filtered out so ambient env is never clobbered."""
    s = {'provider_exec_model': 'auto/coding',
         'provider_base_url': '',
         'provider_api_key': ''}
    env = c.provider_env(s)
    assert 'ANTHROPIC_BASE_URL' not in env
    assert 'ANTHROPIC_AUTH_TOKEN' not in env


def test_prepare_launch_unknown_model_raises_valueerror(monkeypatch, tmp_path):
    """prepare_launch() raises ValueError for a model not in list_models()."""
    monkeypatch.setattr(omniroute, 'ensure_running', lambda *a, **k: (True, 'running'))
    from claude_sessions import config
    from claude_sessions.config import load_settings, save_settings
    monkeypatch.setattr(config, 'settings_file', str(tmp_path / 'claudectl.json'))
    s = load_settings()
    s['provider_base_url'] = 'http://localhost:20128'
    s['provider_api_key'] = ''
    s['provider_exec_model'] = 'free/x'
    s['provider_kind'] = 'omniroute'
    save_settings(s)
    monkeypatch.setattr(omniroute, 'list_models',
                        lambda *a, **k: [('free/x', 'Free Model X'), ('free/y', 'Free Model Y')])
    import pytest
    # 'bogus' is not in the list -> ValueError
    with pytest.raises(ValueError, match='bogus'):
        omniroute.prepare_launch('bogus')
    # 'auto/coding' is always accepted without validation
    env, warn = omniroute.prepare_launch('auto/coding')
    assert isinstance(env, dict) and warn == ''


def test_prepare_launch_ensure_running_failure_propagates(monkeypatch):
    """prepare_launch raises RuntimeError when ensure_running fails."""
    monkeypatch.setattr(omniroute, 'ensure_running',
                        lambda *a, **k: (False, 'daemon dead'))
    import pytest
    with pytest.raises(RuntimeError, match='OmniRoute'):
        omniroute.prepare_launch('auto/coding', {'provider_kind': 'omniroute'})


def test_prepare_launch_never_autostarts_a_daemon_for_a_generic_provider(monkeypatch):
    """'generic' means the user already runs the server. Auto-starting the
    OmniRoute npm daemon for it would be starting the wrong program, and would
    mask an unreachable endpoint behind a spawn that appears to succeed."""
    called = []
    monkeypatch.setattr(omniroute, 'ensure_running',
                        lambda *a, **k: called.append(1) or (True, 'running'))
    monkeypatch.setattr(omniroute, 'is_reachable', lambda *a, **k: True)
    env, _warn = omniroute.prepare_launch('qwen3-coder', {
        'provider_kind': 'generic', 'provider_base_url': 'http://localhost:11434'})
    assert called == []
    assert env['ANTHROPIC_BASE_URL'] == 'http://localhost:11434'


def test_prepare_launch_does_not_validate_a_generic_model_id(monkeypatch):
    """There is no catalogue for a generic Anthropic-shaped server, so a model
    id is taken on trust. Validating against an empty list would reject every
    model the user could possibly name."""
    monkeypatch.setattr(omniroute, 'is_reachable', lambda *a, **k: True)
    monkeypatch.setattr(omniroute, 'list_models', lambda *a, **k: [])
    env, _warn = omniroute.prepare_launch('anything-at-all', {
        'provider_kind': 'generic', 'provider_base_url': 'http://h'})
    assert env['ANTHROPIC_BASE_URL'] == 'http://h'


def test_prepare_launch_fails_before_the_session_opens_when_unreachable(monkeypatch):
    """Raised BEFORE the terminal spawns, deliberately: a launch that succeeds
    and only dies once `claude` tries the model leaves the user in a new console
    with no path back to the setting that was wrong."""
    monkeypatch.setattr(omniroute, 'is_reachable', lambda *a, **k: False)
    import pytest
    with pytest.raises(RuntimeError, match='not reachable'):
        omniroute.prepare_launch('m', {'provider_kind': 'generic',
                                       'provider_base_url': 'http://dead'})


def test_context_warning_counts_claude_codes_own_system_prompt():
    """The 10k-token floor is the whole point: a default 4096-token Ollama
    context is already over budget with an empty repo, and a check that weighed
    only CLAUDE.md would call that fine."""
    assert omniroute.context_warning({'provider_context_tokens': 4096}, 1) != ''
    # no payload measured -> nothing to say
    assert omniroute.context_warning({'provider_context_tokens': 4096}, 0) == ''
    # a big window swallows the same payload
    assert omniroute.context_warning({'provider_context_tokens': 200000}, 4000) == ''
