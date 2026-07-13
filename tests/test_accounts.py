import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, DOWN, ENTER, ESC, RIGHT

from claude_sessions import accounts, config


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def test_accounts_lists_default_active():
    accts = accounts._accounts({'claude_config_dir': '', 'accounts': []})
    assert accts[0][0] == 'default' and accts[0][2] is True   # default active


def test_accounts_active_flag_follows_setting():
    s = {'claude_config_dir': r'C:\work', 'accounts': [{'name': 'work', 'dir': r'C:\work'}]}
    accts = accounts._accounts(s)
    by = {n: active for n, _d, active in accts}
    assert by['work'] is True and by['default'] is False


def test_env_for_strips_api_key(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-x')
    env = accounts._env_for(r'C:\acc')
    assert 'ANTHROPIC_API_KEY' not in env
    assert env['CLAUDE_CONFIG_DIR'].endswith('acc')


def test_add_account_saves_and_creates_dir(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    d = str(tmp_path / 'acc-work')
    inputs = iter(['work', d])
    monkeypatch.setattr(accounts, 'text_input', lambda *a, **k: next(inputs))
    monkeypatch.setattr(accounts, 'confirm', lambda *a, **k: False)   # skip login
    accounts._add_account(config.load_settings())
    s = config.load_settings()
    assert any(a['name'] == 'work' and a['dir'] == d for a in s['accounts'])
    assert os.path.isdir(d)


def test_switch_sets_config_dir(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    s = config.load_settings()
    s['accounts'] = [{'name': 'work', 'dir': r'C:\work'}]
    config.save_settings(s)
    monkeypatch.setattr(accounts, 'menu', lambda *a, **k: 'switch')
    accounts._account_actions(config.load_settings(), 'work')
    assert config.load_settings()['claude_config_dir'] == r'C:\work'
