import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_sessions.main import build_choice_line
from claude_sessions.config import config_dir
from claude_sessions.sessions import get_session_stats
from claude_sessions.transcript import _slug, iter_transcript
from claude_sessions.stats import estimate_cost, fmt_tok
from claude_sessions.session_menu import _sid_of, _move_session


OPTS = {'effort': '', 'model': '', 'perm': '', 'name': '', 'worktree': ''}


def test_choice_line_v3_sentinels():
    line = build_choice_line(r'D:\proj', 'D--proj', 'new', dict(OPTS))
    assert line == rf'v3|D:\proj|D--proj|new|-|-|-|-|-|{config_dir}'


def test_choice_line_empty_effort_set_model():
    # the regression that motivated v2: empty effort + set model must not shift
    o = dict(OPTS, model='sonnet-4-6')
    line = build_choice_line(r'D:\proj', 'D--proj', 'new', o)
    parts = line.split('|')
    assert parts[4] == '-'             # effort stays empty sentinel
    assert parts[5] == 'sonnet-4-6'    # model in model slot


def test_choice_line_full():
    o = dict(OPTS, effort='high', model='fable-5', perm='plan',
             name='My Sess', worktree='*')
    line = build_choice_line(r'D:\p', 'D--p', 'new', o)
    assert line == rf'v3|D:\p|D--p|new|high|fable-5|plan|My Sess|*|{config_dir}'


def test_sid_of():
    assert _sid_of('resume:abc-123') == 'abc-123'
    assert _sid_of('resume-named::abc-123::Some Name') == 'abc-123'
    assert _sid_of('new') is None
    assert _sid_of(None) is None
    assert _sid_of('terminal') is None


def test_slug():
    assert _slug('My Cool Session!') == 'My-Cool-Session'
    assert _slug('***') == 'session'
    assert len(_slug('x' * 100)) <= 40


def _write_jsonl(path, objs):
    with open(path, 'w', encoding='utf-8') as f:
        for o in objs:
            f.write(json.dumps(o) + '\n')


def test_session_stats_usage(tmp_path):
    p = tmp_path / 's.jsonl'
    _write_jsonl(p, [
        {'role': 'user', 'content': 'hello there friend',
         'timestamp': '2026-06-12T10:00:00Z', 'gitBranch': 'main', 'cwd': 'D:\\x'},
        {'type': 'assistant', 'message': {
            'role': 'assistant', 'model': 'claude-sonnet-4-6',
            'usage': {'input_tokens': 100, 'output_tokens': 200,
                      'cache_read_input_tokens': 1000,
                      'cache_creation_input_tokens': 50},
            'content': [{'type': 'text', 'text': 'hi'}]},
         'timestamp': '2026-06-12T10:05:00Z'},
    ])
    s = get_session_stats(str(p))
    assert s['count'] == 2
    assert s['models'] == ['claude-sonnet-4-6']
    u = s['usage_by_model']['claude-sonnet-4-6']
    assert (u['in'], u['out'], u['cache_read'], u['cache_create']) == (100, 200, 1000, 50)
    assert s['branch'] == 'main'
    assert s['last_ts'] - s['first_ts'] == 300
    assert s['api_errors'] == 0


def test_estimate_cost_math():
    usage = {'claude-sonnet-4-6': {'in': 1_000_000, 'out': 1_000_000,
                                   'cache_read': 0, 'cache_create': 0}}
    cost, exact = estimate_cost(usage)
    assert exact is True
    assert abs(cost - 18.0) < 1e-6     # 3 + 15


def test_estimate_cost_cache_terms():
    usage = {'claude-sonnet-4-6': {'in': 0, 'out': 0,
                                   'cache_read': 1_000_000, 'cache_create': 1_000_000}}
    cost, _ = estimate_cost(usage)
    assert abs(cost - (3 * 0.1 + 3 * 1.25)) < 1e-6


def test_estimate_cost_unknown_model_flagged():
    cost, exact = estimate_cost({'mystery-9': {'in': 1000, 'out': 0,
                                               'cache_read': 0, 'cache_create': 0}})
    assert exact is False


def test_fmt_tok():
    assert fmt_tok(999) == '999'
    assert fmt_tok(12_300) == '12.3k'
    assert fmt_tok(4_500_000) == '4.5M'


def test_iter_transcript_filters(tmp_path):
    p = tmp_path / 's.jsonl'
    _write_jsonl(p, [
        {'role': 'user', 'content': 'real question here'},
        {'role': 'user', 'content': '<system-reminder> noise'},
        {'type': 'assistant', 'message': {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'real answer'},
            {'type': 'tool_use', 'name': 'Bash', 'input': {}}]}},
        {'role': 'assistant', 'isApiErrorMessage': True,
         'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'err'}]}},
    ])
    msgs = iter_transcript(str(p))
    assert len(msgs) == 2
    assert msgs[0]['role'] == 'user' and 'real question' in msgs[0]['text']
    assert msgs[1]['role'] == 'assistant' and msgs[1]['text'] == 'real answer'


def test_move_session_roundtrip(tmp_path):
    live = tmp_path / 'proj'
    arch = tmp_path / 'proj' / 'archived'
    live.mkdir()
    (live / 'abc.jsonl').write_text('{"role":"user","content":"hi there you"}\n',
                                    encoding='utf-8')
    (live / 'abc.name').write_text('My Name', encoding='utf-8')

    errs = _move_session(str(live), str(arch), 'abc')
    assert errs == []
    assert (arch / 'abc.jsonl').exists() and (arch / 'abc.name').exists()
    assert not (live / 'abc.jsonl').exists()

    errs = _move_session(str(arch), str(live), 'abc')
    assert errs == []
    assert (live / 'abc.jsonl').exists()
