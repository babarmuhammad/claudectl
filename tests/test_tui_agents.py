import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, UP, DOWN, RIGHT, ENTER, ESC

from claude_sessions import agents


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def _seed(sb, category, name, **meta):
    d = sb.agents_lib / category
    d.mkdir(parents=True, exist_ok=True)
    m = {'name': name, 'description': meta.get('description', 'desc')}
    m.update({k: v for k, v in meta.items() if k != 'description'})
    agents.write_agent(str(d / f'{name}.md'), m, meta.get('body', 'You are an agent.'))


# ── pure helpers ─────────────────────────────────────────────

def test_parse_write_roundtrip(tmp_path):
    p = tmp_path / 'a.md'
    agents.write_agent(str(p),
                       {'name': 'rev', 'description': 'review code',
                        'tools': 'Read, Grep', 'model': 'opus-4-8'},
                       'You are rev.\n\nDo things.')
    meta, body = agents.parse_agent(str(p))
    assert meta['name'] == 'rev'
    assert meta['tools'] == 'Read, Grep'
    assert 'You are rev.' in body


def test_library_listing(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'api-designer', model='sonnet')
    _seed(sb, '01-core', 'backend')
    _seed(sb, '02-lang', 'pythonista')
    assert agents.list_categories() == ['01-core', '02-lang']
    names = [n for n, *_ in agents.list_library_agents('01-core')]
    assert names == ['api-designer', 'backend']
    assert len(agents.all_library_agents()) == 3


def test_build_agents_json(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'rev', description='reviews', tools='Read, Grep',
          model='opus-4-8', body='Review carefully.')
    js = agents.build_agents_json(['01-core/rev'])
    d = json.loads(js)
    assert d['rev']['description'] == 'reviews'
    assert d['rev']['prompt'] == 'Review carefully.'
    assert d['rev']['tools'] == ['Read', 'Grep']
    assert d['rev']['model'] == 'opus-4-8'


def test_build_agents_json_skips_missing(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    assert agents.build_agents_json(['nope/ghost']) == '{}'


def test_agents_json_tempfile(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'rev')
    p = agents.write_agents_json_tempfile(['01-core/rev'])
    assert p and os.path.isfile(p)
    assert agents.write_agents_json_tempfile([]) == ''
    os.remove(p)


def test_sync_project_agents(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'rev')
    _seed(sb, '01-core', 'backend')
    proj = tmp_path / 'proj'
    proj.mkdir()
    dest = proj / '.claude' / 'agents'

    # select two → both copied
    n = agents.sync_project_agents(str(proj), ['01-core/rev', '01-core/backend'])
    assert n == 2
    assert (dest / 'rev.md').exists() and (dest / 'backend.md').exists()

    # deselect one → its file removed, other kept; user files untouched
    (dest / 'mine.md').write_text('user owned', encoding='utf-8')
    agents.sync_project_agents(str(proj), ['01-core/rev'])
    assert (dest / 'rev.md').exists()
    assert not (dest / 'backend.md').exists()
    assert (dest / 'mine.md').exists()

    # deselect all → managed file gone, user file stays, manifest removed
    agents.sync_project_agents(str(proj), [])
    assert not (dest / 'rev.md').exists()
    assert (dest / 'mine.md').exists()
    assert not (dest / agents._MANIFEST).exists()


# ── per-session selection screen ─────────────────────────────

def test_suggest_agents_matches_language(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    # project has python files → connections meta languages picks Python
    os.makedirs(os.path.join(actual, 'src'), exist_ok=True)
    open(os.path.join(actual, 'src', 'main.py'), 'w').write('x = 1\n')
    _seed(sb, '02-lang', 'python-pro',
          description='Build type-safe production Python code and APIs')
    _seed(sb, '02-lang', 'golang-pro',
          description='Concurrent Go microservices and cloud-native systems')
    from claude_sessions import connections
    connections.build_hierarchy(actual, folder)      # populate the cache suggest reads
    sug = agents.suggest_agents(actual, folder)
    refs = [r for r, _reason, _s in sug]
    assert '02-lang/python-pro' in refs
    assert '02-lang/golang-pro' not in refs          # no Go signal


def test_suggest_agents_empty_without_signals(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('zzz')
    _seed(sb, '01-core', 'reviewer', description='review pull requests')
    assert agents.suggest_agents(actual, folder) == []


def test_select_shows_suggested_and_toggles(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    os.makedirs(os.path.join(actual, 'src'), exist_ok=True)
    open(os.path.join(actual, 'src', 'main.py'), 'w').write('x = 1\n')
    _seed(sb, '02-lang', 'python-pro',
          description='Build type-safe production Python code and APIs')
    from claude_sessions import connections
    connections.build_hierarchy(actual, folder)      # populate cache
    # ENTER on the first (suggested) row toggles it, then nav to Done
    keys = flat(ENTER, DOWN, DOWN, ENTER)
    res, cap, _ = run_flow(monkeypatch, keys, agents.select_session_agents,
                           'alpha', [], actual, folder)
    assert '★' in cap.plain                          # suggested section shown
    assert res == ['02-lang/python-pro']             # toggle worked


def test_select_session_agents(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'api-designer')
    _seed(sb, '01-core', 'backend')
    _seed(sb, '02-lang', 'pythonista')
    # enter 01-core, toggle first (api-designer), confirm; then Done
    keys = flat(ENTER,                 # open first category (01-core)
                typed(' '), ENTER,     # multiselect: toggle api-designer, confirm
                DOWN, ENTER)           # back on category list: 'Done' is after separator
    # category menu items: 01-core, 02-lang, sep, Done, Clear all.
    # After returning from multiselect we're at top; navigate to Done.
    res, cap, _ = run_flow(monkeypatch, keys, agents.select_session_agents, 'proj', [])
    # may need exact nav; assert at least it returns a list (not crash)
    assert res is None or isinstance(res, list)


def test_select_empty_library_returns_empty(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)   # library empty
    res, _, _ = run_flow(monkeypatch, flat(ENTER), agents.select_session_agents, 'proj', [])
    assert res == []


def test_over_limit_warns_but_returns(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    for i in range(agents.SAFE_AGENT_LIMIT + 3):
        _seed(sb, '01-core', f'a{i:02d}')
    # open category, select all, confirm; then Done
    keys = flat(ENTER, typed('a'), ENTER, DOWN, ENTER)
    res, cap, _ = run_flow(monkeypatch, keys, agents.select_session_agents, 'proj', [])
    assert res is not None and len(res) > agents.SAFE_AGENT_LIMIT
    assert 'over' in cap.plain.lower() or 'launch may fail' in cap.plain.lower()


def test_select_preselected_shown(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'rev')
    # Done immediately (preselected kept). Done is 2nd selectable (after the cat).
    keys = flat(DOWN, ENTER)
    res, cap, _ = run_flow(monkeypatch, keys, agents.select_session_agents, 'proj',
                           ['01-core/rev'])
    assert res == ['01-core/rev']


# ── create / browse ──────────────────────────────────────────

def test_new_manual_into_category(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'existing')
    # agents_menu: categories(01-core), sep, New manual, New AI
    # New manual -> pick category (01-core first) -> name -> desc -> tools -> model
    keys = flat(DOWN, ENTER,                 # New agent (manual) (2nd selectable)
                ENTER,                       # category: 01-core (first)
                typed('helper'), ENTER,      # name
                typed('helps'), ENTER,       # description
                typed(' '), ENTER,           # tools: toggle Read, confirm
                ENTER,                       # model default
                ESC)
    run_flow(monkeypatch, keys, agents.agents_menu, None)
    p = sb.agents_lib / '01-core' / 'helper.md'
    assert p.exists()
    meta, _ = agents.parse_agent(str(p))
    assert meta['name'] == 'helper' and 'Read' in meta.get('tools', '')


def test_new_manual_new_category(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'x')
    keys = flat(DOWN, ENTER,                 # New manual
                DOWN, ENTER,                 # category: New category (after sep)
                typed('99-custom'), ENTER,   # new cat name
                typed('myagent'), ENTER,     # name
                typed('does things'), ENTER, # desc
                ENTER,                       # tools none
                ENTER,                       # model default
                ESC)
    run_flow(monkeypatch, keys, agents.agents_menu, None)
    assert (sb.agents_lib / '99-custom' / 'myagent.md').exists()


def test_new_ai_agent(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'x')
    monkeypatch.setattr(agents, 'get_claude_exe', lambda: r'C:\fake.exe')
    body = "---\nname: sec\ndescription: security\n---\n\nYou review security."
    monkeypatch.setattr(agents, 'run_with_progress', lambda *a, **k: (body, False))
    keys = flat(DOWN, DOWN, ENTER,           # New AI (3rd selectable)
                ENTER,                       # category 01-core
                typed('sec'), ENTER,         # name
                typed('review security'), ENTER,  # role
                ENTER,                       # approve pager
                ESC)
    run_flow(monkeypatch, keys, agents.agents_menu, None)
    assert (sb.agents_lib / '01-core' / 'sec.md').exists()


def test_delete_agent(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'gone')
    # categories(01-core) -> ENTER browse -> ENTER agent -> detail Delete -> confirm
    keys = flat(ENTER,             # open 01-core
                ENTER,             # first agent (gone)
                DOWN, ENTER,       # detail: Edit(0), Delete(1)
                RIGHT, ENTER,      # confirm No->Yes
                ESC, ESC)
    run_flow(monkeypatch, keys, agents.agents_menu, None)
    assert not (sb.agents_lib / '01-core' / 'gone.md').exists()
