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


def test_account_editable_for_new_session(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    accts = [('default', r'C:\def'), ('work', r'C:\work')]
    # new-session fields: effort,model,perm,worktree,name,Account(5),Think,Subagents
    keys = flat(DOWN, DOWN, DOWN, DOWN, DOWN,   # -> Account field
                RIGHT,                          # default -> work
                ENTER)
    result, cap = run_menu(monkeypatch, keys, is_new=True, account_opts=accts)
    assert result['cfgdir'] == r'C:\work'
    assert 'read-only' not in cap.plain

    # a single account offers no picker → stays read-only, cfgdir ''
    result, cap = run_menu(monkeypatch, flat(ENTER), is_new=True,
                           account_opts=[('default', r'C:\def')])
    assert result['cfgdir'] == ''


def test_economy_fields_cycle(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    # no agents/accounts → fields: effort,model,perm,Think(3),Subagents(4)
    keys = flat(DOWN, DOWN, DOWN, RIGHT,        # Think cap → 4000 (first non-default)
                DOWN, RIGHT,                    # Subagents → haiku
                ENTER)
    result, _ = run_menu(monkeypatch, keys)
    assert result['max_thinking'] == '4000'
    assert result['subagent_model'] == 'claude-haiku-4-5'


def test_recommended_preset_alias_e(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, _ = run_menu(monkeypatch, flat(typed('e'), ENTER))   # e = preset 1 (Recommended)
    assert result['model'] == 'claude-sonnet-5'
    assert result['effort'] == 'high'


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


def test_preset_key_recommended(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, _ = run_menu(monkeypatch, flat(typed('1'), ENTER))
    assert result['model'] == 'claude-sonnet-5'
    assert result['effort'] == 'high'


def test_preset_key_deep_reasoning(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, _ = run_menu(monkeypatch, flat(typed('3'), ENTER))
    assert result['model'] == 'claude-opus-4-8'
    assert result['effort'] == 'xhigh'


def test_effort_slider_moves(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    # field 0 = effort slider; RIGHT advances the knob off 'default' to 'low'
    result, _ = run_menu(monkeypatch, flat(RIGHT, ENTER))
    assert result['effort'] == 'low'


def test_model_card_left_right(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    # field 0 = effort; DOWN -> Model; RIGHT advances selection off default
    result, _ = run_menu(monkeypatch, flat(DOWN, RIGHT, ENTER))
    assert result['model'] == 'claude-haiku-4-5'


def test_advisor_warns_on_bad_combo(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    # Model -> opus (DOWN to model, RIGHT x3 = haiku,sonnet,opus), effort stays default->low
    keys = flat(DOWN, RIGHT, RIGHT, RIGHT,   # model = opus-4-8
                UP, RIGHT,                    # back to effort, -> low
                ESC)
    _, cap = run_menu(monkeypatch, keys)
    assert 'Sonnet 5' in cap.plain and 'tip:' in cap.plain    # advises the cheaper equivalent


def test_guide_overlay_and_no_emoji(monkeypatch, tmp_path):
    import re
    Sandbox(monkeypatch, tmp_path)
    result, cap = run_menu(monkeypatch, flat(typed('?'), typed('x'), ENTER))
    assert result is not None
    assert 'MODEL GUIDE' in cap.plain
    assert '$' in cap.plain and '▪' in cap.plain          # cost / capability bars
    assert not re.search(r'[\U0001F000-\U0001FAFF]', cap.plain)   # no emoji


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
