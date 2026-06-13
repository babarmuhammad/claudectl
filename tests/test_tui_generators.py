import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, ENTER, ESC

from claude_sessions import claude_md, system_prompt, ui
from claude_sessions.config import (_AUTOGEN_START, _AUTOGEN_END,
                                    _SESSIONS_START, _SESSIONS_END, _AI_MARKER)


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


# ── scaffold (no subprocess) ─────────────────────────────────

def test_scaffold_writes_sentinels(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=1)
    proj = str(sb.root / 'work' / 'alpha')
    claude_md.scaffold_claude_md(proj, folder)
    md = open(os.path.join(proj, 'CLAUDE.md'), encoding='utf-8').read()
    assert _AUTOGEN_START in md and _AUTOGEN_END in md
    assert sb.editor_opened   # opens result in editor


def test_scaffold_preserves_user_content(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=1)
    proj = str(sb.root / 'work' / 'alpha')
    md_path = os.path.join(proj, 'CLAUDE.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("# alpha\n\n## My Notes\nKeep me!\n\n"
                f"{_AUTOGEN_START}\nold\n{_AUTOGEN_END}\n")
    claude_md.scaffold_claude_md(proj, folder)
    md = open(md_path, encoding='utf-8').read()
    assert 'Keep me!' in md            # user section untouched
    assert 'old' not in md             # autogen block refreshed


# ── AI analyze with a faked subprocess ───────────────────────

class _FakePopen:
    def __init__(self, lines, returncode=0):
        import io
        body = ''.join(lines)
        self.stdout = io.StringIO(body)
        self.stderr = io.StringIO('')
        self.returncode = returncode

    def wait(self):
        return self.returncode

    def terminate(self):
        self.returncode = -1


def _stream_json(text):
    import json
    return [json.dumps({'type': 'assistant',
                        'message': {'content': [{'type': 'text', 'text': text}]}}) + '\n']


def test_ai_analyze_writes_on_approve(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=1)
    proj = str(sb.root / 'work' / 'alpha')
    body = f"# alpha\n\nAI generated context.\n\n{_AUTOGEN_START}\n{_AUTOGEN_END}\n{_SESSIONS_START}\n{_SESSIONS_END}\n"
    monkeypatch.setattr(claude_md, 'get_claude_exe', lambda: r'C:\fake.exe')
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: _FakePopen(_stream_json(body)))
    # ENTER past the confirm screen (start), then ENTER on extra-instructions
    # input (skip), then ENTER to approve the pager
    keys = flat(ENTER, ENTER, ENTER)
    run_flow(monkeypatch, keys, claude_md.ai_scaffold_claude_md, proj, folder)
    md = open(os.path.join(proj, 'CLAUDE.md'), encoding='utf-8').read()
    assert 'AI generated context' in md
    assert _AI_MARKER in md
    assert _AUTOGEN_START in md


def test_ai_analyze_reject_does_not_write(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=1)
    proj = str(sb.root / 'work' / 'alpha')
    body = f"# alpha\n\nstuff\n\n{_AUTOGEN_START}\n{_AUTOGEN_END}\n"
    monkeypatch.setattr(claude_md, 'get_claude_exe', lambda: r'C:\fake.exe')
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: _FakePopen(_stream_json(body)))
    keys = flat(ENTER, ENTER, ESC)   # start, skip extras, REJECT in pager
    run_flow(monkeypatch, keys, claude_md.ai_scaffold_claude_md, proj, folder)
    assert not os.path.exists(os.path.join(proj, 'CLAUDE.md'))


def test_ai_analyze_no_claude_falls_back_to_scaffold(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=1)
    proj = str(sb.root / 'work' / 'alpha')
    monkeypatch.setattr(claude_md, 'get_claude_exe', lambda: None)
    keys = flat(ENTER, ENTER)   # start, skip extras → fallback scaffold runs
    run_flow(monkeypatch, keys, claude_md.ai_scaffold_claude_md, proj, folder)
    md = open(os.path.join(proj, 'CLAUDE.md'), encoding='utf-8').read()
    assert _AUTOGEN_START in md   # scaffold wrote it


# ── system prompt generation ─────────────────────────────────

def test_system_prompt_generate_writes(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    proj = str(sb.root / 'work' / 'alpha')
    monkeypatch.setattr(system_prompt, 'get_claude_exe', lambda: r'C:\fake.exe')
    monkeypatch.setattr(system_prompt, 'run_with_progress',
                        lambda *a, **k: ('You are a test assistant.', False))
    sp = os.path.join(folder, 'system-prompt.txt')
    # menu: 'ai' is first item; ENTER selects it, then ENTER skips extra prompt
    keys = flat(ENTER, ENTER)
    run_flow(monkeypatch, keys, system_prompt.edit_system_prompt, folder, 'alpha', proj)
    assert os.path.exists(sp)
    assert 'test assistant' in open(sp, encoding='utf-8').read()


def test_system_prompt_generate_cancel(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    proj = str(sb.root / 'work' / 'alpha')
    monkeypatch.setattr(system_prompt, 'get_claude_exe', lambda: r'C:\fake.exe')
    monkeypatch.setattr(system_prompt, 'run_with_progress',
                        lambda *a, **k: (None, True))   # cancelled
    sp = os.path.join(folder, 'system-prompt.txt')
    keys = flat(ENTER, ENTER)
    run_flow(monkeypatch, keys, system_prompt.edit_system_prompt, folder, 'alpha', proj)
    assert not os.path.exists(sp)
