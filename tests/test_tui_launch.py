import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from harness import Sandbox, run_flow, typed, UP, DOWN, LEFT, RIGHT, ENTER, ESC

from claude_sessions.ui import launch_options_menu


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def run_menu(monkeypatch, keys, **kw):
    result, cap, ex = run_flow(monkeypatch, keys, launch_options_menu, 'proj', **kw)
    return result, cap


def test_enter_returns_defaults(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, _ = run_menu(monkeypatch, flat(ENTER))
    assert result == {'effort': '', 'model': '', 'perm': '', 'name': '',
                      'worktree': '', 'agent': '', 'cfgdir': '',
                      'max_thinking': '', 'subagent_model': ''}


def test_account_readonly_not_editable(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    accts = [('default', r'C:\def'), ('work', r'C:\work')]
    # try to reach/cycle it: arrows must never change the account → cfgdir ''
    keys = flat(DOWN, DOWN, DOWN, RIGHT, ENTER)
    result, cap = run_menu(monkeypatch, keys, account_opts=accts)
    assert result['cfgdir'] == ''                 # always the active account
    assert 'read-only' in cap.plain               # shown, marked non-editable
    assert 'default' in cap.plain


def test_economy_fields_cycle(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    # no agents/accounts → fields: effort,model,perm,Think(3),Subagents(4)
    keys = flat(DOWN, DOWN, DOWN, RIGHT,        # Think cap → 4000 (first non-default)
                DOWN, RIGHT,                    # Subagents → haiku
                ENTER)
    result, _ = run_menu(monkeypatch, keys)
    assert result['max_thinking'] == '4000'
    assert result['subagent_model'] == 'claude-haiku-4-5'


def test_economy_preset_key(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, _ = run_menu(monkeypatch, flat(typed('e'), ENTER))
    assert result['model'] == 'claude-sonnet-5'
    assert result['max_thinking'] == '8000'
    assert result['subagent_model'] == 'claude-haiku-4-5'


def test_esc_returns_none(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, _ = run_menu(monkeypatch, flat(ESC))
    assert result is None


def test_cycle_effort_and_model(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    keys = flat(RIGHT,            # effort -> low
                DOWN, RIGHT, RIGHT,  # model -> sonnet
                ENTER)
    result, _ = run_menu(monkeypatch, keys)
    assert result['effort'] == 'low'
    assert result['model'] == 'claude-sonnet-5'


def test_cycle_wraps_backwards(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, _ = run_menu(monkeypatch, flat(LEFT, ENTER))   # '' -> wraps to 'max'
    assert result['effort'] == 'max'


def test_permission_field(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    keys = flat(DOWN, DOWN, RIGHT, ENTER)   # perm -> 'plan'
    result, _ = run_menu(monkeypatch, keys)
    assert result['perm'] == 'plan'


def test_defaults_preselected(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, _ = run_menu(monkeypatch, flat(ENTER),
                         defaults={'effort': 'high', 'model': 'claude-fable-5',
                                   'permission': 'plan'})
    assert result == {'effort': 'high', 'model': 'claude-fable-5',
                      'perm': 'plan', 'name': '', 'worktree': '', 'agent': '', 'cfgdir': '',
                      'max_thinking': '', 'subagent_model': ''}


def test_new_session_has_five_fields(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, cap = run_menu(monkeypatch, flat(ENTER), is_new=True)
    assert 'Worktree' in cap.plain
    assert 'Name' in cap.plain
    assert result['worktree'] == '' and result['name'] == ''


def test_resume_hides_new_fields(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    _, cap = run_menu(monkeypatch, flat(ENTER), is_new=False)
    assert 'Worktree' not in cap.plain


def test_worktree_auto(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    keys = flat(DOWN, DOWN, DOWN,    # to worktree field
                RIGHT,               # off -> auto
                ENTER)
    result, _ = run_menu(monkeypatch, keys, is_new=True)
    assert result['worktree'] == '*'


def test_worktree_custom_name(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    keys = flat(DOWN, DOWN, DOWN,
                RIGHT,                       # off -> auto
                RIGHT, typed('feat-x'), ENTER,   # auto -> custom (text input)
                ENTER)                       # launch
    result, _ = run_menu(monkeypatch, keys, is_new=True)
    assert result['worktree'] == 'feat-x'


def test_worktree_cycle_reaches_off_from_custom(monkeypatch, tmp_path):
    """Audit bug #4: from custom, cycling must be able to reach 'off'."""
    Sandbox(monkeypatch, tmp_path)
    keys = flat(DOWN, DOWN, DOWN,
                RIGHT,                        # off -> auto
                RIGHT, typed('feat-x'), ENTER,    # -> custom
                RIGHT,                        # custom -> off (forward cycle)
                ENTER)
    result, _ = run_menu(monkeypatch, keys, is_new=True)
    assert result['worktree'] == ''

    keys = flat(DOWN, DOWN, DOWN,
                RIGHT,                        # off -> auto
                RIGHT, typed('feat-x'), ENTER,    # -> custom
                LEFT, LEFT,                   # custom -> auto -> off
                ENTER)
    result, _ = run_menu(monkeypatch, keys, is_new=True)
    assert result['worktree'] == ''


def test_session_name_input(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    keys = flat(DOWN, DOWN, DOWN, DOWN,      # to name field
                RIGHT, typed('My Session'), ENTER,
                ENTER)
    result, _ = run_menu(monkeypatch, keys, is_new=True)
    assert result['name'] == 'My Session'


def test_field_nav_wraps(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    # resume-mode fields: effort,model,perm,Think,Subagents → UP from 0 wraps
    # to the last field (Subagents); RIGHT cycles it to haiku
    keys = flat(UP, RIGHT, ENTER)
    result, _ = run_menu(monkeypatch, keys, is_new=False)
    assert result['subagent_model'] == 'claude-haiku-4-5'
