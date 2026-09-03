import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox

from claude_sessions import memory, connections


def _mkfile(base, rel, content='x = 1\n'):
    p = os.path.join(base, rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)
    return p


def _stub(monkeypatch, calls=None):
    """Stub Claude extraction: one entity named after the unit, so coverage is
    checkable. Records the units it was called for."""
    def fake(corpus, cwd, unit='', progress=''):
        if calls is not None:
            calls.append(unit)
        return {'summary': f'summary of {unit}',
                'entities': [{'name': f'E[{unit}]', 'type': 'module', 'summary': 's'}],
                'relations': []}
    monkeypatch.setattr(memory, '_extract', fake)


# ── persistence ──────────────────────────────────────────────

def test_migrate_has_summaries(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    m = memory._migrate({'entities': []})
    assert 'summaries' in m and m['schema_version'] == memory.SCHEMA_VERSION


# ── whole-project coverage ───────────────────────────────────

def test_refresh_covers_every_unit(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _mkfile(actual, 'mod2/b.py')
    calls = []
    _stub(monkeypatch, calls)
    mem = memory.refresh_memory(actual, folder, 'alpha')
    repos = {e['repo'] for e in mem['entities']}
    assert repos == {'mod1', 'mod2'}                 # every top-level unit covered
    assert set(calls) == {'mod1/(root)', 'mod2/(root)'}
    assert mem['summaries']                           # per-unit summaries stored


def test_a_subproject_with_its_own_manifest_is_its_own_section(monkeypatch, tmp_path):
    """A section of the graph was a git repo, so a Next.js app dropped into a
    project was folded into the parent's cluster and flattened into its module
    keys — no summary of its own, no rule file, no digest bullet."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    os.makedirs(os.path.join(actual, '.git'))            # the PARENT is the repo
    _mkfile(actual, 'web/package.json', '{}\n')
    _mkfile(actual, 'web/app/page.ts')
    _mkfile(actual, 'srv/x.py')
    _stub(monkeypatch)
    mem = memory.refresh_memory(actual, folder, 'alpha')

    units = {(r, m) for r, m, _fs in memory._units(actual, folder)}
    assert ('web', 'app') in units, 'modules are relative to the subproject'
    assert ('alpha', 'srv') in units, 'the rest still belongs to the parent repo'
    assert 'web' in mem['repo_summaries']
    assert '**web**' in memory.build_digest_micro(mem)


def test_a_one_file_directory_is_not_a_module_of_its_own(monkeypatch, tmp_path):
    """A unit costs a Claude call and a rule file. Once a subproject became its
    own cluster its module keys turned relative to IT, and an app-router tree —
    one directory per page — split into twenty units of one file each."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'web/package.json', '{}\n')
    for page in ('about', 'faq', 'download'):
        _mkfile(actual, f'web/app/{page}/page.ts')
    for i in range(3):
        _mkfile(actual, f'web/lib/m{i}.ts')
    units = {m: len(fs) for r, m, fs in memory._units(actual, folder) if r == 'web'}
    assert units == {'app': 3, 'lib': 3}, 'the page dirs fold, the module stays'


def test_a_manifest_at_the_project_root_does_not_recluster_it(monkeypatch, tmp_path):
    """A subproject is UNDER the root. Counting the root itself would relabel
    every file of a non-git project from its top-level dir to the root's name."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'pyproject.toml', '[project]\n')
    _mkfile(actual, 'mod1/a.py')
    assert {r for r, _m, _fs in memory._units(actual, folder)} == {'mod1'}


def test_incremental_only_changed_unit(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _mkfile(actual, 'mod2/b.py')
    calls = []
    _stub(monkeypatch, calls)
    memory.refresh_memory(actual, folder, 'alpha')
    assert len(calls) == 2
    calls.clear()
    memory.refresh_memory(actual, folder, 'alpha')   # nothing changed
    assert calls == []
    _mkfile(actual, 'mod1/a.py', 'changed = True\n')  # only mod1 changes
    memory.refresh_memory(actual, folder, 'alpha')
    assert calls == ['mod1/(root)']


def test_deleted_unit_entities_invalidated(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _mkfile(actual, 'mod2/b.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')
    assert any(e['repo'] == 'mod2' and e.get('valid', True)
               for e in memory.load_memory(actual, folder)['entities'])
    import shutil
    shutil.rmtree(os.path.join(actual, 'mod2'))
    mem = memory.refresh_memory(actual, folder, 'alpha')
    # temporal: deleted-unit facts are INVALIDATED (history), not deleted, and
    # never surface as valid
    assert not any(e['repo'] == 'mod2' and e.get('valid', True) for e in mem['entities'])
    assert any(e['repo'] == 'mod2' and not e.get('valid', True) for e in mem['entities'])


# ── module granularity (v2) ──────────────────────────────────

def test_module_of_splits_single_package_repo(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    # git repo at project root → modules must split by dir, not collapse
    os.makedirs(os.path.join(actual, '.git'), exist_ok=True)
    _mkfile(actual, 'claude_sessions/memory.py')
    _mkfile(actual, 'claude_sessions/ui.py')
    _mkfile(actual, 'tests/test_x.py')
    calls = []
    _stub(monkeypatch, calls)
    mem = memory.refresh_memory(actual, folder, 'alpha')
    mods = {e['module'] for e in mem['entities']}
    assert 'claude_sessions' in mods and 'tests' in mods


def test_key_drift_forces_reextract(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    calls = []
    _stub(monkeypatch, calls)
    memory.refresh_memory(actual, folder, 'alpha')
    # simulate v1 legacy keys: entity exists but under an old module key
    mem = memory.load_memory(actual, folder)
    for e in mem['entities']:
        e['module'] = 'legacy-key'
    memory.save_memory(actual, folder, mem)
    calls.clear()
    mem2 = memory.refresh_memory(actual, folder, 'alpha')   # hashes unchanged
    assert calls == ['mod1/(root)']                          # drift → re-extract
    # the current module is re-covered; the legacy-key fact survives only as
    # invalidated history, never as a valid entity
    assert any(e['module'] == 'mod1/(root)'.split('/')[0] or e['module'] == '(root)'
               for e in mem2['entities'] if e.get('valid', True))
    assert all(e['module'] != 'legacy-key'
               for e in mem2['entities'] if e.get('valid', True))


def test_module_edges_and_rank(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'lib/__init__.py', '')
    _mkfile(actual, 'lib/core.py', 'X = 1\n')
    _mkfile(actual, 'app/main.py', 'from lib import core\n')
    _stub(monkeypatch)
    mem = memory.refresh_memory(actual, folder, 'alpha')
    edges = {(e['source'], e['target']) for e in mem['module_edges']}
    assert ('app/(root)', 'lib/(root)') in edges
    ranked = {e['repo']: e.get('rank', 0) for e in mem['entities']}
    assert ranked.get('app', 0) > 0 and ranked.get('lib', 0) > 0


def test_pending_units_recorded(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _mkfile(actual, 'mod2/b.py')
    _mkfile(actual, 'mod3/c.py')
    monkeypatch.setattr('claude_sessions.config.load_settings',
                        lambda: {'memory_to_claudemd': False, 'memory_max_calls': 1})
    _stub(monkeypatch)
    mem = memory.refresh_memory(actual, folder, 'alpha')
    assert mem['pending_units'] == 2                         # coverage notice data


# ── temporal / rollup / reinforcement (v3) ───────────────────

def test_disappeared_entity_invalidated_on_reextract(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    # first extraction yields entity "Flask"
    monkeypatch.setattr(memory, '_extract', lambda *a, **k: {
        'summary': 's', 'entities': [{'name': 'Flask', 'type': 'component', 'summary': 'web'}],
        'relations': []})
    memory.refresh_memory(actual, folder, 'alpha')
    # file changes; new extraction replaces Flask with FastAPI (contradiction)
    _mkfile(actual, 'mod1/a.py', 'migrated = 1\n')
    monkeypatch.setattr(memory, '_extract', lambda *a, **k: {
        'summary': 's2', 'entities': [{'name': 'FastAPI', 'type': 'component', 'summary': 'web'}],
        'relations': []})
    mem = memory.refresh_memory(actual, folder, 'alpha')
    valid = {e['name'] for e in mem['entities'] if e.get('valid', True)}
    invalid = {e['name'] for e in mem['entities'] if not e.get('valid', True)}
    assert 'FastAPI' in valid and 'Flask' not in valid   # superseded
    assert 'Flask' in invalid and any(e.get('invalidated_at')
                                      for e in mem['entities'] if e['name'] == 'Flask')


def test_invalidated_not_injected(monkeypatch, tmp_path):
    from claude_sessions import recall
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    mem = memory._empty()
    mem['entities'] = [
        {'name': 'Flask', 'type': 'component', 'summary': 'old web framework flask',
         'repo': 'app', 'module': 'web', 'source_files': ['app/w.py'], 'valid': False,
         'invalidated_at': '2026-01-01'},
        {'name': 'FastAPI', 'type': 'component', 'summary': 'current web framework',
         'repo': 'app', 'module': 'web', 'source_files': ['app/w.py'], 'valid': True}]
    memory.save_memory(actual, folder, mem)
    r = recall.retrieve(actual, folder, 'web framework', budget_tokens=600)
    assert 'FastAPI' in r['text'] and 'Flask' not in r['text']


def test_rollup_summaries_built(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'svc/api/a.py')
    _mkfile(actual, 'svc/db/b.py')
    _stub(monkeypatch)                                   # summary = "summary of <unit>"
    mem = memory.refresh_memory(actual, folder, 'alpha')
    roll = mem.get('repo_summaries', {})
    assert 'svc' in roll and 'summary of' in roll['svc']


def test_hits_reinforced_on_recall(monkeypatch, tmp_path):
    from claude_sessions import recall
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    mem = memory._empty()
    mem['entities'] = [{'name': 'Parser', 'type': 'component', 'summary': 'parses usage',
                        'repo': 'app', 'module': 'x', 'source_files': ['app/x.py'],
                        'valid': True, 'hits': 0}]
    memory.save_memory(actual, folder, mem)
    recall.retrieve(actual, folder, 'usage parser', budget_tokens=600)

    # The graph itself is NOT rewritten on the per-prompt path: that cost two
    # atomic writes per prompt and two sessions in one project overwrote each
    # other's counts. The hit lands in an append-only sidecar...
    assert os.path.isfile(recall.hits_log_path(actual, folder))
    assert memory.load_memory(actual, folder)['entities'][0]['hits'] == 0

    # ...and is folded in by the next build, which is rewriting the graph anyway.
    m2 = memory.load_memory(actual, folder)
    assert recall.fold_hits(actual, folder, m2)
    assert m2['entities'][0]['hits'] == 1
    assert not os.path.isfile(recall.hits_log_path(actual, folder))


def test_concurrent_recalls_do_not_lose_each_others_counts(monkeypatch, tmp_path):
    """Two sessions in the same project, interleaved. The old read-modify-write
    of the whole graph meant the last writer won and the other count vanished."""
    from claude_sessions import recall
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    mem = memory._empty()
    mem['entities'] = [{'name': 'Parser', 'type': 'component', 'summary': 'parses usage',
                        'repo': 'app', 'module': 'x', 'source_files': ['app/x.py'],
                        'valid': True, 'hits': 0}]
    memory.save_memory(actual, folder, mem)
    for _ in range(5):
        recall.retrieve(actual, folder, 'usage parser', budget_tokens=600)
    m2 = memory.load_memory(actual, folder)
    recall.fold_hits(actual, folder, m2)
    assert m2['entities'][0]['hits'] == 5


def test_migrate_v2_adds_valid(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    m = memory._migrate({'schema_version': 2,
                         'entities': [{'name': 'X', 'type': 'component'}]})
    assert m['schema_version'] == 3
    assert m['entities'][0]['valid'] is True and m['entities'][0]['hits'] == 0
    assert 'repo_summaries' in m


# ── background refresh ───────────────────────────────────────

def test_background_refresh_runs_off_main_thread(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')          # seed memory
    _mkfile(actual, 'mod1/a.py', 'changed = 2\n')           # make it stale
    calls = []
    _stub(monkeypatch, calls)
    t = memory.start_background_refresh(actual, folder, 'alpha')
    assert t is not None
    t.join(timeout=10)
    assert calls == ['mod1/(root)']                          # refreshed in the thread


def test_background_refresh_noop_without_memory(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    assert memory.start_background_refresh(actual, folder, 'alpha') is None  # no graph yet


# ── digest ───────────────────────────────────────────────────

def test_micro_digest_within_budget(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    ents = [{'name': f'E{i}', 'type': 'module', 'summary': 'long summary ' * 12,
             'repo': f'repo{i % 7}', 'module': f'mod{i}'} for i in range(80)]
    mem = {'entities': ents,
           'summaries': {f'repo{i % 7}/mod{i}': 'unit summary ' * 10 for i in range(80)},
           'relations': []}
    d = memory.build_digest_micro(mem)
    assert memory.tokens_estimate(d) <= 250
    assert 'claudectl recall' in d                           # on-demand pointer
    assert 'E0' not in d                                     # no entity dump


def test_micro_digest_carries_the_lessons_themselves(monkeypatch, tmp_path):
    """The digest is the ONE memory surface read on every turn, and it was
    spending its budget on a module listing plus a lesson COUNT — "85 learned"
    tells a session nothing it can act on. The top lessons are named now;
    pending ones still never leave the review screen, and the entity dump this
    replaced stays gone."""
    Sandbox(monkeypatch, tmp_path)
    mem = {'entities': [
        {'name': 'Engine', 'type': 'module', 'summary': 'core', 'repo': 'svc', 'module': 'engine'},
        {'name': 'L1', 'type': 'lesson', 'status': 'approved', 'confidence': 0.9,
         'summary': 'retries need jitter', 'repo': '', 'module': ''},
        {'name': 'L2', 'type': 'lesson', 'status': 'pending', 'summary': 'y', 'repo': '', 'module': ''}],
        'summaries': {'svc/engine': 'the engine'}, 'relations': []}
    d = memory.build_digest_micro(mem)
    assert 'lessons: 1 learned' in d                          # pending excluded
    assert 'L1' in d and 'retries need jitter' in d
    assert 'L2' not in d, 'a pending lesson reached a session without review'


def test_micro_digest_carries_the_module_dependencies(monkeypatch, tmp_path):
    """`module_edges` is derived from real imports and went into the graph file
    and no further — yet which module leans on which is exactly the orientation
    a session lacks on turn one."""
    Sandbox(monkeypatch, tmp_path)
    mem = {'entities': [
        {'name': 'Engine', 'type': 'module', 'summary': 'core', 'repo': 'svc', 'module': 'engine'}],
        'summaries': {'svc/engine': 'the engine'}, 'relations': [],
        'module_edges': [{'source': 'svc/tests', 'target': 'svc/engine', 'weight': 40},
                         {'source': 'svc/cli', 'target': 'svc/engine', 'weight': 9}]}
    d = memory.build_digest_micro(mem)
    assert 'tests → engine' in d and 'cli → engine' in d
    assert 'svc/tests' not in d, 'the repo prefix is on every edge — pure repetition'
    assert memory.tokens_estimate(d) <= 250


# ── digest (full, kept for preview) ──────────────────────────

def test_build_digest_structured(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    mem = {'entities': [
        {'name': 'Engine', 'type': 'module', 'summary': 'core', 'repo': 'svc', 'module': 'engine'},
        {'name': 'Cache', 'type': 'component', 'summary': 'lru', 'repo': 'svc', 'module': 'engine'}],
        'summaries': {'svc/engine': 'the engine module'}, 'relations': []}
    d = memory.build_digest(mem)
    assert '### svc' in d and '**engine**' in d
    assert 'Engine' in d and 'Cache' in d and 'the engine module' in d


def test_refresh_writes_claudemd(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    monkeypatch.setattr('claude_sessions.config.load_settings',
                        lambda: {'memory_to_claudemd': True, 'memory_max_calls': None})
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')
    md = os.path.join(actual, 'CLAUDE.md')
    assert os.path.isfile(md) and 'CLAUDECTL:MEMORY' in open(md, encoding='utf-8').read()


# ── ask ──────────────────────────────────────────────────────

def test_ask_uses_answer(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    m = memory._empty()
    m['entities'] = [{'id': 'entity:svc:eng:Parser', 'name': 'Parser', 'type': 'component',
                      'summary': 'parses tokens', 'repo': 'svc', 'module': 'eng', 'source_files': []}]
    memory.save_memory(actual, folder, m)
    monkeypatch.setattr(memory, '_answer', lambda ctx, q, cwd: 'ANSWER:' + q)
    assert memory.ask_memory(actual, folder, 'what parses tokens') == 'ANSWER:what parses tokens'


# ── structured output (--json-schema) ─────────────────────────
# The old path recovered JSON from prose by stripping code fences and slicing
# between the first '{' and the last '}'. Claude Code can enforce the shape
# instead and hand back a validated object in `structured_output`.

def test_claude_json_asks_claude_code_to_enforce_the_schema(monkeypatch, tmp_path):
    import json as _json
    seen = {}

    def fake(prompt, cwd, **kw):
        seen.update(kw)
        return _json.dumps({'type': 'result', 'total_cost_usd': 0.0123,
                            'result': 'ignored prose',
                            'structured_output': {'summary': 'from schema'}})
    monkeypatch.setattr(memory, '_claude_stdin', fake)
    out = memory._claude_json('p', str(tmp_path), {'type': 'object'})
    argv = list(seen['extra_args'])
    assert argv[:2] == ['--output-format', 'json']
    assert argv[2] == '--json-schema' and _json.loads(argv[3]) == {'type': 'object'}
    # structured_output wins over the prose result, which is the whole point
    assert out == {'summary': 'from schema'}
    # and the envelope's real cost is recorded rather than estimated
    assert memory.last_call_cost == 0.0123


def test_claude_json_falls_back_when_there_is_no_structured_output(monkeypatch, tmp_path):
    """Claude Code before v2.1.205 silently ignored a schema it thought invalid
    and returned unstructured text. That must land on the OLD behaviour, not on
    nothing — otherwise upgrading claudectl breaks an older CLI."""
    import json as _json
    monkeypatch.setattr(memory, '_claude_stdin', lambda *a, **k: _json.dumps(
        {'type': 'result', 'result': 'Here you go:\n```json\n{"summary":"prose"}\n```'}))
    assert memory._claude_json('p', str(tmp_path), {}) == {'summary': 'prose'}
    # not even an envelope: raw prose straight out of an old CLI
    monkeypatch.setattr(memory, '_claude_stdin',
                        lambda *a, **k: 'sure!\n{"summary":"raw"}\n')
    assert memory._claude_json('p', str(tmp_path), {}) == {'summary': 'raw'}
    assert memory.last_call_cost is None


def test_the_fallback_parser_recovers_a_list_not_only_an_object():
    """Slicing '{'..'}' across a two-element array yields '{...}, {...}', which
    is not JSON — so a list-shaped answer used to come back as None."""
    two = '[{"a":1},{"b":2}]'
    assert memory._parse_json(two) == [{'a': 1}, {'b': 2}]
    assert memory._parse_json('here:\n```json\n' + two + '\n```') == [{'a': 1}, {'b': 2}]
    assert memory._parse_json('{"a":1}') == {'a': 1}
    assert memory._parse_json('no json here') is None


def test_a_spend_cap_reaches_the_headless_call(monkeypatch, tmp_path):
    import json as _json
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import config
    s = config.load_settings()
    assert memory._budget_args() == []          # 0 = off, the default
    s['headless_budget_usd'] = 2.5
    config.save_settings(s)
    assert memory._budget_args() == ['--max-budget-usd', '2.5']


# ── graph consolidation: the cap, the pin, and the snapshot ──
# Nothing exercised _consolidate before this: not the memory_max_entities cap,
# not the rank + hits*2 sort, not the cross-module merge, not _INVALID_CAP.
# It is the code that DELETES memory, which makes it the last place to have no
# test at all.

def _ent(name, rank=0, hits=0, **kw):
    e = {'name': name, 'type': 'component', 'summary': 'x' * 10, 'repo': 'r',
         'module': 'm', 'rank': rank, 'hits': hits, 'valid': True}
    e.update(kw)
    return e


def test_eviction_keeps_the_well_connected_and_names_what_it_dropped(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import config
    s = config.load_settings(); s['memory_max_entities'] = 3; config.save_settings(s)
    mem = memory._empty()
    mem['entities'] = [_ent('keep-rank', rank=99), _ent('keep-hits', hits=50),
                       _ent('keep-mid', rank=5),
                       _ent('drop-a'), _ent('drop-b')]
    got = memory._consolidate(mem)
    names = {e['name'] for e in got['entities']}
    assert {'keep-rank', 'keep-hits', 'keep-mid'} <= names
    assert 'drop-a' not in names and 'drop-b' not in names
    assert got['evicted_entities'] == 2
    # names, not just a count — a count is not something a user can check
    assert set(got['evicted_names']) == {'drop-a', 'drop-b'}


def test_a_pinned_entity_is_never_evicted(monkeypatch, tmp_path):
    """Pinning reached lessons only, so the cap could silently drop the very
    fact the user had marked as the one that matters."""
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import config
    s = config.load_settings(); s['memory_max_entities'] = 2; config.save_settings(s)
    mem = memory._empty()
    mem['entities'] = [_ent('pinned-nobody-uses', status='pinned'),
                       _ent('busy-1', rank=90), _ent('busy-2', rank=80),
                       _ent('busy-3', rank=70)]
    got = memory._consolidate(mem)
    names = {e['name'] for e in got['entities']}
    assert 'pinned-nobody-uses' in names, 'a pin outranks the cap'
    assert 'busy-1' in names


def test_pins_win_over_the_cap_rather_than_the_reverse(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import config
    s = config.load_settings(); s['memory_max_entities'] = 1; config.save_settings(s)
    mem = memory._empty()
    mem['entities'] = [_ent('p1', status='pinned'), _ent('p2', status='pinned'),
                       _ent('ordinary', rank=99)]
    got = memory._consolidate(mem)
    names = {e['name'] for e in got['entities']}
    assert {'p1', 'p2'} <= names, 'pinning more than the cap keeps them all'
    assert 'ordinary' not in names


def test_a_pin_survives_the_cross_module_merge(monkeypatch, tmp_path):
    """Two modules describing one thing merge into one record; the merged one
    must inherit the pin, or pinning the duplicate silently did nothing."""
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import config
    s = config.load_settings(); s['memory_max_entities'] = 1; config.save_settings(s)
    mem = memory._empty()
    mem['entities'] = [_ent('Auth Service', module='a'),
                       _ent('auth service', module='b', status='pinned'),
                       _ent('other', rank=99)]
    got = memory._consolidate(mem)
    merged = [e for e in got['entities'] if e['name'].lower() == 'auth service']
    assert len(merged) == 1, 'merged by normalized name'
    assert merged[0].get('status') == 'pinned'


def test_relations_to_evicted_entities_are_dropped(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import config
    s = config.load_settings(); s['memory_max_entities'] = 1; config.save_settings(s)
    mem = memory._empty()
    mem['entities'] = [_ent('kept', rank=99), _ent('gone')]
    mem['relations'] = [{'source': 'kept', 'target': 'gone', 'type': 'uses'}]
    got = memory._consolidate(mem)
    assert got['relations'] == []


def test_a_shrinking_save_is_snapshotted_and_restorable(monkeypatch, tmp_path):
    """The whole answer to 'if I compact or prune, do I lose my memory'."""
    from claude_sessions import diffview
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    big = memory._empty()
    big['entities'] = [_ent('a'), _ent('b'), _ent('c')]
    memory.save_memory(actual, folder, big)
    assert diffview.versions(actual, folder, 'memory_graph') == []

    small = memory._empty(); small['entities'] = [_ent('a')]
    memory.save_memory(actual, folder, small)
    vs = diffview.versions(actual, folder, 'memory_graph')
    assert vs, 'a save that loses entities snapshots the old graph'

    ok, _msg = diffview.restore(actual, folder, 'memory_graph', vs[0]['ts'])
    assert ok
    back = {e['name'] for e in memory.load_memory(actual, folder)['entities']}
    assert back == {'a', 'b', 'c'}


def test_growing_saves_are_not_snapshotted(monkeypatch, tmp_path):
    """Every refresh saves. Versioning all of them pushes the one snapshot that
    matters out of the ring within an hour."""
    from claude_sessions import diffview
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    m = memory._empty(); m['entities'] = [_ent('a')]
    memory.save_memory(actual, folder, m)
    for extra in ('b', 'c', 'd'):
        m['entities'].append(_ent(extra))
        memory.save_memory(actual, folder, m)
    assert diffview.versions(actual, folder, 'memory_graph') == []


# ── a cycle must never destroy what it failed to replace ─────
# The worst class of bug in this subsystem: refresh_memory advanced provenance
# for EVERY unit it hashed, not the units it actually extracted. A failed call
# or a max_calls truncation therefore marked unprocessed units "current", so
# the hash gate said unchanged forever and their stale facts were never
# revisited — while a failure additionally invalidated everything already known
# about that module.

def _stub_failing(monkeypatch, fail_units, calls=None):
    """Extraction that returns None (call failed) for the named units."""
    def fake(corpus, cwd, unit='', progress=''):
        if calls is not None:
            calls.append(unit)
        if unit in fail_units:
            return None
        return {'summary': f'summary of {unit}',
                'entities': [{'name': f'E[{unit}]', 'type': 'module', 'summary': 's'}],
                'relations': []}
    monkeypatch.setattr(memory, '_extract', fake)


def test_a_failed_extraction_keeps_the_facts_it_could_not_replace(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _mkfile(actual, 'mod2/b.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')

    # both modules change; mod1's call now fails
    _mkfile(actual, 'mod1/a.py', 'changed = 1\n')
    _mkfile(actual, 'mod2/b.py', 'changed = 2\n')
    _stub_failing(monkeypatch, {'mod1/(root)'})
    mem = memory.refresh_memory(actual, folder, 'alpha')

    m1 = [e for e in mem['entities'] if e['repo'] == 'mod1']
    assert m1, 'the failed module still has its entities'
    assert all(e.get('valid', True) for e in m1), \
        'a failed CALL is not evidence the facts became false'
    assert mem['summaries'].get('mod1/(root)'), 'its summary survives too'
    assert mem['pending_units'] >= 1, 'and it is counted as still owed'


def test_a_failed_unit_is_retried_next_cycle(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')

    _mkfile(actual, 'mod1/a.py', 'changed = 1\n')
    _stub_failing(monkeypatch, {'mod1/(root)'})
    memory.refresh_memory(actual, folder, 'alpha')
    # the file's hash must NOT have been recorded, or this is invisible forever
    assert memory.is_stale(actual, folder), 'a failed unit is still stale'

    calls = []
    _stub(monkeypatch, calls)
    memory.refresh_memory(actual, folder, 'alpha')
    assert calls == ['mod1/(root)'], 'and the next cycle picks it up'


def test_a_truncated_run_leaves_the_units_it_skipped_stale(monkeypatch, tmp_path):
    """memory_max_calls caps the work; it must not also declare the uncapped
    remainder up to date."""
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import config
    actual, enc, folder, _ = sb.add_project('alpha')
    for i in range(4):
        _mkfile(actual, f'mod{i}/a.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')

    for i in range(4):
        _mkfile(actual, f'mod{i}/a.py', f'changed = {i}\n')
    s = config.load_settings(); s['memory_max_calls'] = 2; config.save_settings(s)
    calls = []
    _stub(monkeypatch, calls)
    mem = memory.refresh_memory(actual, folder, 'alpha')
    assert len(calls) == 2, 'the cap held'
    assert mem['pending_units'] == 2
    assert memory.is_stale(actual, folder), 'the other two are still owed'

    calls.clear()
    memory.refresh_memory(actual, folder, 'alpha')
    assert len(calls) == 2, 'and the next run does them'
    assert not memory.is_stale(actual, folder)


def test_a_refresh_that_cannot_save_is_not_silent(monkeypatch, tmp_path):
    """One Claude call per unit was already paid for. Losing it quietly is the
    one outcome worse than not running."""
    import pytest
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _stub(monkeypatch)
    monkeypatch.setattr(memory, 'save_memory', lambda *a, **k: False)
    with pytest.raises(OSError):
        memory.refresh_memory(actual, folder, 'alpha')


def test_two_processes_cannot_both_hold_the_scan_lock(monkeypatch, tmp_path):
    """The claim and the test have to be one syscall. Read-then-write left a
    window where a GUI pass and a detached worker both proceeded."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    assert memory.acquire_scan_lock(actual) is True
    assert memory.acquire_scan_lock(actual) is False
    memory.clear_scan_lock(actual)
    assert memory.acquire_scan_lock(actual) is True
    memory.clear_scan_lock(actual)


def test_progress_goes_to_the_project_it_belongs_to(monkeypatch, tmp_path):
    """One module-global lock root meant two concurrent refreshes wrote each
    other's progress and the first to finish silenced both."""
    sb = Sandbox(monkeypatch, tmp_path)
    a, _e1, _f1, _ = sb.add_project('alpha')
    b, _e2, _f2, _ = sb.add_project('beta')
    assert memory.acquire_scan_lock(a) and memory.acquire_scan_lock(b)
    memory._report_progress('memory 1/9', a)
    memory._report_progress('memory 4/4', b)
    assert memory.scan_lock_status(a) == 'memory 1/9'
    assert memory.scan_lock_status(b) == 'memory 4/4'
    memory.clear_scan_lock(b)
    memory._report_progress('memory 2/9', a)
    assert memory.scan_lock_status(a) == 'memory 2/9', \
        'clearing one project must not silence the other'
    memory.clear_scan_lock(a)


def test_extract_reports_a_failed_call_as_failure_not_as_emptiness(monkeypatch, tmp_path):
    """The conversion the loop's guard depends on. `_claude_json` returns None
    for a timeout, a budget stop or unparseable output; turning that into an
    empty result is what made a failure look like 'this module has nothing'."""
    Sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(memory, '_claude_json', lambda *a, **k: None)
    assert memory._extract('some corpus', '.', unit='m/(root)') is None

    monkeypatch.setattr(memory, '_claude_json',
                        lambda *a, **k: {'summary': 's', 'entities': [], 'relations': []})
    got = memory._extract('some corpus', '.', unit='m/(root)')
    assert got == {'summary': 's', 'entities': [], 'relations': []}, \
        'a genuinely empty module is still a successful answer'


# ── the cycle has to make progress, not stall ────────────────

def test_a_capped_cycle_makes_partial_progress_and_converges(monkeypatch, tmp_path):
    """auto_cap used to be all-or-nothing: more changed units than the cap and
    the run did NOTHING, so the harder you worked the less memory updated."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    for i in range(5):
        _mkfile(actual, f'mod{i}/a.py')
    calls = []
    _stub(monkeypatch, calls)
    memory.refresh_memory(actual, folder, 'alpha')      # first build, uncapped
    for i in range(5):
        _mkfile(actual, f'mod{i}/a.py', f'changed = {i}\n')

    calls.clear()
    mem = memory.refresh_memory(actual, folder, 'alpha', auto_cap=2)
    assert len(calls) == 2, 'it does what it can'
    assert mem['pending_units'] == 3 and mem['last_extracted'] == 2

    seen = set(calls)
    for _ in range(4):                                   # further ticks
        if not memory.is_stale(actual, folder):
            break
        calls.clear()
        memory.refresh_memory(actual, folder, 'alpha', auto_cap=2)
        seen |= set(calls)
    assert not memory.is_stale(actual, folder), 'it converges'
    assert len(seen) == 5, 'and every changed unit was eventually done'


def test_auto_memory_can_bootstrap_an_empty_graph(monkeypatch, tmp_path):
    """is_stale returned False with no entities, so a project you had opted in
    stayed at zero entities forever and the first build had to be manual."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    assert memory.load_memory(actual, folder)['entities'] == []
    assert memory.is_stale(actual, folder) is True

    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha', auto_cap=2)
    assert memory.load_memory(actual, folder)['entities']


def test_an_empty_project_is_not_stale(monkeypatch, tmp_path):
    """…but a directory with no source is not 'owing a build' forever."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    assert memory.is_stale(actual, folder) is False


def test_the_budget_keeps_the_important_units_not_the_biggest(monkeypatch, tmp_path):
    """Units were taken in file-count order, so a cap kept the largest modules
    and dropped the entry point — and the coverage-repair units appended by
    _changed_units sat at the very end, first to be cut by the cap meant to
    catch up on them."""
    todo = [('r', 'big', ['f'] * 50), ('r', 'core', ['f']), ('r', 'new', ['f'] * 2)]
    mem = {'entities': [
        {'repo': 'r', 'module': 'big', 'rank': 1},
        {'repo': 'r', 'module': 'core', 'rank': 900},
    ]}
    order = [m for _r, m, _fs in memory._prioritise(todo, mem)]
    assert order[0] == 'new', 'a unit with no entities at all comes first'
    assert order[1] == 'core', 'then the most depended-on, not the biggest'
    assert order[2] == 'big'


def test_a_capped_cycle_actually_uses_the_priority_order(monkeypatch, tmp_path):
    """The ordering function is only worth having if refresh_memory calls it."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'core/a.py')                       # small
    for i in range(6):
        _mkfile(actual, f'big/f{i}.py')                # large
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')

    # make `core` the most depended-on unit, then change both
    mem = memory.load_memory(actual, folder)
    for e in mem['entities']:
        e['rank'] = 900 if e['repo'] == 'core' else 1
    memory.save_memory(actual, folder, mem)
    _mkfile(actual, 'core/a.py', 'changed = 1\n')
    for i in range(6):
        _mkfile(actual, f'big/f{i}.py', f'changed = {i}\n')

    calls = []
    _stub(monkeypatch, calls)
    memory.refresh_memory(actual, folder, 'alpha', auto_cap=1)
    assert calls == ['core/(root)'], \
        'one call must go to the important unit, not the largest one'


def test_the_more_recently_touched_module_goes_first(monkeypatch, tmp_path):
    """The last tiebreak was file COUNT, so a module written this afternoon lost
    to an equally-ranked one that merely had more files in it."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'aold/a.py')                         # first alphabetically…
    _mkfile(actual, 'aold/b.py')                         # …and the bigger one
    _mkfile(actual, 'znew/a.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')

    for rel in ('aold/a.py', 'aold/b.py', 'znew/a.py'):
        _mkfile(actual, rel, 'changed = 1\n')
    stale = os.path.getmtime(os.path.join(actual, 'znew', 'a.py')) - 86400
    for rel in ('aold/a.py', 'aold/b.py'):
        os.utime(os.path.join(actual, rel.replace('/', os.sep)), (stale, stale))

    calls = []
    _stub(monkeypatch, calls)
    memory.refresh_memory(actual, folder, 'alpha', auto_cap=1)
    assert calls == ['znew/(root)']


def test_a_new_unit_is_not_starved_by_what_you_are_editing(monkeypatch, tmp_path):
    """Never-covered units rank above covered ones but BELOW what you just
    edited, and the edit log is refilled by every turn you work — so a
    subproject added to a project under active development never landed."""
    from claude_sessions import memdirty_hook
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    for i in range(4):
        _mkfile(actual, f'known{i}/a.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')

    for i in range(4):                                   # everything you edited
        _mkfile(actual, f'known{i}/a.py', f'changed = {i}\n')
        memdirty_hook.record(actual, os.path.join(actual, f'known{i}', 'a.py'))
    _mkfile(actual, 'brandnew/a.py')                     # the thing you just added

    calls = []
    _stub(monkeypatch, calls)
    memory.refresh_memory(actual, folder, 'alpha', auto_cap=2)
    assert 'brandnew/(root)' in calls, 'half the budget is reserved for new units'
    assert any(c.startswith('known') for c in calls), 'and the rest still moves'


# ── the staleness sweep has to be cheap ──────────────────────

def test_an_unchanged_project_is_not_re_hashed(monkeypatch, tmp_path):
    """is_stale ran on every scheduler tick and every project open, and read
    every source file in full to SHA-256 it. Unchanged files are now settled by
    a stat."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    for i in range(4):
        _mkfile(actual, f'mod{i}/a.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')

    from claude_sessions import workspace
    hashed = []
    real = workspace._sha256_file
    monkeypatch.setattr(workspace, '_sha256_file',
                        lambda p: (hashed.append(p), real(p))[1])
    assert memory.is_stale(actual, folder) is False
    assert hashed == [], 'nothing changed, so nothing was read'

    _mkfile(actual, 'mod2/a.py', 'changed = 1\n')
    hashed.clear()
    assert memory.is_stale(actual, folder) is True
    assert len(hashed) == 1 and hashed[0].endswith('a.py'), \
        'only the file whose stat moved is read'


def test_a_touched_but_identical_file_costs_no_claude_call(monkeypatch, tmp_path):
    """The stat is a prefilter, not the answer — content stays the truth, or a
    `touch` would bill a model call per module."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    p = _mkfile(actual, 'mod1/a.py')
    calls = []
    _stub(monkeypatch, calls)
    memory.refresh_memory(actual, folder, 'alpha')
    calls.clear()

    os.utime(p, (12345, 12345))                  # mtime moves, bytes do not
    assert memory.is_stale(actual, folder) is False
    memory.refresh_memory(actual, folder, 'alpha')
    assert calls == []


def test_a_graph_written_before_signatures_existed_still_works(monkeypatch, tmp_path):
    """Provenance records with only a hash must not read as 'changed'."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')

    mem = memory.load_memory(actual, folder)
    mem['provenance'] = {rel: {'hash': v['hash']}      # strip the signatures
                         for rel, v in mem['provenance'].items()}
    memory.save_memory(actual, folder, mem)
    assert memory.is_stale(actual, folder) is False


# ── the edit signal ──────────────────────────────────────────

def test_the_edit_hook_records_what_claude_changed(monkeypatch, tmp_path):
    from claude_sessions import memdirty_hook
    cwd = str(tmp_path)
    memdirty_hook.record(cwd, os.path.join(cwd, 'a.py'))
    memdirty_hook.record(cwd, os.path.join(cwd, 'b.py'))
    assert memory.has_dirty(cwd) is True
    got = memory.drain_dirty(cwd)
    assert {os.path.basename(p) for p in got} == {'a.py', 'b.py'}
    assert memory.has_dirty(cwd) is False, 'draining clears it'
    assert memory.drain_dirty(cwd) == set()


def test_a_staleness_probe_does_not_eat_the_edit_signal(monkeypatch, tmp_path):
    """is_stale runs on every tick and every project open; if the probe drained
    the log, the refresh that followed would find nothing to prioritise."""
    from claude_sessions import memdirty_hook
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')

    memdirty_hook.record(actual, os.path.join(actual, 'mod1', 'a.py'))
    assert memory.is_stale(actual, folder) is True
    assert memory.is_stale(actual, folder) is True, 'probing twice is still true'
    assert memory.has_dirty(actual), 'the signal survives the probe'
    memory.refresh_memory(actual, folder, 'alpha')
    assert not memory.has_dirty(actual), 'the refresh consumes it'


def test_an_edited_unit_is_extracted_first(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import memdirty_hook
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    for i in range(4):
        _mkfile(actual, f'big/f{i}.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')

    _mkfile(actual, 'mod1/a.py', 'changed = 1\n')
    for i in range(4):
        _mkfile(actual, f'big/f{i}.py', f'changed = {i}\n')
    memdirty_hook.record(actual, os.path.join(actual, 'mod1', 'a.py'))

    calls = []
    _stub(monkeypatch, calls)
    memory.refresh_memory(actual, folder, 'alpha', auto_cap=1)
    assert calls == ['mod1/(root)'], 'the unit you just edited goes first'


def test_the_dirty_log_only_records_edit_tools(monkeypatch, tmp_path):
    """A PostToolUse payload for a Read or a Bash must not mark anything."""
    from claude_sessions import memdirty_hook
    cwd = str(tmp_path)
    assert memdirty_hook.record(cwd, '') == 0
    assert memory.has_dirty(cwd) is False


def test_memory_spend_accumulates(monkeypatch, tmp_path):
    """`last_cost_usd` only ever held the MOST RECENT cycle — the next cycle
    overwrote it, so what memory has cost over its life was unmeasurable."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _mkfile(actual, 'mod2/b.py')

    def fake(corpus, cwd, unit='', progress=''):
        memory.last_call_cost = 0.01          # what a real envelope records
        return {'summary': f'summary of {unit}',
                'entities': [{'name': f'E[{unit}]', 'type': 'module', 'summary': 's'}],
                'relations': []}
    monkeypatch.setattr(memory, '_extract', fake)

    mem = memory.refresh_memory(actual, folder, 'alpha')
    assert round(mem['cost_usd_total'], 4) == 0.02      # two units
    assert mem['cost_history'] == [0.01, 0.01]

    _mkfile(actual, 'mod1/a.py', 'changed = True\n')
    mem = memory.refresh_memory(actual, folder, 'alpha')
    assert round(mem['cost_usd_total'], 4) == 0.03, 'the total was overwritten, not added to'
    assert mem['last_cost_usd'] == 0.01, 'the per-cycle figure must still be per-cycle'

    # the ring is bounded — it feeds a sparkline, not an audit log
    for _ in range(40):
        memory.charge(mem)
    assert len(mem['cost_history']) == memory.COST_HISTORY


def test_an_old_graph_gains_the_cost_keys(monkeypatch, tmp_path):
    """No SCHEMA_VERSION bump: _migrate setdefaults every _empty() key, so a
    graph written before the accumulator existed reads back with it at zero."""
    Sandbox(monkeypatch, tmp_path)
    m = memory._migrate({'entities': [], 'schema_version': 3})
    assert m['cost_usd_total'] == 0.0 and m['cost_history'] == []


def test_the_dirty_log_can_be_counted_without_draining(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    assert memory.dirty_count(actual) == 0
    p = memory.dirty_log_path(actual)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write('a.py\nb.py\n\n')
    assert memory.dirty_count(actual) == 2
    assert memory.dirty_count(actual) == 2, 'counting drained the signal'
    assert memory.drain_dirty(actual)                  # drain is still the remover
