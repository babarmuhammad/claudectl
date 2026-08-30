"""Every function reaches every surface, and every account.

`test_parity_gate.py` proves the sessions screen's 29 keys each name a GUI
route. That gate works, and it is also exactly as far as it reaches: it does not
cover the main menu or the settings screen, and it checks that a route STRING
EXISTS — which three of the misses this file was written for satisfied while
having no control anywhere in the GUI.

The rungs, each in the layer it belongs to:

  rung 2  the SPA actually CALLS the route (extracted call sites, not a
          substring, so a route named only in a comment does not count)
  rung 3  the verb matches — a POST route appears in a `post(` call
  rung 4  no read-only toggle: a field the GUI renders as on/off must be a
          field it can also send back. This is the class rungs 2-3 cannot see,
          because the failure is not an unused route — it is NO route at all.

Plus the two namespaces that must agree about the settings keys, and the
account fan-out that existed with zero callers for its whole life.
"""

import ast
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_sessions import config, gui, gui_api, hooks, main, session_menu, ui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, 'claude_sessions', 'web', 'app.js')


def _js():
    return io.open(APP_JS, encoding='utf-8').read()


#: comments are prose, not behaviour. Scanning them found `hook_on` inside the
#: very comment explaining that hook_on used to be read-only — the same class of
#: false positive rung 2 avoids by extracting call sites instead of substrings.
def _strip_js_comments(src):
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    return re.sub(r'^\s*//.*$', '', src, flags=re.M)


def _routes():
    return (set(gui_api.GET_ROUTES) | set(gui_api.POST_ROUTES)
            | set(gui._LOCAL_GET) | set(gui._LOCAL_POST))


#: `api('/api/x…` / `post('/api/x…` — the CALL SITE, not the string anywhere.
#: A route mentioned only in a comment does not count as reachable, which is
#: the difference between this and a substring search.
_CALL = re.compile(r"""\b(api|post)\s*\(\s*['"`](/api/[a-z0-9/_-]+)""", re.I)


def _called():
    """{route: {verbs}} for every route the SPA actually calls."""
    out = {}
    for fn, route in _CALL.findall(_js()):
        out.setdefault(route, set()).add(fn.lower())
    return out


# ── rung 2 / 3: the SPA calls the route, with the right verb ───────────────

def test_every_route_is_called_or_says_why_not():
    """A route nobody calls is either dead or a missing control.

    Seven were uncalled when this landed and every one was the second: a real
    capability with no way to reach it. A route that is legitimately
    terminal-only carries its reason as a comment on its own table line.
    """
    src = io.open(gui_api.__file__, encoding='utf-8').read()
    called = set(_called())
    missing = []
    for route in sorted(_routes()):
        if route in called:
            continue
        line = next((ln for ln in src.splitlines()
                     if ("'%s'" % route) in ln and ':' in ln), '')
        if '#' in line:                      # a written reason, on the line
            continue
        missing.append(route)
    assert not missing, (
        'GUI routes the SPA never calls, with no reason on their table line: %s'
        % missing)


def test_a_post_route_is_reached_with_post():
    """Calling a POST route through `api(` sends a GET and gets a 404 — and the
    SPA shows that as an empty page rather than an error."""
    called = _called()
    wrong = [r for r in gui_api.POST_ROUTES
             if r in called and 'post' not in called[r]
             and r not in gui_api.GET_ROUTES]
    assert not wrong, 'POST routes the SPA reaches with api(): %s' % wrong


def test_the_call_site_regex_does_not_match_a_bare_mention():
    """The gate above is only worth anything if a commented-out route fails it.

    Mutation-verified: moving `/api/hooks` into a comment must still count as
    uncalled, which a substring search would not manage.
    """
    assert not _CALL.findall("// see /api/hooks for the shape")
    assert _CALL.findall("const d=await api('/api/hooks');")


# ── rung 4: a toggle the GUI renders must be a toggle the GUI can send ─────

_TOGGLE = re.compile(r"""(\w+)\s*\?\s*['"]on['"]\s*:\s*['"]off['"]""")


def test_no_setting_is_rendered_as_a_read_only_toggle():
    """`${st.hook_on?'on':'off'}` prints a switch you cannot flip.

    The waiver is DERIVED, not listed: a field is fine if the SPA sends it back
    somewhere, i.e. it appears as an object key. `otel_enabled` self-waives
    because setOtelSave posts it; `hook_on` and `rules_on` did not, and there
    was no route for them to be sent to.
    """
    js = _strip_js_comments(_js())
    bad = []
    for field in set(_TOGGLE.findall(js)):
        name = field.split('.')[-1]
        if re.search(r'\b%s\s*:' % re.escape(name), js):
            continue                          # sent back somewhere — flippable
        bad.append(name)
    assert not bad, 'fields rendered as on/off that the GUI cannot send: %s' % bad


# ── the hooks reader must walk BOTH state blocks ───────────────────────────

def test_the_hooks_reader_walks_every_state_block():
    """A hook disabled in the TUI vanished from the GUI, which then offered its
    template as uninstalled and created an enabled duplicate beside it."""
    assert hooks.HOOK_STATE_KEYS == ('hooks', 'hooks_disabled')
    src = io.open(gui_api.__file__, encoding='utf-8').read()
    body = src[src.index('def api_hooks_get('):src.index('def _template_installed(')]
    assert 'HOOK_STATE_KEYS' in body, (
        'api_hooks_get reads a bare block name again — a disabled hook will vanish')


# ── settings: ONE namespace, four set intersections ────────────────────────

def _tui_setting_ids():
    """Row ids of ui.settings_menu that name a setting.

    Read from the source rather than by driving the screen: the ids ARE the
    settings keys now, so the join is a set operation instead of a mapping
    nobody maintains.
    """
    src = io.open(ui.__file__, encoding='utf-8').read()
    block = src[src.index('def settings_menu('):]
    block = block[:block.index('\n\ndef ')]
    ids = set(re.findall(r"\{C_RESET\}\", '([a-z_]+)'\)|\}\", '([a-z_]+)'\)", block))
    return {a or b for a, b in ids}


def test_the_tui_settings_rows_are_named_after_the_settings_they_write():
    """They were not: 'claude', 'config_dir', 'headless_budget' and five hidden
    `s[f'default_{sel}']` rewrites meant no test could join this screen to the
    GUI's list of the same settings."""
    ids = _tui_setting_ids()
    assert ids, 'the settings_menu row walk found nothing — the shape changed'
    #: rows that open another SCREEN rather than write a key
    submenus = {'automode', 'theme', 'failover', 'ui_mode', 'back'}
    unknown = {i for i in ids if i not in submenus} - set(config._DEFAULT_SETTINGS)
    assert not unknown, 'settings rows that name no setting: %s' % sorted(unknown)


def test_the_gui_setting_keys_are_derived_from_the_registry():
    """A sixth hand-maintained table would be a copy of _DEFAULT_SETTINGS that
    falls behind it — `claude_config_dir` was missing from exactly such a copy
    while three others were accepted and had no control."""
    assert set(gui._SETTING_KEYS) == (set(config._DEFAULT_SETTINGS)
                                      - config.INTERNAL_SETTINGS)
    # and every excluded key really does have another owner in the handler
    src = io.open(gui.__file__, encoding='utf-8').read()
    body = src[src.index('def _api_settings('):src.index('_LOCAL_GET =')]
    for k in ('failover_models', 'nav_collapsed', 'side_w', 'headless_budget_usd',
              'provider_api_key', 'ui_mode'):
        assert k in body, '%s is excluded from the generic loop and unhandled' % k


def test_every_tui_setting_reaches_the_gui():
    """The four that did not — editor, claude.exe, config dir, budget cap — are
    the gap this file was written for."""
    ids = _tui_setting_ids() & set(config._DEFAULT_SETTINGS)
    assert ids <= set(gui._SETTING_KEYS) | config.INTERNAL_SETTINGS
    payload = gui.state_payload()
    missing = [k for k in ids if k not in payload
               and k not in {'default_effort', 'default_model', 'default_permission',
                             'default_max_thinking', 'default_subagent_model'}]
    assert not missing, 'settings the GUI never receives: %s' % missing


def test_the_settings_the_gui_saves_are_the_settings_it_renders():
    """False-positive guard included: provider_base_url reaches post() through
    a variable, and must stay green."""
    js = _js()
    for key in ('default_model', 'editor', 'claude_exe', 'claude_config_dir',
                'headless_budget_usd', 'provider_base_url'):
        assert re.search(r'\b%s\s*:' % key, js), (
            '%s is a setting the GUI never sends back' % key)


# ── the main menu ──────────────────────────────────────────────────────────

def test_every_main_menu_row_has_a_gui_counterpart():
    """The screen ACTIONS does not cover. `Global CLAUDE.md / MCP Analysis` had
    no GUI surface at all and nothing could say so."""
    routes = _routes()
    missing = [(k, r) for _l, k, r in main.MAIN_ACTIONS if r and r not in routes]
    assert not missing, 'main-menu rows naming a route that does not exist: %s' % missing


def test_a_main_menu_row_without_a_counterpart_must_say_why():
    src = io.open(main.__file__, encoding='utf-8').read()
    table = src[src.index('MAIN_ACTIONS = ['):src.index('def run():')]
    for _label, key, route in main.MAIN_ACTIONS:
        if route:
            continue
        line = next(ln for ln in table.splitlines() if "'%s'" % key in ln)
        assert '#' in line, 'main-menu row %r gives no reason' % (key,)


def test_the_main_menu_is_built_from_the_table():
    """Hoisting it is what makes the gate possible; if the menu stops reading
    it, the table is decoration."""
    src = io.open(main.__file__, encoding='utf-8').read()
    assert 'for label, key, _route in MAIN_ACTIONS' in src
    keys = {k for _l, k, _r in main.MAIN_ACTIONS}
    assert {'__mcp__', '__hooks__', '__settings__', '__accounts__'} <= keys


# ── stubs that stand in for a stdlib function must accept its real calls ───

def test_a_patched_get_terminal_size_accepts_keyword_arguments():
    """`lambda *a` is not a stand-in for `shutil.get_terminal_size`.

    Its real signature takes `fallback=` as a KEYWORD, and pytest's own terminal
    writer calls it that way. One test patched the GLOBAL shutil with a
    positional-only lambda, so every subsequent report line raised inside
    pytest: the run printed "679 passed" and then exited 1 with an
    INTERNALERROR. It only ever failed on CI, because a local TTY run reaches a
    different branch of the writer — which is exactly why a source-level gate is
    the right layer for it.
    """
    bad = []
    for d in ('tests', 'tools'):
        root = os.path.join(ROOT, d)
        for fn in sorted(os.listdir(root)):
            if not fn.endswith('.py'):
                continue
            src = io.open(os.path.join(root, fn), encoding='utf-8').read()
            for m in re.finditer(r"get_terminal_size'?\s*,\s*\n?\s*(lambda[^:]*):", src):
                if '**' not in m.group(1):
                    bad.append('%s/%s: %s' % (d, fn, m.group(1).strip()))
    assert not bad, 'get_terminal_size stubs that reject fallback=: %s' % bad


# ── the account fan-out ────────────────────────────────────────────────────

def test_the_fan_out_helper_has_callers():
    """The one that would have caught the whole class.

    `hooks.across_accounts` shipped with a docstring calling itself "the fan-out
    target for anything that installs into settings.json" and ZERO callers, so
    four of five accounts had no hooks at all while every surface reported them
    installed.
    """
    # an ATTRIBUTE access on the hooks module, not the substring: the substring
    # also matches context_inject.find_sessions_across_accounts, which would have
    # made this gate pass with every real caller deleted.
    call = re.compile(r'\b\w+\.across_accounts\s*\(')
    hits = []
    pkg = os.path.join(ROOT, 'claude_sessions')
    for fn in sorted(os.listdir(pkg)):
        if not fn.endswith('.py') or fn == 'hooks.py':
            continue
        src = io.open(os.path.join(pkg, fn), encoding='utf-8').read()
        if call.search(src):
            hits.append(fn)
    assert hits, 'hooks.across_accounts has no callers outside hooks.py'


def test_no_provisioning_path_binds_the_account_at_import():
    """`agents.user_agents_dir` joined a `config_dir` imported BY VALUE: single
    account and frozen at import. A legitimate survivor carries a `# waiver:`
    comment on the import line."""
    pkg = os.path.join(ROOT, 'claude_sessions')
    offenders = []
    for fn in sorted(os.listdir(pkg)):
        if not fn.endswith('.py'):
            continue
        path = os.path.join(pkg, fn)
        src = io.open(path, encoding='utf-8').read()
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.ImportFrom) and node.module == 'config'):
                continue
            if not any(a.name in ('config_dir', 'projects_dir', 'global_claude_md')
                       for a in node.names):
                continue
            near = lines[max(0, node.lineno - 5):node.lineno]
            if any('waiver:' in ln for ln in near):
                continue
            offenders.append('%s:%d' % (fn, node.lineno))
    assert not offenders, (
        'modules importing an account-derived path BY VALUE, with no waiver: %s'
        % offenders)


def test_every_claude_cli_call_names_an_account():
    """`claude plugin …` with no env lands on whatever CLAUDE_CONFIG_DIR the
    process inherited, while every reader resolves cfgdir — so read and write
    could name different accounts."""
    from claude_sessions import plugins, mcp
    for src, name in ((io.open(plugins.__file__, encoding='utf-8').read(), 'plugins'),
                      (io.open(mcp.__file__, encoding='utf-8').read(), 'mcp')):
        runner = src[src.index('def _claude_cli(' if name == 'plugins'
                               else 'def mcp_cli('):]
        runner = runner[:runner.index('\n\n\ndef ')]
        assert 'account_env(' in runner, (
            '%s runs the claude CLI without naming an account' % name)
    # and the plain-subprocess reader too
    src = io.open(mcp.__file__, encoding='utf-8').read()
    body = src[src.index('def get_mcp_status('):src.index('def _status_icon(')]
    assert 'account_env(' in body, 'claude mcp list does not name an account'


def test_a_per_project_toggle_does_not_promise_a_hook_another_account_lacks(
        monkeypatch, tmp_path):
    """The skew from the report: `memory_hook` is stored PER PROJECT while the
    hook is installed PER ACCOUNT, so the same project under another login said
    the feature was on while nothing ran."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from harness import Sandbox
    Sandbox(monkeypatch, tmp_path)
    from claude_sessions import memhub
    a, b = tmp_path / 'acctA', tmp_path / 'acctB'
    a.mkdir(); b.mkdir()
    monkeypatch.setattr(config, 'all_config_dirs',
                        lambda: [('A', str(a)), ('B', str(b))])
    memhub.set_prompt_hook('proj-enc', True)
    assert hooks.memory_hook_installed(str(a)), 'account A never got the hook'
    assert hooks.memory_hook_installed(str(b)), (
        'the toggle promised recall on every account and installed it on one')
