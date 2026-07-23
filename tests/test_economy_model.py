"""Economy model routing: claudectl's own internal `claude -p` calls should
carry `--model <extract_model>` when set, and no `--model` when the setting is
blank (account default). Verifies the shared memory._claude_stdin helper.
"""

import subprocess

from harness import Sandbox
from claude_sessions import config, memory


def _write_settings(sb, **kw):
    import json
    s = dict(config._DEFAULT_SETTINGS)
    s.update(kw)
    sb.settings.write_text(json.dumps(s), encoding='utf-8')


def _capture_args(monkeypatch):
    """Run _claude_stdin in silent mode with a fake exe/subprocess and return
    the argv it built."""
    seen = {}

    class _P:
        stdout = '{}'

    def fake_run(args, **kw):
        seen['args'] = args
        return _P()

    monkeypatch.setattr(config, 'get_claude_exe', lambda: r'C:\fake\claude.exe')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    memory._tls.silent = True
    try:
        memory._claude_stdin('hello', cwd='.')
    finally:
        memory._tls.silent = False
    return seen.get('args', [])


def test_extract_model_adds_model_flag(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _write_settings(sb, extract_model='claude-haiku-4-5')
    args = _capture_args(monkeypatch)
    assert '--model' in args
    assert args[args.index('--model') + 1] == 'claude-haiku-4-5'


def test_blank_extract_model_uses_account_default(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _write_settings(sb, extract_model='')
    args = _capture_args(monkeypatch)
    assert '--model' not in args


def test_explicit_model_override_beats_setting(monkeypatch, tmp_path):
    """Passing model='' forces the default even when the setting is non-blank."""
    sb = Sandbox(monkeypatch, tmp_path)
    _write_settings(sb, extract_model='claude-haiku-4-5')
    seen = {}

    class _P:
        stdout = '{}'

    monkeypatch.setattr(config, 'get_claude_exe', lambda: r'C:\fake\claude.exe')
    monkeypatch.setattr(subprocess, 'run',
                        lambda args, **kw: (seen.__setitem__('args', args), _P())[1])
    memory._tls.silent = True
    try:
        memory._claude_stdin('hi', cwd='.', model='')          # force default
    finally:
        memory._tls.silent = False
    assert '--model' not in seen['args']


def test_default_setting_is_haiku(monkeypatch, tmp_path):
    """Out-of-the-box, extract_model routes internal calls to the cheap model."""
    assert config._DEFAULT_SETTINGS['extract_model'] == 'claude-haiku-4-5'
