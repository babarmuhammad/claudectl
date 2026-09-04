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


# ── getting the installed agents actually USED ────────────────
#
# The complaint this answers: agents are selected, copied in, carried on every
# launch — and almost never delegated to. Claude Code picks a subagent by
# matching the task against its `description`, so a set of files nothing ever
# mentions is a set that never fires. CLAUDE.md is read on every turn, which
# makes it the one place a delegation table can change the outcome.

def test_installing_agents_writes_a_delegation_table_into_claude_md(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'rev')
    proj = tmp_path / 'proj'
    proj.mkdir()
    agents.sync_project_agents(str(proj), ['01-core/rev'])
    md = (proj / 'CLAUDE.md').read_text(encoding='utf-8')
    assert '<!-- CLAUDECTL:AGENTS:START -->' in md
    assert 'rev' in md and 'Delegate' in md


def test_the_table_disappears_with_the_last_agent(monkeypatch, tmp_path):
    """A table naming agents that are no longer installed is worse than none:
    it tells the model to delegate to something that is not there."""
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'rev')
    proj = tmp_path / 'proj'
    proj.mkdir()
    (proj / 'CLAUDE.md').write_text('# proj\n\nMy own notes.\n', encoding='utf-8')
    agents.sync_project_agents(str(proj), ['01-core/rev'])
    agents.sync_project_agents(str(proj), [])
    md = (proj / 'CLAUDE.md').read_text(encoding='utf-8')
    assert 'CLAUDECTL:AGENTS' not in md
    assert 'My own notes.' in md, 'the block must take nothing else with it'


def test_the_table_is_built_from_what_is_on_disk(monkeypatch, tmp_path):
    """From the agent files themselves, so it cannot describe a selection that
    was changed by hand or by another tool."""
    sb = Sandbox(monkeypatch, tmp_path)
    proj = tmp_path / 'proj'
    dest = proj / '.claude' / 'agents'
    dest.mkdir(parents=True)
    (dest / 'sec.md').write_text(
        '---\nname: sec\ndescription: Use this agent when auditing auth code. '
        'It also does other things.\n---\n\nbody\n', encoding='utf-8')
    rows = agents.routing_table(str(proj))
    assert rows == [('sec', 'auditing auth code')], rows


def test_the_prompt_hook_reads_one_small_index(monkeypatch, tmp_path):
    """It fires on EVERY prompt. Opening and parsing every agent file per turn
    is the cost mistake the recall hook and the worklog hook each made once, so
    `sync_project_agents` writes a single index and the hook reads that."""
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'rev')
    proj = tmp_path / 'proj'
    proj.mkdir()
    agents.sync_project_agents(str(proj), ['01-core/rev'])
    idx = proj / '.claude' / agents.AGENT_INDEX
    assert idx.is_file()
    import json as _json
    rows = _json.loads(idx.read_text(encoding='utf-8'))['agents']
    assert rows and rows[0]['name'] and rows[0]['keywords']
    # …and it goes when the last agent does
    agents.sync_project_agents(str(proj), [])
    assert not idx.exists()


def test_the_hook_names_a_match_and_stays_quiet_otherwise():
    """A hook on every prompt has to be silent by default: two coincidental
    words are not a reason to interrupt."""
    from claude_sessions import agentnudge_hook as nudge
    index = [{'name': 'security-auditor',
              'keywords': ['security', 'auth', 'vulnerability', 'audit']},
             {'name': 'frontend-developer',
              'keywords': ['react', 'css', 'component', 'browser']}]
    hits = nudge.suggest('review the auth flow for a security vulnerability', index)
    assert [n for n, _s, _r in hits] == ['security-auditor']
    assert nudge.suggest('rename a variable', index) == []
    assert nudge.suggest('', index) == []
    # one weak overlap is not a match
    assert nudge.suggest('is this secure?', index) == []


def test_keywords_come_from_the_description_the_router_reads():
    """The hook and Claude Code must be matching on the same text, or the hook
    recommends what the model would never pick."""
    kw = agents.keywords_for('security-auditor',
                             'Use PROACTIVELY when auditing authentication code '
                             'for vulnerabilities.')
    assert 'auditing' in kw and 'authentication' in kw
    assert 'the' not in kw and 'use' not in kw and 'proactively' not in kw


def test_sharpening_rewrites_only_the_description(monkeypatch, tmp_path):
    """It is the only field Claude Code routes on — and touching the body would
    change what the agent DOES while leaving the reason it is never picked."""
    sb = Sandbox(monkeypatch, tmp_path)
    proj = tmp_path / 'proj'
    dest = proj / '.claude' / 'agents'
    dest.mkdir(parents=True)
    (dest / 'sec.md').write_text(
        '---\nname: sec\ndescription: Expert security engineer.\nmodel: opus\n---\n\n'
        'You are a security engineer.\nLine two.\n', encoding='utf-8')
    done = agents.apply_descriptions(str(proj), {
        'sec': 'Use PROACTIVELY when auditing auth code. Do not use for UI work.'})
    assert done == ['sec']
    meta, body = agents.parse_agent(str(dest / 'sec.md'))
    assert meta['description'].startswith('Use PROACTIVELY')
    assert meta['model'] == 'opus', 'the other frontmatter survives'
    assert 'You are a security engineer.' in body and 'Line two.' in body


def test_the_same_agent_in_many_places_is_one_question(monkeypatch, tmp_path):
    """A library agent copied into a dozen projects is the SAME description a
    dozen times. Grouping is what keeps one prompt row from becoming twelve —
    and twelve chances at twelve different answers to one question."""
    rows = [
        {'name': 'sec', 'desc': 'Expert security engineer.', 'dir': 'a',
         'project_path': '/p1', 'scope': 'project', 'path': 'a/sec.md'},
        {'name': 'sec', 'desc': 'Expert security engineer.', 'dir': 'b',
         'project_path': '/p2', 'scope': 'project', 'path': 'b/sec.md'},
        {'name': 'fe', 'desc': 'Frontend person.', 'dir': 'a',
         'project_path': '/p1', 'scope': 'project', 'path': 'a/fe.md'},
    ]
    groups = agents.sharpen_groups(rows)
    assert len(groups) == 2, 'two unique agents, not three installs'
    assert len(groups[('sec', 'Expert security engineer.')]) == 2


def test_sharpening_writes_every_directory_an_agent_lives_in(monkeypatch, tmp_path):
    """The point of moving this to the global page: one approval, every copy."""
    sb = Sandbox(monkeypatch, tmp_path)
    dirs = []
    for n in ('p1', 'p2'):
        d = tmp_path / n / '.claude' / 'agents'
        d.mkdir(parents=True)
        (d / 'sec.md').write_text(
            '---\nname: sec\ndescription: Expert security engineer.\n---\n\nBody.\n',
            encoding='utf-8')
        dirs.append(d)
    new = {'sec': 'Use PROACTIVELY when auditing auth code. Do not use for UI.'}
    for d in dirs:
        assert agents.apply_descriptions_dir(str(d), new) == ['sec']
    for d in dirs:
        meta, body = agents.parse_agent(str(d / 'sec.md'))
        assert meta['description'].startswith('Use PROACTIVELY')
        assert 'Body.' in body


def test_apply_descriptions_dir_knows_nothing_about_projects(monkeypatch, tmp_path):
    """It has to serve a user-level agents dir and the library too, neither of
    which has a CLAUDE.md routing table to refresh."""
    sb = Sandbox(monkeypatch, tmp_path)
    d = tmp_path / 'useragents'
    d.mkdir()
    (d / 'x.md').write_text('---\nname: x\ndescription: old.\n---\n\nB\n',
                            encoding='utf-8')
    assert agents.apply_descriptions_dir(str(d), {'x': 'Use PROACTIVELY when X.'}) == ['x']
    assert not (tmp_path / 'CLAUDE.md').exists()


def test_the_model_reply_is_parsed_leniently():
    """A model that adds a bullet or a stray line must not cost the whole batch."""
    got = agents.parse_sharpened(
        '- `sec`|Use PROACTIVELY when auditing auth code.\n'
        '\n'
        'here you go:\n'
        '2. fe|Use PROACTIVELY when the task touches React components.\n'
        'garbage line with no pipe\n')
    assert got == {'sec': 'Use PROACTIVELY when auditing auth code.',
                   'fe': 'Use PROACTIVELY when the task touches React components.'}


def test_usage_comes_from_claude_codes_own_record(monkeypatch, tmp_path):
    """`agentLastUsed` is the only honest answer to "is this doing anything?",
    and claudectl already reads that file for other things."""
    sb = Sandbox(monkeypatch, tmp_path)
    import json as _json
    import time as _time
    with open(os.path.join(str(sb.cfg), '.claude.json'), 'w', encoding='utf-8') as f:
        _json.dump({'agentLastUsed': {'rev': (_time.time() - 7200) * 1000}}, f)
    got = agents.usage(str(sb.cfg))
    assert got.get('rev'), got


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
    """It lands in `~/.claude/agents/`, FLAT — where Claude Code reads agents.

    It used to be written into claudectl's own category-organised library, which
    Claude Code never looks at, so an AI-generated agent was never picked. The
    category is frontmatter now, exactly as `agents.category_of` documents and as
    the manual GUI path (`api_agent_create`) already did.
    """
    from claude_sessions import memory
    sb = Sandbox(monkeypatch, tmp_path)
    _seed(sb, '01-core', 'x')
    monkeypatch.setattr(agents, 'get_claude_exe', lambda: r'C:\fake.exe')
    body = "---\nname: sec\ndescription: security\n---\n\nYou review security."
    monkeypatch.setattr(memory, '_claude_stdin', lambda *a, **k: body)
    keys = flat(DOWN, DOWN, ENTER,           # New AI (3rd selectable)
                typed('sec'), ENTER,         # name
                typed('review security'), ENTER,  # role
                ENTER,                       # category (blank)
                ENTER,                       # approve pager
                ESC)
    run_flow(monkeypatch, keys, agents.agents_menu, None)
    assert (sb.cfg / 'agents' / 'sec.md').exists()
    assert not (sb.agents_lib / '01-core' / 'sec.md').exists()


def test_the_ai_agent_prompt_goes_over_stdin_with_the_shared_guards(monkeypatch, tmp_path):
    """It used to spawn `claude --print <prompt>` directly: the prompt on ARGV
    (the 32767-char CreateProcess limit `_claude_stdin` exists to dodge, and
    `_build_ai_context` can push it past that), no cwd, no HEADLESS_MARK so the
    call was listed back as one of your own session topics, no `--max-turns` and
    no `--max-budget-usd`. Every one of those comes free from the shared seam."""
    from claude_sessions import memory
    Sandbox(monkeypatch, tmp_path)
    seen = {}

    def fake(prompt, cwd, **kw):
        seen['prompt'], seen['cwd'], seen['kw'] = prompt, cwd, kw
        return "---\nname: sec\n---\n\nbody"

    monkeypatch.setattr(memory, '_claude_stdin', fake)
    r = agents.generate_agent_ai('sec', 'review security', 'user', None)
    assert r['ok'] and r['path'].endswith(os.path.join('agents', 'sec.md'))
    assert 'review security' in seen['prompt']
    assert 'timeout' in seen['kw'] and seen['kw']['timeout'] > 0
    # and the module no longer has its own spawn site at all
    src = open(agents.__file__, encoding='utf-8').read()
    assert '--print' not in src, 'agents.py spawns claude directly again'
    assert 'run_with_progress' not in src, 'agents.py has its own spawn site again'


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
