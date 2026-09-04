import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, make_jsonl, run_flow, ESC

from claude_sessions import workspace, config


FIXED_SHA = 'a' * 40


def _stub_git(monkeypatch, sha=FIXED_SHA):
    monkeypatch.setattr(workspace, '_git_head', lambda p: (sha, sha[:7], 'main'))


def _seed_project(sb, monkeypatch, n_sessions=2, document_mcp=True):
    actual, enc, folder, sids = sb.add_project('repo', n_sessions=n_sessions)
    # README first, then CLAUDE.md → CLAUDE.md is the newer file (no conflict)
    with open(os.path.join(actual, 'README.md'), 'w', encoding='utf-8') as f:
        f.write('# repo\n\nhello\n')
    with open(os.path.join(actual, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
        f.write('# repo\n\n## Project context\n')
    if document_mcp:
        # harness stubs mcp_servers = [('TestMCP','ok')]; document it in global md
        with open(config.global_claude_md, 'w', encoding='utf-8') as f:
            f.write('# Global\n<!-- MCP:TestMCP:START -->\n## MCP: TestMCP\n'
                    '- `do_thing` does a thing\n<!-- MCP:TestMCP:END -->\n')
    return actual, enc, folder, sids


# ── pure helpers ─────────────────────────────────────────────

def test_manifest_roundtrip(tmp_path):
    m = workspace._empty_manifest()
    m['repo']['head_sha'] = 'deadbeef'
    assert workspace.save_manifest(str(tmp_path), m)
    got = workspace.load_manifest(str(tmp_path))
    assert got['repo']['head_sha'] == 'deadbeef'
    assert got['schema_version'] == workspace.SCHEMA_VERSION


def test_migrate_fills_and_preserves():
    old = {'schema_version': 0, 'repo': {'head_sha': 'x'}, 'custom_future_key': 42}
    m = workspace._migrate(old)
    assert m['schema_version'] == workspace.SCHEMA_VERSION
    assert m['repo']['head_sha'] == 'x'           # kept
    assert m['repo']['branch'] == ''              # filled
    assert 'file_hashes' in m and 'operations' in m
    assert m['custom_future_key'] == 42           # unknown key preserved


def test_sha256_stable(tmp_path):
    p = tmp_path / 'f.txt'
    p.write_text('abc', encoding='utf-8')
    h1 = workspace._sha256_file(str(p))
    h2 = workspace._sha256_file(str(p))
    assert h1 and h1 == h2
    p.write_text('abcd', encoding='utf-8')
    assert workspace._sha256_file(str(p)) != h1


def test_count_tools():
    md = "## MCP: x\n| Tool | Desc |\n|---|---|\n| a | x |\n| b | y |\n"
    assert workspace._count_tools(md) == 2


# ── status evaluation ────────────────────────────────────────

def test_scaffold_makes_fresh(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    workspace.update_manifest(actual, folder, 'scaffold')

    m, live, checks, score, safe = workspace.compute_status(actual, folder)
    states = {c['name']: c['state'] for c in checks}
    assert safe is True
    assert score >= 80, states
    assert states['claude_md'] == 'fresh'
    assert states['claude_md_fresh'] == 'fresh'
    assert states['mcp_docs'] == 'fresh'
    assert m['sessions']['analyzed_count'] == 2


def test_readme_change_makes_stale(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    workspace.update_manifest(actual, folder, 'scaffold')
    _, _, _, fresh_score, _ = workspace.compute_status(actual, folder)

    with open(os.path.join(actual, 'README.md'), 'w', encoding='utf-8') as f:
        f.write('# repo\n\nCHANGED CONTENT\n')

    _, _, checks, score, safe = workspace.compute_status(actual, folder)
    states = {c['name']: c['state'] for c in checks}
    assert states['claude_md_fresh'] == 'stale'
    assert score < fresh_score
    assert safe is True   # stale ≠ unsafe


def test_repo_moved_makes_stale(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch, FIXED_SHA)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    workspace.update_manifest(actual, folder, 'scaffold')

    _stub_git(monkeypatch, 'b' * 40)   # HEAD moved
    _, _, checks, score, safe = workspace.compute_status(actual, folder)
    states = {c['name']: c['state'] for c in checks}
    assert states['repo'] == 'stale'
    assert states['claude_md_fresh'] == 'stale'


def test_new_session_makes_stale(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch, n_sessions=2)
    workspace.update_manifest(actual, folder, 'scaffold')

    make_jsonl(os.path.join(folder, 'bbbb0000-0000-0000-0000-000000000099.jsonl'))
    from claude_sessions import sessions as _s
    _s._info_cache.clear()

    _, _, checks, _, _ = workspace.compute_status(actual, folder)
    states = {c['name']: c['state'] for c in checks}
    assert states['sessions'] == 'stale'


def test_corrupt_manifest_invalid_not_safe(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    d = os.path.join(actual, workspace.MANIFEST_DIR)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, workspace.MANIFEST_NAME), 'w', encoding='utf-8') as f:
        f.write('{ not valid json')

    m, _, checks, score, safe = workspace.compute_status(actual, folder)
    states = {c['name']: c['state'] for c in checks}
    assert states['manifest'] == 'invalid'
    assert safe is False
    # display must not have healed the corrupt file
    raw = open(os.path.join(d, workspace.MANIFEST_NAME), encoding='utf-8').read()
    assert raw == '{ not valid json'


def test_print_status_output(monkeypatch, tmp_path, capsys):
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    workspace.update_manifest(actual, folder, 'scaffold')

    workspace.print_workspace_status(actual, folder)
    out = capsys.readouterr().out
    for needle in ('Workspace Status', 'Repo HEAD', 'Sessions analyzed',
                   'MCP servers', 'CLAUDE.md status', 'Safe to launch',
                   'freshness score'):
        assert needle in out, needle


def test_status_screen_renders_and_exits(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    workspace.update_manifest(actual, folder, 'scaffold')
    _res, cap, _ = run_flow(monkeypatch, ESC, workspace.workspace_status_screen,
                            actual, folder)
    assert 'WORKSPACE' in cap.plain
    assert 'Safe to launch' in cap.plain


def test_update_is_nonfatal_without_project(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    # no project_path, no proj_folder → must not raise, returns None or dict
    res = workspace.update_manifest('', None, 'launch', choice='new')
    assert res is None or isinstance(res, dict)


# ── the score has to MOVE when you act on it ─────────────────
# The writer stamped a baseline for scaffold/ai_analyze only, while _last_gen
# read the same two — so a memory rebuild, a compress or a prune regenerated
# the very blocks the check measures and the score stayed on Stale forever.

def test_rebuilding_memory_clears_the_stale_flag(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch, FIXED_SHA)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    workspace.update_manifest(actual, folder, 'scaffold')

    _stub_git(monkeypatch, 'c' * 40)                 # work happened
    _, _, checks, before, _ = workspace.compute_status(actual, folder)
    assert {c['name']: c['state'] for c in checks}['claude_md_fresh'] == 'stale'

    # the operation the UI actually tells you to run
    workspace.update_manifest(actual, folder, 'memory')
    _, _, checks, after, _ = workspace.compute_status(actual, folder)
    states = {c['name']: c['state'] for c in checks}
    assert states['claude_md_fresh'] == 'fresh'
    assert states['repo'] == 'fresh' and states['sessions'] == 'fresh'
    assert after > before


def test_every_context_regenerating_op_is_a_baseline(monkeypatch, tmp_path):
    """One tuple, read by _last_gen and written by update_manifest. They were
    two lists that disagreed, which is the whole bug."""
    assert set(workspace._BASELINE_OPS) >= {'scaffold', 'ai_analyze', 'compress',
                                            'memory', 'prune'}
    assert 'launch' not in workspace._BASELINE_OPS, 'launching regenerates nothing'

    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    for op in workspace._BASELINE_OPS:
        m = workspace.update_manifest(actual, folder, op)
        assert 'sessions_at_gen' in m['operations'][op], op


def test_an_op_with_no_baseline_is_not_taken_as_one(monkeypatch, tmp_path):
    """A `launch` record carries only last_run. Treating it as the baseline
    would report `fresh` on no evidence at all."""
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    workspace.update_manifest(actual, folder, 'launch', choice='new')
    m = workspace.load_manifest(actual, folder)
    assert workspace._last_gen(m) is None


def test_a_check_that_does_not_count_does_not_warn(monkeypatch, tmp_path):
    """`applicable=False` removes a check from BOTH sides of the score, so
    painting it Stale showed a warning worth zero points."""
    checks = [{'name': 'mcp_docs', 'state': 'fresh', 'applicable': False,
               'detail': 'no MCP servers'}]
    assert workspace._state_of(checks, 'mcp_docs') == 'n/a'
    assert workspace._DOTS['n/a'] and workspace._WORDS['n/a'] == 'n/a'


def test_the_status_block_says_how_to_raise_the_score(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch, document_mcp=False)
    lines, _m, score, _safe = workspace._status_lines(actual, folder)
    body = '\n'.join(lines)
    assert score < 100
    assert 'To raise it:' in body
    # the remedy, not just the symptom
    assert 'undocumented: TestMCP' in body and 'MCP screen' in body


def test_mcp_docs_are_found_under_any_account(monkeypatch, tmp_path):
    """The reader used config.global_claude_md — bound at import to whichever
    account was active then — while the writer takes a cfgdir. Documenting a
    server under a second account wrote a file status never opened."""
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch, document_mcp=False)
    _, _, checks, _, _ = workspace.compute_status(actual, folder)
    assert {c['name']: c['state'] for c in checks}['mcp_docs'] == 'stale'

    alt = os.path.join(str(tmp_path), 'second-account')
    os.makedirs(alt, exist_ok=True)
    with open(os.path.join(alt, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
        f.write('# Global\n<!-- MCP:TestMCP:START -->\n## MCP: TestMCP\n'
                '- `do_thing` does a thing\n<!-- MCP:TestMCP:END -->\n')
    monkeypatch.setattr(config, 'all_config_dirs',
                        lambda: [('default', config.config_dir), ('alt', alt)])

    _, _, checks, _, _ = workspace.compute_status(actual, folder)
    assert {c['name']: c['state'] for c in checks}['mcp_docs'] == 'fresh'


def test_the_gui_gets_structured_checks(monkeypatch, tmp_path):
    """`_status_lines` is a TUI renderer — emoji dots, a meter bar and `(+25)`
    weight suffixes baked into strings. The GUI ANSI-stripped them into a <pre>,
    so every check's name, state and weight died one call short of the browser
    and nothing on the page could be acted on."""
    from claude_sessions import gui_api
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch, document_mcp=False)

    d = gui_api.api_workspace_status(
        {'path': actual, 'enc': enc, 'cfgdir': str(sb.cfg)}, None)
    by = {c['name']: c for c in d['checks']}
    assert set(by) == set(workspace._WEIGHTS), 'a check is missing from the payload'
    for c in d['checks']:
        assert {'name', 'state', 'detail', 'applicable', 'weight'} <= set(c)
        assert c['state'] in ('fresh', 'stale', 'invalid')
        assert '\x1b' not in c['detail'] and '●' not in c['detail']
    assert by['mcp_docs']['state'] == 'stale' and by['mcp_docs']['weight'] == 15
    assert isinstance(d['score'], int) and isinstance(d['safe'], bool)


# ── does the prose still agree with the graph? ───────────────

def _seed_graph(actual, folder, *summaries):
    """A memory graph whose entity summaries carry the given sentences."""
    from claude_sessions import memory
    mem = memory.load_memory(actual, folder)
    mem['entities'] = [{'id': 'e%d' % i, 'name': 'E%d' % i, 'type': 'component',
                        'summary': s, 'repo': 'repo', 'module': '(root)',
                        'source_files': [], 'valid': True}
                       for i, s in enumerate(summaries)]
    memory.save_memory(actual, folder, mem)


def test_a_noun_counted_twice_is_not_a_claim():
    """The rule the whole check rests on. A page saying "250 tokens" here and
    "600 tokens" there is not contradicting itself, and a check that cannot tell
    that apart is one people switch off."""
    assert workspace._claims('32 palettes') == {'palettes': 32}
    assert workspace._claims('32 palettes and then 29 palettes') == {}
    # the connector ends the run, so 32 is not also claimed about worlds
    got = workspace._claims('with 32 palettes and 4 themed worlds')
    assert got['palettes'] == 32 and got['worlds'] == 4
    assert 'themed' not in got, 'themed carries both numbers and must be dropped'


def test_only_plural_nouns_and_never_units():
    """Both filters exist because of a real false positive on this repository:
    "15 call sites" was read as a claim about `call`, and "2 because" as one
    about `because`."""
    assert workspace._claims('15 call sites') == {'sites': 15}
    assert workspace._claims('2 because it was') == {}
    assert workspace._claims('250 tokens') == {}, 'a unit is not a countable noun'
    assert workspace._claims('1.9.0 palettes') == {}, 'a version is not a count'


def test_a_sentence_about_the_past_is_not_a_claim_about_now():
    """The loudest false positive this check can produce. This repository's own
    CLAUDE.md documents a deleted design — "an earlier design was 26 generative
    canvas renderers" — and that sentence is correct."""
    past = 'An earlier design was 26 renderers. It now runs 3 renderers.'
    assert workspace._claims(past, present_only=True) == {'renderers': 3}
    # off by default: the graph side is present-tense by construction
    assert workspace._claims(past) == {}, 'both numbers seen → dropped'


def test_a_generated_block_never_counts_as_the_prose_contradicting_itself():
    """AUTOGEN/SESSIONS/MEMORY are rewritten from live inputs. A number in there
    disagreeing with the graph means a rebuild is due, not that the author wrote
    something wrong — and it would be a permanent false positive."""
    # The block names something the prose never mentions, on purpose: if it
    # repeated a noun the prose also states, the ambiguity rule would swallow
    # the finding and this test would pass whether or not the block was read.
    md = ('Prose says 32 palettes.\n'
          '<!-- CLAUDECTL:MEMORY:START -->\n'
          '- 3 worlds\n'
          '<!-- CLAUDECTL:MEMORY:END -->\n')
    mem = {'entities': [{'summary': 'Ships 32 palettes and 4 worlds.'}]}
    assert workspace._claim_conflicts(md, mem) == []
    # and the past-tense filter has to be WIRED, not merely available
    hist = 'It shipped with 29 palettes before the overhaul.'
    assert workspace._claim_conflicts(hist, mem) == []
    assert workspace._claim_conflicts('It ships 29 palettes.', mem) \
        == [('palettes', 29, 32)]


def test_the_prose_going_stale_is_reported_and_names_both_numbers(monkeypatch, tmp_path):
    """The bug this check was built for: claudectl's own CLAUDE.md said "29
    palettes" for months against a themes.py holding 32, while the graph — read
    from the same code — said 32. Nothing compared the two."""
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    with open(os.path.join(actual, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
        f.write('# repo\n\nThe app ships 29 palettes.\n')
    _seed_graph(actual, folder, 'Desktop app serving 32 palettes.')

    _m, live, checks, _score, _safe = workspace.compute_status(actual, folder)
    assert live['claim_conflicts'] == [('palettes', 29, 32)]
    c = next(c for c in checks if c['name'] == 'claude_md_claims')
    assert c['state'] == 'stale' and c['applicable']
    assert '29 palettes' in c['detail'] and '32' in c['detail']


def test_prose_that_agrees_is_fresh_and_a_project_with_no_graph_is_not_judged(
        monkeypatch, tmp_path):
    """"Compared and agrees" and "nothing to compare" are different facts.
    Reporting the second one Fresh is the confident overstatement claude_md_fresh
    already refuses to make, and it would hand every graph-less project 5 free
    points."""
    sb = Sandbox(monkeypatch, tmp_path)
    _stub_git(monkeypatch)
    actual, enc, folder, sids = _seed_project(sb, monkeypatch)
    with open(os.path.join(actual, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
        f.write('# repo\n\nThe app ships 32 palettes.\n')

    # no graph yet
    _m, _live, checks, _s, _safe = workspace.compute_status(actual, folder)
    c = next(c for c in checks if c['name'] == 'claude_md_claims')
    assert c['state'] == 'fresh' and not c['applicable']

    _seed_graph(actual, folder, 'Desktop app serving 32 palettes.')
    _m, _live, checks, _s, _safe = workspace.compute_status(actual, folder)
    c = next(c for c in checks if c['name'] == 'claude_md_claims')
    assert c['state'] == 'fresh' and c['applicable']


def test_the_remedy_does_not_assume_which_side_is_wrong(monkeypatch, tmp_path):
    """Found by running the check on this repository: the graph said 7 skins and
    the (corrected) prose said 8, so the GRAPH was the stale one. A fix text
    saying "edit your prose" would have sent the user to change a correct
    sentence."""
    fix = workspace._FIXES['claude_md_claims']
    assert 'rebuild memory' in fix.lower() and 'claude.md' in fix.lower()
