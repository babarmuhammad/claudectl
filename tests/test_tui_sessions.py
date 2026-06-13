import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import (Sandbox, run_flow, typed,
                     UP, DOWN, LEFT, RIGHT, ENTER, ESC, BACK)

from claude_sessions.session_menu import sessions_menu
from claude_sessions.sessions import scan_sessions


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def open_menu(monkeypatch, sb, keys, folder, name='proj', path=None):
    sess = scan_sessions(folder)
    return run_flow(monkeypatch, keys, sessions_menu,
                    sess, folder, name, path or str(sb.root / 'work' / name))


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
    assert 'A archived' in cap.plain          # hint line 2 visible


def test_empty_project(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project(n_sessions=0)
    result, cap, _ = open_menu(monkeypatch, sb, flat(ENTER), folder)
    assert result == 'new'                    # New Chat only (+ terminal)
