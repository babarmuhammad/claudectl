import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claude_sessions.config as config


def test_load_settings_defaults_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'settings_file', str(tmp_path / 'nope.json'))
    s = config.load_settings()
    assert s['editor'] == ''
    assert s['project_defaults'] == {}


def test_settings_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'settings_file', str(tmp_path / 'claudectl.json'))
    s = config.load_settings()
    s['default_effort'] = 'high'
    s['project_defaults']['D--repos'] = {'effort': 'low', 'model': ''}
    assert config.save_settings(s)
    s2 = config.load_settings()
    assert s2['default_effort'] == 'high'
    assert s2['project_defaults']['D--repos']['effort'] == 'low'


def test_load_settings_ignores_unknown_keys(monkeypatch, tmp_path):
    f = tmp_path / 'claudectl.json'
    f.write_text(json.dumps({'editor': 'x', 'evil_key': 1}), encoding='utf-8')
    monkeypatch.setattr(config, 'settings_file', str(f))
    s = config.load_settings()
    assert 'evil_key' not in s


def test_load_settings_corrupt_file(monkeypatch, tmp_path):
    f = tmp_path / 'claudectl.json'
    f.write_text('{{{not json', encoding='utf-8')
    monkeypatch.setattr(config, 'settings_file', str(f))
    s = config.load_settings()
    assert s == config._DEFAULT_SETTINGS or s['editor'] == ''


def test_get_config_dir_default(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'settings_file', str(tmp_path / 'nope.json'))
    assert config.get_config_dir() == os.path.join(config._USERPROFILE, '.claude')


def test_get_config_dir_override(monkeypatch, tmp_path):
    f = tmp_path / 'claudectl.json'
    f.write_text(json.dumps({'claude_config_dir': str(tmp_path / 'acct')}), encoding='utf-8')
    monkeypatch.setattr(config, 'settings_file', str(f))
    assert config.get_config_dir() == str(tmp_path / 'acct')


def test_get_config_dir_expands(monkeypatch, tmp_path):
    f = tmp_path / 'claudectl.json'
    f.write_text(json.dumps({'claude_config_dir': '~/.claude-work'}), encoding='utf-8')
    monkeypatch.setattr(config, 'settings_file', str(f))
    assert config.get_config_dir() == os.path.expanduser('~/.claude-work')


def test_find_editor_returns_existing_or_none():
    e = config.find_editor()
    assert e is None or os.path.exists(e)


def test_get_claude_exe_returns_existing_or_none():
    c = config.get_claude_exe()
    assert c is None or os.path.exists(c)
