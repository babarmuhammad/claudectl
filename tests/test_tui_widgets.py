import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, UP, DOWN, LEFT, RIGHT, ENTER, ESC

from claude_sessions import ui, config


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


# ── confirm ──────────────────────────────────────────────────

def test_confirm_default_no(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    res, _, _ = run_flow(monkeypatch, flat(ENTER), ui.confirm, 'Delete?')
    assert res is False          # default highlight = No


def test_confirm_yes_via_nav(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    res, _, _ = run_flow(monkeypatch, flat(RIGHT, ENTER), ui.confirm, 'Delete?')
    assert res is True


def test_confirm_esc_is_no(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    res, _, _ = run_flow(monkeypatch, flat(ESC), ui.confirm, 'Delete?')
    assert res is False


def test_confirm_y_key(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    res, _, _ = run_flow(monkeypatch, flat(typed('y')), ui.confirm, 'Delete?')
    assert res is True


def test_confirm_danger_renders(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    res, cap, _ = run_flow(monkeypatch, flat(ESC), ui.confirm, 'Nuke?', True)
    assert 'CONFIRM' in cap.plain and 'Nuke?' in cap.plain


# ── multiselect ──────────────────────────────────────────────

ITEMS = [('Alpha', 'a'), ('Beta', 'b'), ('Gamma', 'c')]


def test_multiselect_toggle(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    # toggle first + third
    keys = flat(typed(' '), DOWN, DOWN, typed(' '), ENTER)
    res, _, _ = run_flow(monkeypatch, keys, ui.multiselect, ITEMS, 'Pick')
    assert res == {'a', 'c'}


def test_multiselect_all_then_none(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    res, _, _ = run_flow(monkeypatch, flat(typed('a'), ENTER), ui.multiselect, ITEMS, 'Pick')
    assert res == {'a', 'b', 'c'}
    res, _, _ = run_flow(monkeypatch, flat(typed('a'), typed('n'), ENTER),
                         ui.multiselect, ITEMS, 'Pick')
    assert res == set()


def test_multiselect_preselected(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    res, cap, _ = run_flow(monkeypatch, flat(ENTER), ui.multiselect, ITEMS, 'Pick',
                           {'b'})
    assert res == {'b'}
    assert '[x] Beta' in cap.plain


def test_multiselect_cancel(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    res, _, _ = run_flow(monkeypatch, flat(typed(' '), ESC), ui.multiselect, ITEMS, 'Pick')
    assert res is None


def test_multiselect_view_fn(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    seen = []
    view = lambda v: seen.append(v)
    keys = flat(typed('v'), ENTER)
    res, _, _ = run_flow(monkeypatch, keys, ui.multiselect, ITEMS, 'Pick', None, '', view)
    assert seen == ['a']


# ── theme ────────────────────────────────────────────────────

def test_apply_theme_swaps_globals():
    config.apply_theme('ocean')
    assert config.C_ACCENT == config.THEMES['ocean']['C_ACCENT']
    config.apply_theme('default')
    assert config.C_ACCENT == config.THEMES['default']['C_ACCENT']


def test_apply_theme_unknown_falls_back():
    config.apply_theme('nonsense')
    assert config.C_ACCENT == config.THEMES['default']['C_ACCENT']


def test_all_themes_have_core_keys():
    for name, pal in config.THEMES.items():
        for k in ('C_ACCENT', 'C_SEL_BG', 'C_HEADER_BG', 'C_OK', 'C_WARN'):
            assert k in pal, (name, k)
