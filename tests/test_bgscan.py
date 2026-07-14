"""F8 — background memory update: detached worker, incremental/atomic saves,
cross-process scan lock."""

import os
import sys
import json
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, ESC, run_flow

from claude_sessions import lessons, memory
from claude_sessions import main as main_mod


def _mk_transcript(folder, sid, age_sec=300):
    p = os.path.join(folder, f'{sid}.jsonl')
    msgs = [
        {'message': {'role': 'user', 'content': 'fix the parser timeout bug ' * 10}},
        {'message': {'role': 'assistant',
                     'content': [{'type': 'text', 'text': 'Fixed with backoff. ' * 10}]}},
    ]
    with open(p, 'w', encoding='utf-8') as f:
        for m in msgs:
            f.write(json.dumps(m) + '\n')
    t = time.time() - age_sec
    os.utime(p, (t, t))


def _lesson_payload(title):
    return json.dumps({'lessons': [
        {'title': title, 'summary': f'durable fact about {title}',
         'kind': 'decision', 'confidence': 0.5, 'files': []}]})


# ── atomic save ──────────────────────────────────────────────

def test_save_memory_atomic_on_dump_failure(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    mem = memory._empty()
    mem['entities'] = [{'name': 'Keep', 'type': 'component'}]
    assert memory.save_memory(actual, folder, mem)

    def boom(*a, **k):
        raise RuntimeError('disk full')
    real_dump = memory.json.dump
    monkeypatch.setattr(memory.json, 'dump', boom)
    mem2 = memory._empty()
    assert memory.save_memory(actual, folder, mem2) is False
    monkeypatch.setattr(memory.json, 'dump', real_dump)
    # original graph intact — no torn/empty file replaced it
    got = memory.load_memory(actual, folder)
    assert got['entities'] and got['entities'][0]['name'] == 'Keep'


# ── incremental lesson saves ─────────────────────────────────

def test_lessons_scan_saves_after_each_transcript(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    _mk_transcript(folder, 'sid-one')
    _mk_transcript(folder, 'sid-two', age_sec=200)
    calls = []

    def fake_claude(prompt, *a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('killed mid-scan')
        return _lesson_payload('First lesson')
    monkeypatch.setattr(memory, '_claude_stdin', fake_claude)

    try:
        lessons.scan_sessions(actual, folder, ['sid-one', 'sid-two'])
    except RuntimeError:
        pass
    mem = memory.load_memory(actual, folder)
    # transcript 1's work survived the interruption and won't re-scan
    assert 'sid-one' in mem['lessons_scanned']
    assert any(e.get('name') == 'First lesson' for e in mem['entities'])
    assert 'sid-two' not in mem['lessons_scanned']


# ── scan lock ────────────────────────────────────────────────

def test_scan_lock_acquire_guard_and_clear(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    assert memory.scan_lock_status(actual) is None
    assert memory.acquire_scan_lock(actual) is True          # own live lock
    try:
        assert memory.scan_lock_status(actual) == ''
        assert memory.acquire_scan_lock(actual) is False     # held → refused
        memory._report_progress('lessons 2/5')
        assert memory.scan_lock_status(actual) == 'lessons 2/5'
    finally:
        memory.clear_scan_lock(actual)
    assert memory.scan_lock_status(actual) is None


def test_stale_scan_lock_is_ignored_and_removed(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    p = memory._scan_lock_path(actual)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:                # long-dead worker
        json.dump({'pid': os.getpid(), 'started': 'x',
                   'updated': time.time() - 9999, 'progress': 'memory 1/3'}, f)
    assert memory.scan_lock_status(actual) is None
    assert not os.path.isfile(p)                             # stale lock removed
    assert memory.acquire_scan_lock(actual) is True
    memory.clear_scan_lock(actual)


# ── detached spawner ─────────────────────────────────────────

def test_spawn_background_worker_detached_args(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    spawned = {}

    def fake_popen(args, **kw):
        spawned['args'] = args
        spawned['kw'] = kw
        return object()
    monkeypatch.setattr(subprocess, 'Popen', fake_popen)
    assert memory.spawn_background_worker(actual, folder) is not None
    assert '--bg-scan' in spawned['args']
    assert os.path.abspath(actual) in spawned['args']
    assert folder in spawned['args']
    assert spawned['args'][1:3] == ['-m', 'claude_sessions']
    want = (getattr(subprocess, 'DETACHED_PROCESS', 0)
            | getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert spawned['kw']['creationflags'] == want


def test_spawn_skipped_while_worker_lock_live(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('spawned')))
    assert memory.acquire_scan_lock(actual)
    try:
        assert memory.spawn_background_worker(actual, folder) is None
    finally:
        memory.clear_scan_lock(actual)


# ── worker CLI ───────────────────────────────────────────────

def test_bg_scan_cli_runs_lessons_then_refresh(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    _mk_transcript(folder, 'sid-one')
    with open(sb.settings, 'w', encoding='utf-8') as f:
        json.dump({'memory_lessons': 'auto', 'memory_auto_refresh': 'open'}, f)
    seed = memory._empty()
    seed['entities'] = [{'name': 'X', 'type': 'component'}]  # refresh precondition
    memory.save_memory(actual, folder, seed)

    order = []
    monkeypatch.setattr(lessons, 'scan_sessions',
                        lambda *a, **k: order.append('lessons') or (0, 1))
    monkeypatch.setattr(memory, 'refresh_memory',
                        lambda *a, **k: order.append('refresh') or seed)
    main_mod._bg_scan_cli(actual, folder)
    assert order == ['lessons', 'refresh']                   # serialized, in order
    assert memory.scan_lock_status(actual) is None           # lock released


def test_bg_scan_cli_lock_released_on_failure(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    _mk_transcript(folder, 'sid-one')
    with open(sb.settings, 'w', encoding='utf-8') as f:
        json.dump({'memory_lessons': 'auto'}, f)

    def boom(*a, **k):
        raise RuntimeError('worker crash')
    monkeypatch.setattr(lessons, 'scan_sessions', boom)
    main_mod._bg_scan_cli(actual, folder)                    # must not raise
    assert memory.scan_lock_status(actual) is None


def test_run_dispatches_bg_scan(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    got = []
    monkeypatch.setattr(main_mod, '_bg_scan_cli', lambda p, f: got.append((p, f)))
    monkeypatch.setattr(sys, 'argv', ['claudectl', '--bg-scan', actual, folder])
    main_mod.run()
    assert got == [(actual, folder)]


# ── badge ────────────────────────────────────────────────────

def test_sessions_menu_badge_shows_worker_progress(monkeypatch, tmp_path):
    from claude_sessions.session_menu import sessions_menu
    from claude_sessions.sessions import scan_sessions as scan
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=1)
    assert memory.acquire_scan_lock(actual)
    try:
        memory._report_progress('lessons 3/9')
        sess = scan(folder)
        _ret, cap, _ex = run_flow(monkeypatch, [*ESC], sessions_menu,
                                  sess, folder, 'alpha', actual)
        assert 'memory updating' in cap.plain
        assert 'lessons 3/9' in cap.plain
    finally:
        memory.clear_scan_lock(actual)
