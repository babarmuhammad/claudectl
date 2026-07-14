"""F5 — account-accurate memory layer & multi-account functions."""

import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, make_jsonl

from claude_sessions import lessons, memory, claude_md, sessions, stats, workspace
from claude_sessions import config as config_mod


def _two_accounts(sb, tmp_path, monkeypatch, primary_sessions=1):
    """'alpha' with a session under default + a 'work' account with its own
    session for the same real path (same encoded name)."""
    actual, enc, folder, sids = sb.add_project('alpha', n_sessions=primary_sessions)
    other_cfg = tmp_path / 'other-cfg'
    other_folder = other_cfg / 'projects' / enc
    other_folder.mkdir(parents=True)
    accts = lambda: [('default', str(sb.cfg)), ('work', str(other_cfg))]
    monkeypatch.setattr(config_mod, 'all_config_dirs', accts)
    return actual, enc, folder, sids, str(other_cfg), str(other_folder)


def _mk_lesson_transcript(folder, sid, age_sec=300):
    p = os.path.join(folder, f'{sid}.jsonl')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'message': {'role': 'user',
                'content': 'fix the flaky retry logic in the client ' * 8}}) + '\n')
        f.write(json.dumps({'message': {'role': 'assistant',
                'content': [{'type': 'text', 'text': 'Added jitter to backoff. ' * 8}]}}) + '\n')
    t = time.time() - age_sec
    os.utime(p, (t, t))
    return p


def test_account_folders_for_finds_both(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids, ocfg, ofolder = _two_accounts(sb, tmp_path, monkeypatch)
    got = dict(sessions.account_folders_for(enc))
    assert os.path.normcase(got['default']) == os.path.normcase(folder)
    assert os.path.normcase(got['work']) == os.path.normcase(ofolder)


def test_pending_sids_spans_accounts(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids, ocfg, ofolder = _two_accounts(
        sb, tmp_path, monkeypatch, primary_sessions=0)
    _mk_lesson_transcript(folder, 'prim-sid')
    _mk_lesson_transcript(ofolder, 'work-sid')
    pend = lessons.pending_sids(folder, memory._empty())
    assert set(pend) == {'prim-sid', 'work-sid'}     # foreign session included


def test_lessons_learn_foreign_session_into_shared_graph(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids, ocfg, ofolder = _two_accounts(
        sb, tmp_path, monkeypatch, primary_sessions=0)
    _mk_lesson_transcript(ofolder, 'work-sid')       # only under the work account
    monkeypatch.setattr(memory, '_claude_stdin', lambda *a, **k: json.dumps({
        'lessons': [{'title': 'Backoff jitter', 'summary': 'add jitter to retry backoff',
                     'kind': 'error_fix', 'confidence': 0.9, 'files': []}]}))
    pend = lessons.pending_sids(folder, memory.load_memory(actual, folder))
    added, scanned = lessons.scan_sessions(actual, folder, pend)
    assert (added, scanned) == (1, 1)
    mem = memory.load_memory(actual, folder)         # shared, real-path store
    assert 'work-sid' in mem['lessons_scanned']
    assert any(e.get('name') == 'Backoff jitter' for e in mem['entities'])


def test_sessions_block_includes_foreign_topics(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids, ocfg, ofolder = _two_accounts(
        sb, tmp_path, monkeypatch, primary_sessions=1)
    make_jsonl(os.path.join(ofolder, 'work0000-0000-0000-0000-000000000000.jsonl'),
               title='', preview='foreign account topic xyz')
    block = claude_md._build_sessions_block(folder, {}, cap=0)
    assert 'foreign account topic xyz' in block


def test_usage_dashboard_merges_project_across_accounts(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids, ocfg, ofolder = _two_accounts(
        sb, tmp_path, monkeypatch, primary_sessions=1)
    make_jsonl(os.path.join(ofolder, 'work0000-0000-0000-0000-000000000000.jsonl'),
               preview='work session')
    # entries as main.run builds them: (mtime, actual_path, encoded_name, cfgdir)
    entries = [(1, actual, enc, str(sb.cfg)), (1, actual, enc, ocfg)]
    rows = [it for it in stats.iter_all_sessions(entries) if it]
    # dashboard keys per-project by enc → one merged row for two accounts
    per = {}
    for (mtime, ppath, e, sid, st, cfgdir) in rows:
        per.setdefault(e, {'sessions': 0})['sessions'] += 1
    assert len(per) == 1 and per[enc]['sessions'] == 2


def test_project_usage_screen_lists_both_accounts(monkeypatch, tmp_path):
    from harness import ESC, run_flow
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids, ocfg, ofolder = _two_accounts(
        sb, tmp_path, monkeypatch, primary_sessions=1)
    make_jsonl(os.path.join(ofolder, 'work0000-0000-0000-0000-000000000000.jsonl'),
               title='WorkAcctSession', preview='work session')
    _ret, cap, _ex = run_flow(monkeypatch, [*ESC],
                              stats.project_usage_screen, folder, 'alpha')
    plain = cap.plain
    assert 'Session 0' in plain                       # primary account session
    assert 'WorkAcctSession' in plain and '[work]' in plain


def test_load_recent_sessions_merges_stores(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids, ocfg, ofolder = _two_accounts(
        sb, tmp_path, monkeypatch, primary_sessions=1)
    work_sid = 'work0000-0000-0000-0000-000000000000'
    make_jsonl(os.path.join(ofolder, f'{work_sid}.jsonl'), preview='work')
    # primary store
    sb.write_last_sessions([{'project_path': actual, 'encoded_name': enc,
                             'session_id': sids[0], 'cfgdir': str(sb.cfg),
                             'timestamp': 100}])
    # work-account store
    with open(os.path.join(ocfg, 'projects', 'last-session.json'), 'w', encoding='utf-8') as f:
        json.dump([{'project_path': actual, 'encoded_name': enc,
                    'session_id': work_sid, 'cfgdir': ocfg, 'timestamp': 200}], f)
    recent = sessions.load_recent_sessions(5)
    sids_got = {e['session_id'] for e in recent}
    assert work_sid in sids_got and sids[0] in sids_got
    assert recent[0]['session_id'] == work_sid        # newest (ts 200) first


def test_workspace_stats_count_both_accounts(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids, ocfg, ofolder = _two_accounts(
        sb, tmp_path, monkeypatch, primary_sessions=2)
    for i in range(3):
        make_jsonl(os.path.join(ofolder, f'work000{i}-0000-0000-0000-00000000000{i}.jsonl'),
                   preview=f'w{i}')
    man = workspace.update_manifest(actual, folder, 'test')
    assert man['sessions']['analyzed_count'] == 5     # 2 primary + 3 work
