import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_sessions import ui, memory


def test_run_with_progress_silent_skips_renderer(monkeypatch):
    # regression: run_with_progress used to always render a TUI progress bar.
    # Its clear-screen fallback (os.system('cls'), used when VT mode isn't
    # available -- true for a console-less GUI job thread) spawns a real
    # console per tick. At ~10 ticks/sec for up to `timeout` seconds that
    # looked like terminals endlessly opening/closing (confirmed via
    # Plan->Execute). memory._tls.silent is set by every GUI job thread
    # (gui_api.start_job) -- render_frame must never run when it's set.
    monkeypatch.setattr(memory._tls, 'silent', True, raising=False)

    def boom(*a, **k):
        raise AssertionError('render_frame must not run in silent mode')
    monkeypatch.setattr(ui.render, 'render_frame', boom)

    import subprocess
    captured = {}

    class FakeResult:
        stdout = 'hello'
    def fake_run(args, **kw):
        captured['args'] = args
        return FakeResult()
    monkeypatch.setattr(subprocess, 'run', fake_run)

    out, cancelled = ui.run_with_progress(['echo', 'hi'], ('A', 'B'), 'label')
    assert out == 'hello' and cancelled is False
    assert captured['args'] == ['echo', 'hi']


def test_run_with_progress_stdin_silent_skips_renderer(monkeypatch):
    monkeypatch.setattr(memory._tls, 'silent', True, raising=False)

    def boom(*a, **k):
        raise AssertionError('render_frame must not run in silent mode')
    monkeypatch.setattr(ui.render, 'render_frame', boom)

    import subprocess
    captured = {}

    class FakeResult:
        stdout = 'plan text'
    def fake_run(args, **kw):
        captured['input'] = kw.get('input')
        return FakeResult()
    monkeypatch.setattr(subprocess, 'run', fake_run)

    out, cancelled = ui.run_with_progress_stdin(['echo'], 'my prompt', ('A', 'B'), 'label')
    assert out == 'plan text' and cancelled is False
    assert captured['input'] == 'my prompt'


if __name__ == '__main__':
    test_run_with_progress_silent_skips_renderer()
    test_run_with_progress_stdin_silent_skips_renderer()
    print('ok')
