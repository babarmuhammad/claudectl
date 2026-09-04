"""One reader, one cache ladder: memory -> disk -> parse.

The disk cache (`stats.claudectl-stats-cache.json`, ~1 MB on a real machine)
existed but was only reachable through `stats.get_session_stats_cached`, and on
the cold path nothing reached it: `gui.list_sessions` calls `scan_sessions`
FIRST, which full-parses every transcript through `sessions._parse_session`, and
only then calls the cached accessor — by which point the accessor can only ever
hit the in-memory cache the parse just warmed.

So every GUI restart re-parsed a project's entire transcript corpus on the
request thread. Live evidence: `GET /api/sessions` at an 8.06 s median and a
13.6 s worst case in `~/.claude/claudectl-events.jsonl`, against ~620 MB of
transcripts for the largest project on this machine.

The ladder lives in the parser now, which is the only thing that reads a
transcript — same discipline as `transcripts.iter_json` being the one reader of
the format.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from claude_sessions import sessions, stats, transcripts


@pytest.fixture
def _cache_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(stats, 'cache_file', str(tmp_path / 'stats-cache.json'))
    monkeypatch.setattr(stats, '_disk_cache', None, raising=False)
    monkeypatch.setattr(stats, '_cache_dirty', False, raising=False)
    sessions._info_cache.clear()
    return tmp_path


def _transcript(tmp_path, name='s.jsonl'):
    p = tmp_path / name
    p.write_text(
        json.dumps({'type': 'user', 'message': {'role': 'user',
                    'content': 'refactor the parser please'}}) + '\n'
        + json.dumps({'type': 'assistant', 'message': {
            'role': 'assistant', 'model': 'claude-opus-5',
            'usage': {'input_tokens': 11, 'output_tokens': 7}}}) + '\n',
        encoding='utf-8')
    return str(p)


def test_the_parser_reads_the_disk_cache_not_just_the_process_one(_cache_in_tmp,
                                                                  monkeypatch):
    """The whole defect, stated as a test: with the in-memory cache empty — a
    fresh GUI process — a second parse must not touch the file."""
    path = _transcript(_cache_in_tmp)

    first = sessions.get_session_stats(path)
    assert first['count'] == 2 and first['preview']

    sessions._info_cache.clear()               # a new process
    def _boom(*a, **k):
        raise AssertionError('re-parsed a transcript the disk cache already had')
    monkeypatch.setattr(transcripts, 'iter_json', _boom)

    again = sessions.get_session_stats(path)
    assert again == first


def test_a_changed_transcript_is_reparsed(_cache_in_tmp):
    """The key is (mtime_ns, size), so appending a message must invalidate both
    caches — a stale hit here would be worse than the slow path it replaced."""
    path = _transcript(_cache_in_tmp)
    before = sessions.get_session_stats(path)['count']
    sessions._info_cache.clear()

    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'type': 'user', 'message': {
            'role': 'user', 'content': 'and now add a test for it'}}) + '\n')
    os.utime(path, (0, 0))                     # force a distinct mtime

    assert sessions.get_session_stats(path)['count'] == before + 1


def test_the_cached_accessor_and_the_parser_are_the_same_ladder(_cache_in_tmp):
    """`get_session_stats_cached` had its own copy of memory->disk->parse. Two
    implementations of one cache is two chances to disagree about the key — the
    lesson `gate['diff'] must be a list at EVERY producer` already records."""
    path = _transcript(_cache_in_tmp)
    assert stats.get_session_stats_cached(path) == sessions.get_session_stats(path)
    entry = stats._load_disk_cache().get(path)
    assert entry and entry['stats']['count'] == 2, entry


def test_scan_sessions_warms_the_disk_cache(_cache_in_tmp):
    """`scan_sessions` is what runs first on the cold path, so it is the one that
    has to fill the cache — not the accessor called after it."""
    folder = _cache_in_tmp / 'proj'
    folder.mkdir()
    _transcript(folder, 'aaaaaaaa.jsonl')
    _transcript(folder, 'bbbbbbbb.jsonl')

    sessions.scan_sessions(str(folder))
    cached = stats._load_disk_cache()
    assert len(cached) == 2, cached
    assert stats._cache_dirty, 'nothing marked the cache for saving'


def test_a_schema_change_invalidates_a_cache_entry(_cache_in_tmp, monkeypatch):
    """The key is (mtime_ns, size), and a finished transcript never changes
    either — so without a schema number a value written by an older claudectl is
    served forever. Widening `preview` from 65 to 200 characters is exactly that
    case: it would have shown the new length only on sessions written after the
    upgrade, which reads as "it did not work" rather than as a cache.
    """
    path = _transcript(_cache_in_tmp)
    first = sessions.get_session_stats(path)
    assert first['count'] == 2

    sessions._info_cache.clear()
    monkeypatch.setattr(sessions, '_STATS_SCHEMA', sessions._STATS_SCHEMA + 1)
    hit, _got = sessions._disk_cache_hit(path, (os.stat(path).st_mtime_ns,
                                                os.stat(path).st_size,
                                                sessions._STATS_SCHEMA))
    assert not hit, 'a bumped schema still served the old entry'


def test_an_entry_missing_a_field_is_not_served(_cache_in_tmp):
    """The automatic half: a field ADDED to the stats dict does not need the
    number bumped, because an entry written before it exists has the wrong key
    set — and `s['preview']`/`s['count']`/`s['title']` are read by index, so
    serving one would be a KeyError, not a stale value."""
    path = _transcript(_cache_in_tmp)
    sessions.get_session_stats(path)
    st = os.stat(path)
    key = (st.st_mtime_ns, st.st_size, sessions._STATS_SCHEMA)
    entry = stats._load_disk_cache()[path]
    entry['stats'] = {k: v for k, v in entry['stats'].items() if k != 'headless'}
    assert sessions._disk_cache_hit(path, key)[0] is False


def test_the_preview_ceiling_is_not_the_old_65(_cache_in_tmp):
    """It is what a session row shows with no AI title and no manual name."""
    p = _cache_in_tmp / 'long.jsonl'
    long_text = 'refactor the transcript parser so a 100 MB file streams ' \
                'instead of being read into memory all at once, and then ' \
                'measure it'
    p.write_text(json.dumps({'type': 'user', 'message': {
        'role': 'user', 'content': long_text}}) + '\n', encoding='utf-8')
    got = sessions.get_session_stats(str(p))['preview']
    assert len(got) > 65 and got == long_text[:200]
