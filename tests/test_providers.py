"""The provider layer: the omniroute_* -> provider_* migration, the one seam
every routed launch goes through, and the two bugs the rename uncovered.

Every test here was mutation-verified: revert the change it covers and it goes
red. That matters more than usual for the migration, because the failure mode is
silent (a user's settings quietly reverting to defaults on upgrade).
"""
import os

from claude_sessions import config as c
from claude_sessions import stats


# ── the migration ────────────────────────────────────────────────

def test_migration_carries_the_old_omniroute_keys_forward():
    """load_settings() parks unrecognised keys in _unknown, so by the time
    migrate_settings runs the old names are THERE and not at the top level.
    Reading the top level instead finds nothing and silently resets a
    configured user to defaults."""
    s = dict(c._DEFAULT_SETTINGS)
    s[c._UNKNOWN_KEYS] = {'omniroute_base_url': 'http://box:20128',
                          'omniroute_api_key': 'sk-old',
                          'omniroute_exec_model': 'auto/coding'}
    out, changed = c.migrate_settings(s)
    assert changed
    assert out['provider_base_url'] == 'http://box:20128'
    assert out['provider_api_key'] == 'sk-old'
    assert out['provider_exec_model'] == 'auto/coding'


def test_migration_infers_the_kind_from_a_configured_exec_model():
    """Before there was a kind, "routing is on" WAS "an exec model is set" --
    so that is the only signal available to migrate from."""
    s = dict(c._DEFAULT_SETTINGS)
    s[c._UNKNOWN_KEYS] = {'omniroute_exec_model': 'auto/coding'}
    out, _ = c.migrate_settings(s)
    assert out['provider_kind'] == 'omniroute'


def test_migration_leaves_a_user_who_never_used_omniroute_on_anthropic():
    s = dict(c._DEFAULT_SETTINGS)
    out, _ = c.migrate_settings(s)
    assert out['provider_kind'] == ''
    assert out['provider_exec_model'] == ''


def test_migration_drops_the_dead_names_so_they_are_not_rewritten_forever():
    """save_settings layers _unknown back over its output, so a legacy key left
    in that bucket is written to disk on every save for the rest of time."""
    s = dict(c._DEFAULT_SETTINGS)
    s[c._UNKNOWN_KEYS] = {'omniroute_api_key': 'sk-old', 'future_key': 1}
    out, _ = c.migrate_settings(s)
    assert 'omniroute_api_key' not in out[c._UNKNOWN_KEYS]
    # a key from a NEWER claudectl is not ours to delete
    assert out[c._UNKNOWN_KEYS]['future_key'] == 1


def test_migration_is_idempotent():
    s = dict(c._DEFAULT_SETTINGS)
    s[c._UNKNOWN_KEYS] = {'omniroute_base_url': 'http://box:20128'}
    out, first = c.migrate_settings(s)
    out2, second = c.migrate_settings(out)
    assert first and not second
    assert out2['provider_base_url'] == 'http://box:20128'


def test_a_second_run_does_not_undo_a_user_who_switched_back_to_anthropic():
    """The flag is what makes it one-time. Without it, turning routing off is
    overruled on the next start."""
    s = dict(c._DEFAULT_SETTINGS)
    s[c._UNKNOWN_KEYS] = {'omniroute_exec_model': 'auto/coding'}
    out, _ = c.migrate_settings(s)
    out['provider_kind'] = ''
    out['provider_exec_model'] = ''
    out, changed = c.migrate_settings(out)
    assert not changed and out['provider_kind'] == ''


# ── schema discipline ────────────────────────────────────────────

def test_every_provider_key_is_declared_so_the_gui_cannot_wipe_it():
    """/api/settings does load -> mutate -> save and load_settings drops what it
    does not know, so an undeclared key is written once and deleted by the very
    next save."""
    for k in ('provider_base_url', 'provider_api_key', 'provider_exec_model',
              'provider_kind', 'provider_context_tokens', 'provider_tool_search',
              'provider_keys_migrated'):
        assert k in c._DEFAULT_SETTINGS, k


def test_the_provider_key_is_write_only():
    """Same rule the OmniRoute key already had: a secret is never echoed back
    into a form that would resubmit it."""
    assert 'provider_api_key' in c.INTERNAL_SETTINGS


# ── the env the seam produces ────────────────────────────────────

def test_anthropic_direct_is_untouched():
    """The default path must add no env at all -- not a base URL, not a
    disabled-feature flag. Anything else changes every existing user's session."""
    assert c.provider_env(dict(c._DEFAULT_SETTINGS)) == {}


def test_adaptive_thinking_is_disabled_for_every_routed_backend():
    """Including an Anthropic-SHAPED one. A thinking block carries a signature
    that must round-trip byte-for-byte to the infrastructure that minted it, so
    a local server cannot produce one -- and Claude Code sends
    thinking:{"type":"adaptive"} unconditionally, which a backend that does not
    know the field answers with 400. That fails the whole turn, not the
    thinking."""
    for kind in ('omniroute', 'generic'):
        s = dict(c._DEFAULT_SETTINGS, provider_kind=kind,
                 provider_exec_model='m', provider_base_url='http://h')
        assert c.provider_env(s)['CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING'] == '1'


def test_the_failover_proxy_still_wins_the_base_url():
    s = dict(c._DEFAULT_SETTINGS, provider_kind='generic', provider_exec_model='m',
             provider_base_url='http://upstream', failover_models=['b'],
             failover_port=20129)
    assert c.provider_env(s)['ANTHROPIC_BASE_URL'] == 'http://127.0.0.1:20129'


# ── the subagent frontmatter bug ─────────────────────────────────

def test_synced_agents_lose_their_model_field_on_a_routed_session(tmp_path, monkeypatch):
    """A bare Anthropic id in agent frontmatter cannot be resolved by a routed
    backend: the subagent 401s, or the proxy answers "Ambiguous model". Three of
    the four call sites never passed the flag, so GUI-launched and
    suggestion-accepted agents kept it and broke on every routed session --
    which is why the default is derived here instead of asked of callers."""
    from claude_sessions import agents
    lib = tmp_path / 'lib'
    lib.mkdir()
    (lib / 'a.md').write_text('---\nname: a\nmodel: claude-haiku-4-5\n---\nbody\n',
                              encoding='utf-8')
    monkeypatch.setattr(agents, 'find_library_agent', lambda ref: str(lib / 'a.md'))
    proj = tmp_path / 'proj'
    proj.mkdir()
    monkeypatch.setattr(c, 'load_settings',
                        lambda: dict(c._DEFAULT_SETTINGS, provider_kind='generic'))
    agents.sync_project_agents(str(proj), ['a'])
    out = (proj / '.claude' / 'agents' / 'a.md').read_text(encoding='utf-8')
    assert 'model:' not in out
    assert 'name: a' in out


def test_synced_agents_keep_their_model_field_on_anthropic(tmp_path, monkeypatch):
    from claude_sessions import agents
    lib = tmp_path / 'lib'
    lib.mkdir()
    (lib / 'a.md').write_text('---\nname: a\nmodel: claude-haiku-4-5\n---\nbody\n',
                              encoding='utf-8')
    monkeypatch.setattr(agents, 'find_library_agent', lambda ref: str(lib / 'a.md'))
    proj = tmp_path / 'proj'
    proj.mkdir()
    monkeypatch.setattr(c, 'load_settings', lambda: dict(c._DEFAULT_SETTINGS))
    agents.sync_project_agents(str(proj), ['a'])
    out = (proj / '.claude' / 'agents' / 'a.md').read_text(encoding='utf-8')
    assert 'model: claude-haiku-4-5' in out


# ── the cost bug ─────────────────────────────────────────────────

def test_an_unpriced_model_reads_as_not_tracked_never_as_zero():
    """A routed model may be a paid OpenRouter or self-hosted one. `~$0.00` told
    that user their spend was approximately nothing."""
    cost, exact = stats.estimate_cost({'qwen3-coder': {'in': 100000, 'out': 50000}})
    assert not exact
    assert stats.fmt_cost(cost, exact) == 'n/a'


def test_a_priced_model_still_shows_its_number():
    cost, exact = stats.estimate_cost({'claude-sonnet-5': {'in': 1000000, 'out': 0}})
    assert exact and cost > 0
    assert stats.fmt_cost(cost, exact).startswith('3') or '.' in stats.fmt_cost(cost, exact)


def test_a_mixed_rollup_still_quotes_the_part_it_knows():
    """cost>0 with exact=False is a real, useful number for the Anthropic half --
    only a rollup with NOTHING priced becomes n/a."""
    cost, exact = stats.estimate_cost({'claude-sonnet-5': {'in': 1000000, 'out': 0},
                                       'qwen3-coder': {'in': 999999, 'out': 999999}})
    assert cost > 0 and not exact
    assert stats.fmt_cost(cost, exact).startswith('~')


def test_the_gui_renders_the_same_two_cases():
    """Two renderers of one rule is two chances to disagree about it, so the
    JS helper is asserted to exist rather than trusted."""
    js = open(os.path.join(os.path.dirname(__file__), '..', 'claude_sessions',
                           'web', 'app.js'), encoding='utf-8').read()
    assert 'function costCell(' in js
    assert "'n/a'" in js.split('function costCell(')[1][:200]
    assert '?.exact' not in js  # no stray old-style inline render left behind
    assert "exact?'':'~'}$${" not in js
