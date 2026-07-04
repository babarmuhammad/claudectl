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


def test_deleted_unit_entities_dropped(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'mod1/a.py')
    _mkfile(actual, 'mod2/b.py')
    _stub(monkeypatch)
    memory.refresh_memory(actual, folder, 'alpha')
    assert any(e['repo'] == 'mod2' for e in memory.load_memory(actual, folder)['entities'])
    import shutil
    shutil.rmtree(os.path.join(actual, 'mod2'))
    mem = memory.refresh_memory(actual, folder, 'alpha')
    assert not any(e['repo'] == 'mod2' for e in mem['entities'])


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
    assert all(e['module'] != 'legacy-key' for e in mem2['entities'])


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


def test_micro_digest_counts_lessons(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    mem = {'entities': [
        {'name': 'Engine', 'type': 'module', 'summary': 'core', 'repo': 'svc', 'module': 'engine'},
        {'name': 'L1', 'type': 'lesson', 'status': 'approved', 'summary': 'x', 'repo': '', 'module': ''},
        {'name': 'L2', 'type': 'lesson', 'status': 'pending', 'summary': 'y', 'repo': '', 'module': ''}],
        'summaries': {'svc/engine': 'the engine'}, 'relations': []}
    d = memory.build_digest_micro(mem)
    assert 'lessons: 1 learned' in d                          # pending excluded
    assert 'L1' not in d


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
