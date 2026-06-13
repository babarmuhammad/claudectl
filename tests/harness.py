"""Reusable TUI simulation harness for claudectl tests.

Drives interactive screens with a scripted fake msvcrt, captures rendered
output for assertions, and sandboxes all config/data paths into tmp dirs so
tests never touch the real ~/.claude.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import msvcrt   # noqa: E402  (patched per-test)

from claude_sessions import config, sessions, stats, render, ui  # noqa: E402
from claude_sessions import usage as usage_mod                   # noqa: E402
from claude_sessions import mcp as mcp_mod                       # noqa: E402

# ── key script primitives ────────────────────────────────────

UP    = [b'\xe0', b'H']
DOWN  = [b'\xe0', b'P']
LEFT  = [b'\xe0', b'K']
RIGHT = [b'\xe0', b'M']
DEL   = [b'\xe0', b'S']
ENTER = [b'\r']
ESC   = [b'\x1b']
BACK  = [b'\x08']


def typed(s):
    """Key bytes for typing a plain string."""
    return [c.encode('latin-1') for c in s]


class OutOfKeys(SystemExit):
    """Raised when the key script is exhausted — ends the flow cleanly."""


class TuiScript:
    """Fake msvcrt fed from a key list. kbhit() True while keys remain."""

    def __init__(self, keys):
        self.keys = list(keys)
        self.i = 0

    def getch(self):
        if self.i >= len(self.keys):
            raise OutOfKeys('OUT_OF_KEYS')
        k = self.keys[self.i]
        self.i += 1
        return k

    def getwch(self):
        return self.getch().decode('latin-1')

    def kbhit(self):
        # Always True: when the script is exhausted the next getch raises
        # OutOfKeys, ending the flow — kbhit False would idle-loop forever.
        return True

    def install(self, monkeypatch):
        monkeypatch.setattr(msvcrt, 'getch', self.getch)
        monkeypatch.setattr(msvcrt, 'getwch', self.getwch)
        monkeypatch.setattr(msvcrt, 'kbhit', self.kbhit)
        # flush_input would eat scripted keys (kbhit is always True)
        monkeypatch.setattr(ui, 'flush_input', lambda: None)


class CapturingStdout:
    """stdout stand-in that records everything written."""

    def __init__(self):
        self.chunks = []

    def write(self, t):
        self.chunks.append(t)

    def flush(self):
        pass

    def reconfigure(self, **kw):
        pass

    def isatty(self):
        return False

    @property
    def text(self):
        return ''.join(self.chunks)

    @property
    def plain(self):
        return render.strip_ansi(self.text)


# ── synthetic session fixtures ───────────────────────────────

def make_jsonl(path, n_msgs=3, title='', model='claude-sonnet-4-6',
               preview='hello world message', branch='main'):
    """Write a synthetic session transcript with usage data."""
    lines = []
    if title:
        lines.append({'type': 'ai-title', 'title': title})
    for i in range(n_msgs):
        if i % 2 == 0:
            lines.append({'role': 'user',
                          'content': f'{preview} {i}' if i else preview,
                          'timestamp': f'2026-06-12T10:{i:02d}:00Z',
                          'gitBranch': branch, 'cwd': 'D:\\fake'})
        else:
            lines.append({'type': 'assistant',
                          'message': {'role': 'assistant', 'model': model,
                                      'usage': {'input_tokens': 100,
                                                'output_tokens': 200,
                                                'cache_read_input_tokens': 1000,
                                                'cache_creation_input_tokens': 50},
                                      'content': [{'type': 'text',
                                                   'text': f'answer {i}'}]},
                          'timestamp': f'2026-06-12T10:{i:02d}:30Z'})
    with open(path, 'w', encoding='utf-8') as f:
        for o in lines:
            f.write(json.dumps(o) + '\n')


class Sandbox:
    """Fake config tree + all module path bindings repointed into tmp_path."""

    def __init__(self, monkeypatch, tmp_path, terminal=(100, 35)):
        self.mp = monkeypatch
        self.root = tmp_path
        self.cfg = tmp_path / 'claude-cfg'
        self.projects = self.cfg / 'projects'
        self.projects.mkdir(parents=True)
        self.choice = tmp_path / 'choice.txt'
        self.settings = tmp_path / 'claudectl.json'
        self.editor_opened = []
        self._encoded_to_actual = {}
        self._patch_paths()
        self._stub_threads()
        self.set_terminal(*terminal)
        monkeypatch.setattr(time, 'sleep', lambda s: None)
        monkeypatch.setenv('CLAUDECTL_BAT', '1')
        # reset caches between tests
        sessions._info_cache.clear()
        stats._disk_cache = None
        stats._cache_dirty = False
        ui._pushback.clear()
        render.invalidate()

    # -- path bindings (config imports are by-value in consumers) --
    def _patch_paths(self):
        import claude_sessions.main as main_mod
        import claude_sessions.search as search_mod
        cfg, prj = str(self.cfg), str(self.projects)
        lsf = os.path.join(prj, 'last-session.json')
        for mod, attr, val in [
            (config, 'config_dir', cfg), (config, 'projects_dir', prj),
            (config, 'last_session_file', lsf),
            (config, 'settings_file', str(self.settings)),
            (config, 'choice_file', str(self.choice)),
            (config, 'global_claude_md', os.path.join(cfg, 'CLAUDE.md')),
            (sessions, 'projects_dir', prj), (sessions, 'last_session_file', lsf),
            (stats, 'projects_dir', prj), (stats, 'config_dir', cfg),
            (stats, 'cache_file', os.path.join(cfg, 'stats-cache.json')),
            (search_mod, 'projects_dir', prj),
            (main_mod, 'projects_dir', prj), (main_mod, 'choice_file', str(self.choice)),
            (main_mod, 'config_dir', cfg),
        ]:
            self.mp.setattr(mod, attr, val)
        # find_actual_path can't walk fake drives — resolve via registry
        self.mp.setattr(main_mod, 'find_actual_path',
                        lambda enc: self._encoded_to_actual.get(enc))
        self.mp.setattr(config, 'open_in_editor',
                        lambda p: (self.editor_opened.append(p), True)[1])
        # modules that import open_in_editor by value need their own patch
        import claude_sessions.transcript as tr
        import claude_sessions.claude_md as cmd
        import claude_sessions.system_prompt as spm
        ed = lambda p: (self.editor_opened.append(p), True)[1]
        for m in (tr, cmd, spm):
            if hasattr(m, 'open_in_editor'):
                self.mp.setattr(m, 'open_in_editor', ed)

    def _stub_threads(self):
        self.mp.setattr(usage_mod, '_started', True)
        self.mp.setattr(usage_mod, '_ready', True)
        self.mp.setattr(usage_mod, '_data', {
            'five_hour': {'utilization': 30, 'resets_at': '2026-06-12T14:00:00Z'},
            'seven_day': {'utilization': 55, 'resets_at': '2026-06-16T09:00:00Z'},
        })
        self.mp.setattr(mcp_mod, '_mcp_ready', True)
        self.mp.setattr(mcp_mod, 'mcp_servers', [('TestMCP', 'ok')])
        # claude.exe is absent on CI runners → run()'s availability gate would
        # fire pause() and eat a scripted key, desyncing every flow. Pin a fake
        # path everywhere it was imported by value so tests are host-independent.
        import claude_sessions.main as main_mod
        import claude_sessions.claude_md as cmd
        import claude_sessions.system_prompt as spm
        fake_exe = lambda: r'C:\fake\claude.exe'
        for m in (config, main_mod, mcp_mod, ui, cmd, spm):
            if hasattr(m, 'get_claude_exe'):
                self.mp.setattr(m, 'get_claude_exe', fake_exe)

    def set_terminal(self, cols, lines):
        import shutil as _sh
        size = os.terminal_size((cols, lines))
        self.mp.setattr(_sh, 'get_terminal_size', lambda *a, **k: size)
        self.mp.setattr(render.shutil, 'get_terminal_size', lambda *a, **k: size)
        ui._term_size = None   # reset resize detector

    # -- content builders --
    def add_project(self, name='proj', n_sessions=2, **jsonl_kw):
        """Create a fake project: real dir + encoded folder with sessions.
        Returns (actual_path, encoded_name, folder, [sids])."""
        actual = self.root / 'work' / name
        actual.mkdir(parents=True, exist_ok=True)
        enc = f"X--work-{name}".replace('_', '-')
        self._encoded_to_actual[enc] = str(actual)
        folder = self.projects / enc
        folder.mkdir(exist_ok=True)
        sids = []
        for i in range(n_sessions):
            sid = f'aaaa{i:04d}-0000-0000-0000-00000000000{i}'
            title = jsonl_kw.pop('title', f'Session {i}') if i == 0 else ''
            make_jsonl(folder / f'{sid}.jsonl', title=title, **jsonl_kw)
            sids.append(sid)
        return str(actual), enc, str(folder), sids

    def write_last_sessions(self, entries):
        with open(os.path.join(str(self.projects), 'last-session.json'),
                  'w', encoding='utf-8') as f:
            json.dump(entries, f)

    def choice_line(self):
        try:
            return self.choice.read_text(encoding='utf-8').strip()
        except FileNotFoundError:
            return None


def run_flow(monkeypatch, keys, fn, *args, **kwargs):
    """Run an interactive function under a scripted keyboard + captured stdout.
    Returns (result, captured: CapturingStdout, exhausted: bool)."""
    script = TuiScript(keys)
    script.install(monkeypatch)
    cap = CapturingStdout()
    monkeypatch.setattr(sys, 'stdout', cap)
    render.invalidate()
    exhausted = False
    result = None
    try:
        result = fn(*args, **kwargs)
    except OutOfKeys:
        exhausted = True
    finally:
        monkeypatch.setattr(sys, 'stdout', sys.__stdout__)
    return result, cap, exhausted
