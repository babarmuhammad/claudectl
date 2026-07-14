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
    body = (f"# alpha\n\n## Project context\nAI generated context. "
            + "This project does interesting things worth describing at length. " * 3
            + f"\n\n## Tech stack\nPython 3.10, stdlib only.\n\n"
            + f"{_AUTOGEN_START}\n{_AUTOGEN_END}\n{_SESSIONS_START}\n{_SESSIONS_END}\n")
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
    body = (f"# alpha\n\n## Project context\nstuff and more descriptive text. " * 4
            + f"\n\n## Notes\nmore\n\n{_AUTOGEN_START}\n{_AUTOGEN_END}\n")
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
    # menu: 'ai' first → ENTER select, ENTER skip extra prompt, ENTER approve diff
    keys = flat(ENTER, ENTER, ENTER)
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


# ── F2: AI compression ───────────────────────────────────────

def _seed_compress_md(proj, folder):
    from claude_sessions.config import _MEMORY_START, _MEMORY_END
    md_path = os.path.join(proj, 'CLAUDE.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("# alpha\n\n## Long prose\n" + "wordy explanation sentence. " * 80
                + f"\n\n{_AUTOGEN_START}\nold autogen\n{_AUTOGEN_END}\n"
                + f"{_SESSIONS_START}\n## Session topics\n- **s** (2 msgs): t\n{_SESSIONS_END}\n"
                + f"{_MEMORY_START}\nverbatim digest\n{_MEMORY_END}\n")
    return md_path


def test_ai_compress_writes_backup_and_preserves_memory(monkeypatch, tmp_path):
    from claude_sessions import memory, diffview
    from claude_sessions.config import _MEMORY_START, _MEMORY_END
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=1)
    proj = str(sb.root / 'work' / 'alpha')
    md_path = _seed_compress_md(proj, folder)
    old = open(md_path, encoding='utf-8').read()

    monkeypatch.setattr(memory, '_claude_stdin',
                        lambda *a, **k: ("# alpha\n\n- run: pytest -q\n- style: terse\n"
                                         "- package manager: uv\n- never touch dist/\n"))
    monkeypatch.setattr(diffview, 'confirm', lambda *a, **k: True)
    monkeypatch.setattr(ui, 'flash', lambda *a, **k: None)
    assert claude_md.ai_compress_claude_md(proj, folder) is True

    out = open(md_path, encoding='utf-8').read()
    assert '- run: pytest -q' in out                       # compressed manual
    assert 'wordy explanation sentence.' not in out        # prose replaced
    assert f"{_MEMORY_START}\nverbatim digest\n{_MEMORY_END}" in out.replace('\r', '')
    assert _AUTOGEN_START in out and _SESSIONS_START in out
    assert open(md_path + '.bak', encoding='utf-8').read() == old


def test_ai_compress_refusal_leaves_file_untouched(monkeypatch, tmp_path):
    from claude_sessions import memory
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=1)
    proj = str(sb.root / 'work' / 'alpha')
    md_path = _seed_compress_md(proj, folder)
    old = open(md_path, encoding='utf-8').read()
    monkeypatch.setattr(memory, '_claude_stdin',
                        lambda *a, **k: "I'll help you compress this file...")
    monkeypatch.setattr(ui, 'flash', lambda *a, **k: None)
    assert claude_md.ai_compress_claude_md(proj, folder) is False
    assert open(md_path, encoding='utf-8').read() == old
    assert not os.path.exists(md_path + '.bak')


def test_ai_compress_rejected_diff_no_write(monkeypatch, tmp_path):
    from claude_sessions import memory, diffview
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=1)
    proj = str(sb.root / 'work' / 'alpha')
    md_path = _seed_compress_md(proj, folder)
    old = open(md_path, encoding='utf-8').read()
    monkeypatch.setattr(memory, '_claude_stdin',
                        lambda *a, **k: ("# alpha\n\n- terse fact one two three\n"
                                         "- another durable terse fact to keep\n"))
    monkeypatch.setattr(diffview, 'confirm', lambda *a, **k: False)
    monkeypatch.setattr(ui, 'flash', lambda *a, **k: None)
    assert claude_md.ai_compress_claude_md(proj, folder) is False
    assert open(md_path, encoding='utf-8').read() == old
