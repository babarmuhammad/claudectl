"""Code-review: JSON parsing, confidence filtering + severity ordering,
diff gathering, and render. The Claude call is stubbed — no real model runs.
"""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox
from claude_sessions import review, memory, config


# ── parsing ──────────────────────────────────────────────────

def test_parse_plain_array():
    out = '[{"file":"a.py","line":3,"confidence":90,"severity":"high","summary":"x"}]'
    f = review.parse_findings(out)
    assert len(f) == 1 and f[0]['file'] == 'a.py'


def test_parse_fenced_and_prose():
    out = 'Here are the issues:\n```json\n[{"file":"a","line":1,"confidence":80}]\n```\n'
    f = review.parse_findings(out)
    assert len(f) == 1 and f[0]['confidence'] == 80


def test_parse_findings_wrapper():
    out = '{"findings":[{"file":"a","line":1,"confidence":85}]}'
    f = review.parse_findings(out)
    assert len(f) == 1


def test_parse_empty_and_garbage():
    assert review.parse_findings('[]') == []
    assert review.parse_findings('not json at all') == []
    assert review.parse_findings('') == []


# ── run_review filtering + ordering ──────────────────────────

def _canned(monkeypatch, sb, findings_json, min_conf=80):
    import json
    s = dict(config._DEFAULT_SETTINGS)
    s['review_min_confidence'] = min_conf
    sb.settings.write_text(json.dumps(s), encoding='utf-8')
    monkeypatch.setattr(review, 'get_diff', lambda *a, **k: 'diff --git a b\n+changed')
    monkeypatch.setattr(review, 'gather_guidance', lambda *a, **k: '')
    monkeypatch.setattr(memory, '_claude_stdin', lambda *a, **k: findings_json)


def test_run_review_filters_low_confidence(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _canned(monkeypatch, sb,
            '[{"file":"a","line":1,"confidence":95,"severity":"high","summary":"real"},'
            ' {"file":"b","line":2,"confidence":40,"severity":"low","summary":"noise"}]')
    r = review.run_review(str(tmp_path), None, silent=True)
    assert not r['empty']
    assert len(r['findings']) == 1          # the 40% one dropped
    assert r['findings'][0]['summary'] == 'real'
    assert r['raw_count'] == 2


def test_run_review_orders_by_severity(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _canned(monkeypatch, sb,
            '[{"file":"a","line":1,"confidence":90,"severity":"low","summary":"lo"},'
            ' {"file":"b","line":2,"confidence":90,"severity":"critical","summary":"crit"},'
            ' {"file":"c","line":3,"confidence":90,"severity":"medium","summary":"med"}]')
    r = review.run_review(str(tmp_path), None, silent=True)
    sevs = [f['severity'] for f in r['findings']]
    assert sevs == ['critical', 'medium', 'low']


def test_run_review_empty_diff(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(review, 'get_diff', lambda *a, **k: '')
    called = []
    monkeypatch.setattr(memory, '_claude_stdin', lambda *a, **k: called.append(1) or '[]')
    r = review.run_review(str(tmp_path), None, silent=True)
    assert r['empty'] and r['findings'] == []
    assert not called                        # no Claude call on empty diff


def test_render_lines_reports_count(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _canned(monkeypatch, sb,
            '[{"file":"a.py","line":7,"confidence":99,"severity":"high",'
            '"summary":"bug here","detail":"fix it"}]')
    r = review.run_review(str(tmp_path), None, silent=True)
    from claude_sessions import render
    txt = render.strip_ansi('\n'.join(review.render_lines(r)))
    assert 'Found 1 issue' in txt and 'a.py:7' in txt and 'bug here' in txt


# ── real git diff ────────────────────────────────────────────

def _has_git():
    return shutil.which('git') is not None


@pytest.mark.skipif(not _has_git(), reason='git not installed')
def test_get_diff_against_head(tmp_path):
    repo = tmp_path / 'r'
    repo.mkdir()
    env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
    def run(*a):
        subprocess.run(['git', *a], cwd=str(repo), env=env,
                       capture_output=True, text=True)
    run('init'); run('config', 'user.email', 't@t'); run('config', 'user.name', 't')
    (repo / 'f.py').write_text('x = 1\n')
    run('add', '.'); run('commit', '-m', 'init')
    (repo / 'f.py').write_text('x = 2\n')          # unstaged change
    diff = review.get_diff(str(repo))
    assert '+x = 2' in diff and '-x = 1' in diff
