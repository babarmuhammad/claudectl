import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import (Sandbox, run_flow, typed, make_jsonl,
                     UP, DOWN, LEFT, RIGHT, ENTER, ESC, BACK)

from claude_sessions import main as main_mod
from claude_sessions import config as config_mod
from claude_sessions.session_menu import sessions_menu
from claude_sessions.sessions import scan_sessions


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def open_menu(monkeypatch, sb, keys, folder, name='proj', path=None):
    sess = scan_sessions(folder)
    ret, cap, exhausted = run_flow(
        monkeypatch, keys, sessions_menu,
        sess, folder, name, path or str(sb.root / 'work' / name))
    result, _foreign_dir = ret if ret is not None else (None, None)
    return result, cap, exhausted


def test_enter_resumes_session(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, sids = sb.add_project(n_sessions=1)
    # nav: New Chat -> Continue latest -> first session
    keys = flat(DOWN, DOWN, ENTER)
    result, cap, _ = open_menu(monkeypatch, sb, keys, folder)
    assert result and result.startswith('resume')
    assert sids[0] in result


def test_new_and_continue_and_terminal(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, sids = sb.add_project(n_sessions=1)
    r1, _, _ = open_menu(monkeypatch, sb, flat(ENTER), folder)
    assert r1 == 'new'
    r2, _, _ = open_menu(monkeypatch, sb, flat(DOWN, ENTER), folder)
    assert r2 == 'continue'
    # UP from New Chat goes to search bar; UP-wrap not available here, so
    # navigate down to Terminal (last selectable)
    r3, _, _ = open_menu(monkeypatch, sb, flat(*([DOWN] * 9), ENTER), folder)
    assert r3 == 'terminal'


def test_esc_returns_none(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project(n_sessions=1)
    result, _, _ = open_menu(monkeypatch, sb, flat(ESC), folder)
    assert result is None


def test_rename_writes_name_file(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, sids = sb.add_project(n_sessions=1)
    keys = flat(DOWN, DOWN,                 # to first session
                typed('r'),                 # rename
                *([BACK] * 12),             # clear prefilled AI title
                typed('My Name'), ENTER,    # input
                ESC)                        # leave menu
    result, cap, _ = open_menu(monkeypatch, sb, keys, folder)
    nf = os.path.join(folder, f'{sids[0]}.name')
    assert os.path.exists(nf)
    assert open(nf, encoding='utf-8').read() == 'My Name'


def test_archive_and_restore_roundtrip(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, sids = sb.add_project(n_sessions=2)
    sid = sids[0]   # newest? scan sorts by mtime desc; both same-ish — find row
    # archive first listed session: d -> Archive (first option) ENTER
    keys = flat(DOWN, DOWN, typed('d'), ENTER, ESC)
    result, cap, _ = open_menu(monkeypatch, sb, keys, folder)
    arch = os.path.join(folder, 'archived')
    archived = [f for f in os.listdir(arch) if f.endswith('.jsonl')]
    assert len(archived) == 1
    live = [f for f in os.listdir(folder) if f.endswith('.jsonl')]
    assert len(live) == 1

    # restore: open menu, A toggles archived view, d -> Restore ENTER
    keys = flat(typed('A'), typed('d'), ENTER, ESC, ESC)
    open_menu(monkeypatch, sb, keys, folder)
    live = [f for f in os.listdir(folder) if f.endswith('.jsonl')]
    assert len(live) == 2
    assert not [f for f in os.listdir(arch) if f.endswith('.jsonl')]


def test_delete_with_confirmation(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, sids = sb.add_project(n_sessions=1)
    keys = flat(DOWN, DOWN, typed('d'),
                DOWN, ENTER,                 # second option: Delete permanently
                RIGHT, ENTER,                # confirm modal: No->Yes, confirm
                ESC)
    open_menu(monkeypatch, sb, keys, folder)
    assert not [f for f in os.listdir(folder) if f.endswith('.jsonl')]


def test_fork_returns_fork_action(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, sids = sb.add_project(n_sessions=1)
    result, _, _ = open_menu(monkeypatch, sb, flat(DOWN, DOWN, typed('f')), folder)
    assert result == f'fork:{sids[0]}'


def test_viewer_opens_and_exits(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, sids = sb.add_project(n_sessions=1)
    keys = flat(DOWN, DOWN, typed('v'), ESC, ESC)
    result, cap, _ = open_menu(monkeypatch, sb, keys, folder)
    assert 'TRANSCRIPT' in cap.plain
    assert 'hello world message' in cap.plain


def test_export_writes_markdown(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = sb.add_project(n_sessions=1)
    keys = flat(DOWN, DOWN, typed('e'), ESC)
    result, cap, _ = open_menu(monkeypatch, sb, keys, folder, path=actual)
    exports = [f for f in os.listdir(actual) if f.startswith('claude-session-')]
    assert len(exports) == 1
    content = open(os.path.join(actual, exports[0]), encoding='utf-8').read()
    assert '### User' in content and '### Assistant' in content
    assert sb.editor_opened   # opened in editor


def test_metadata_panel(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, sids = sb.add_project(n_sessions=3)
    keys = flat(DOWN, DOWN, typed('i'), ESC, ESC)
    result, cap, _ = open_menu(monkeypatch, sb, keys, folder)
    assert 'SESSION INFO' in cap.plain
    assert 'Tokens' in cap.plain
    assert 'claude-sonnet-4-6' in cap.plain


def test_paths_and_adddirs_files(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project(n_sessions=1)
    # p -> add new path (navigate: first selectable is '+ Add new path')
    keys = flat(typed('p'), ENTER, typed('C:/tools'), ENTER,
                DOWN, DOWN, ENTER,           # select Back
                typed('x'), ENTER, typed('C:/data'), ENTER,
                DOWN, DOWN, ENTER,
                ESC)
    open_menu(monkeypatch, sb, keys, folder)
    assert open(os.path.join(folder, 'extra-paths.txt'), encoding='utf-8').read().strip() == 'C:/tools'
    assert open(os.path.join(folder, 'add-dirs.txt'), encoding='utf-8').read().strip() == 'C:/data'


def test_search_filter_in_sessions(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, sids = sb.add_project(n_sessions=2)
    # search focus via UP from top; filtered list still has New/Continue
    # rows above the sessions, so navigate down to the session row
    keys = flat(UP, typed('Session 0'), DOWN, DOWN, DOWN, ENTER)
    result, cap, _ = open_menu(monkeypatch, sb, keys, folder)
    assert result and result.startswith('resume')
    assert sids[0] in result   # the titled session, not the other one


def test_windowed_sessions_small_terminal(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path, terminal=(90, 16))
    _, enc, folder, _ = sb.add_project(n_sessions=25)
    keys = flat(ESC)
    result, cap, _ = open_menu(monkeypatch, sb, keys, folder)
    assert 'more' in cap.plain                # overflow marker
    assert 'r rename' in cap.plain            # hint line 1 visible
    assert 'm memory' in cap.plain            # hint line 2 (project row) visible


def test_empty_project(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project(n_sessions=0)
    result, cap, _ = open_menu(monkeypatch, sb, flat(ENTER), folder)
    assert result == 'new'                    # New Chat only (+ terminal)


# ── multi-account project merging ────────────────────────────

def _multi_account_project(sb, tmp_path, monkeypatch, n_sessions=1):
    """'alpha' project with a live session under the default account, plus a
    SECOND account ('work') that has its own session for the same real path
    (same encoded name, different config dir)."""
    actual, enc, folder, sids = sb.add_project('alpha', n_sessions=n_sessions)
    other_cfg = tmp_path / 'other-cfg'
    other_folder = other_cfg / 'projects' / enc
    other_folder.mkdir(parents=True)
    other_sid = 'bbbb0000-0000-0000-0000-000000000000'
    make_jsonl(other_folder / f'{other_sid}.jsonl', title='Other Acct Session')
    accts = lambda: [('default', str(sb.cfg)), ('work', str(other_cfg))]
    monkeypatch.setattr(main_mod, 'all_config_dirs', accts)
    monkeypatch.setattr(config_mod, 'all_config_dirs', accts)   # context_inject reads this one
    return actual, enc, folder, sids, str(other_cfg), str(other_folder), other_sid


def _run_main(monkeypatch, keys):
    def fn():
        try:
            main_mod.run()
        except SystemExit as e:
            if str(e) == 'OUT_OF_KEYS':
                raise
    return run_flow(monkeypatch, keys, fn)


def test_project_list_merges_same_path_across_accounts(monkeypatch, tmp_path):
    # wide terminal: pytest's own tmp_path prefix is long enough to truncate
    # the project row (and its account tag) off-screen at default width
    sb = Sandbox(monkeypatch, tmp_path, terminal=(220, 35))
    _multi_account_project(sb, tmp_path, monkeypatch)
    _, cap, _ = _run_main(monkeypatch, flat(ESC))
    plain = cap.plain
    # one project ROW (basename + full path both contain 'alpha', so count
    # lines, not substring occurrences) — not one row per account
    alpha_lines = [l for l in plain.splitlines() if 'alpha' in l]
    assert len(alpha_lines) == 1
    assert '[+work]' in alpha_lines[0]    # hints the merged account


def test_opening_merged_project_shows_tagged_foreign_session(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _multi_account_project(sb, tmp_path, monkeypatch)
    _, cap, _ = _run_main(monkeypatch, flat(ENTER, ESC, ESC))
    plain = cap.plain
    assert 'Other Acct Session' in plain
    assert '[work]' in plain


def test_resuming_foreign_session_returns_its_account_dir(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids, other_cfg, other_folder, other_sid = \
        _multi_account_project(sb, tmp_path, monkeypatch)

    sess = scan_sessions(folder)
    extra = [('work', other_folder)]
    keys = flat(UP, typed('Other Acct'), DOWN, DOWN, DOWN, ENTER)
    ret, cap, _ = run_flow(monkeypatch, keys, sessions_menu,
                           sess, folder, 'alpha', actual, extra)
    result, foreign_dir = ret
    assert result and other_sid in result
    assert foreign_dir and os.path.normcase(os.path.abspath(foreign_dir)) == \
                          os.path.normcase(os.path.abspath(other_cfg))


def test_archiving_foreign_session_moves_it_under_its_own_account(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids, other_cfg, other_folder, other_sid = \
        _multi_account_project(sb, tmp_path, monkeypatch)

    sess = scan_sessions(folder)
    extra = [('work', other_folder)]
    keys = flat(UP, typed('Other Acct'), DOWN, DOWN, DOWN,
                typed('d'), ENTER, ESC)   # d -> Archive (first option)
    run_flow(monkeypatch, keys, sessions_menu, sess, folder, 'alpha', actual, extra)

    assert not os.path.exists(os.path.join(other_folder, f'{other_sid}.jsonl'))
    assert os.path.exists(os.path.join(other_folder, 'archived', f'{other_sid}.jsonl'))
    # the primary account's own session must be untouched
    assert os.path.exists(os.path.join(folder, f'{sids[0]}.jsonl'))


def test_scaffold_key_c_does_not_crash(monkeypatch, tmp_path):
    # regression: a function-local `from .claude_md import scaffold_claude_md`
    # in the '!' handler made scaffold_claude_md a local for the whole
    # sessions_menu scope, so pressing 'c' raised UnboundLocalError before it.
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = sb.add_project('alpha', n_sessions=1)
    from claude_sessions import session_menu as sm
    called = []
    monkeypatch.setattr(sm, 'scaffold_claude_md',
                        lambda *a, **k: called.append(a))
    # ENTER opens the project's session list is already open here; press 'c'
    _res, _cap, _ex = open_menu(monkeypatch, sb, flat(typed('c'), ESC), folder,
                                name='alpha', path=actual)
    assert called, "pressing 'c' must reach scaffold_claude_md without crashing"
