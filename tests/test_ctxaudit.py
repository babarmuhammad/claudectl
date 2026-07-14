"""Step 0 + F1 — CLAUDE.md caps/prune and the context-weight audit."""

import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, ESC, ENTER, RIGHT, make_jsonl, run_flow, typed

from claude_sessions import claude_md, ctxaudit, memory
from claude_sessions.config import (_AUTOGEN_START, _AUTOGEN_END,
                                    _SESSIONS_START, _SESSIONS_END,
                                    _MEMORY_START, _MEMORY_END)


def _many_sessions(sb, folder, n):
    for i in range(n):
        sid = f'bbbb{i:04d}-0000-0000-0000-00000000{i:04d}'
        make_jsonl(os.path.join(folder, f'{sid}.jsonl'),
                   preview=f'topic number {i}')
        t = time.time() - (n - i) * 60          # i=n-1 newest
        os.utime(os.path.join(folder, f'{sid}.jsonl'), (t, t))


# ── Step 0: caps ─────────────────────────────────────────────

def test_sessions_block_capped_to_setting(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    _many_sessions(sb, folder, 15)
    block = claude_md._build_sessions_block(folder, {})
    lines = [l for l in block.splitlines() if l.startswith('- ')]
    assert len(lines) == 10                     # default cap
    assert 'topic number 14' in block           # newest kept
    assert 'topic number 0' not in block        # oldest dropped

    with open(sb.settings, 'w', encoding='utf-8') as f:
        json.dump({'claude_md_sessions_cap': 3}, f)
    block = claude_md._build_sessions_block(folder, {})
    assert len([l for l in block.splitlines() if l.startswith('- ')]) == 3

    block = claude_md._build_sessions_block(folder, {}, cap=0)   # 0 = unlimited
    assert len([l for l in block.splitlines() if l.startswith('- ')]) == 15


def test_prune_claude_md_shrinks_and_preserves_manual_and_memory(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    _many_sessions(sb, folder, 12)
    old_sessions = '\n'.join(f'- **old{i}** (2 msgs): stale topic {i}' for i in range(30))
    manual = "# alpha\n\n## Project context\nHand-written notes stay.\n\n"
    mem_block = f"{_MEMORY_START}\nsemantic digest line\n{_MEMORY_END}\n"
    md = (manual
          + f"{_AUTOGEN_START}\nold autogen\n{_AUTOGEN_END}\n"
          + f"{_SESSIONS_START}\n## Session topics\n{old_sessions}\n{_SESSIONS_END}\n"
          + mem_block)
    md_path = os.path.join(actual, 'CLAUDE.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md)

    res = claude_md.prune_claude_md(actual, folder)
    assert res is not None
    old_tok, new_tok = res
    assert new_tok < old_tok
    out = open(md_path, encoding='utf-8').read()
    assert 'Hand-written notes stay.' in out
    assert f"{_MEMORY_START}\nsemantic digest line\n{_MEMORY_END}" in out
    lines = [l for l in out.splitlines() if l.startswith('- **')]
    assert len(lines) == 10                     # capped
    assert 'stale topic 29' not in out          # old accumulated entries pruned


# ── F1: audit items ──────────────────────────────────────────

def _seed_audit_project(sb):
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    _many_sessions(sb, folder, 3)
    md = ("# alpha\n\nManual notes here.\n\n"
          + f"{_AUTOGEN_START}\nrepo block " + "x " * 300 + f"\n{_AUTOGEN_END}\n"
          + f"{_SESSIONS_START}\n## Session topics\n- **s1** (2 msgs): t\n"
            f"- **s2** (2 msgs): t\n{_SESSIONS_END}\n"
          + f"{_MEMORY_START}\ndigest\n{_MEMORY_END}\n")
    with open(os.path.join(actual, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
        f.write(md)
    from claude_sessions import config as config_mod
    with open(config_mod.global_claude_md, 'w', encoding='utf-8') as f:
        f.write('global conventions ' * 60)                 # > 500 tok? no, ~285
    rules = os.path.join(actual, '.claude', 'rules')
    os.makedirs(rules, exist_ok=True)
    with open(os.path.join(rules, 'lazy-rule.md'), 'w', encoding='utf-8') as f:
        f.write('---\nglobs:\n  - "src/**"\n---\nrule body\n')
    with open(os.path.join(rules, 'always-rule.md'), 'w', encoding='utf-8') as f:
        f.write('always-on rule body\n')
    with open(os.path.join(folder, 'system-prompt.txt'), 'w', encoding='utf-8') as f:
        f.write('be terse\n')
    return actual, folder


def test_audit_items_labels_tokens_lazy(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, folder = _seed_audit_project(sb)
    items = ctxaudit.audit_items(actual, folder)
    by = {i['label']: i for i in items}
    assert 'CLAUDE.md · manual content' in by
    assert by['CLAUDE.md · autogen (repos/commits)']['tokens'] > 100
    assert 'CLAUDE.md · session topics (2)' in by
    assert 'CLAUDE.md · memory digest' in by
    assert 'global ~/.claude/CLAUDE.md' in by
    assert by['rule lazy-rule.md']['lazy'] is True
    assert by['rule always-rule.md']['lazy'] is False
    assert 'system-prompt.txt (--system-prompt-file)' in by
    mcp_rows = [l for l in by if l.startswith('MCP servers (1)')]
    assert mcp_rows and by[mcp_rows[0]]['tokens'] == ctxaudit.MCP_TOKENS_PER_SERVER
    # lazy rules excluded from the always-on total
    assert ctxaudit.audit_total(items) == sum(
        i['tokens'] for i in items if not i['lazy'] and i['tokens'])
    assert by['rule lazy-rule.md']['tokens'] not in (None, 0)
    # compact-instructions hint present (no section in the seeded file)
    manual_warns = ' '.join(by['CLAUDE.md · manual content']['warnings'])
    assert 'Compact instructions' in manual_warns


def test_audit_warns_on_long_claude_md_and_uncapped_sessions(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    entries = '\n'.join(f'- **e{i}** (2 msgs): topic' for i in range(25))
    md = ("# alpha\n" + 'filler line\n' * 220
          + f"{_SESSIONS_START}\n## Session topics\n{entries}\n{_SESSIONS_END}\n")
    with open(os.path.join(actual, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
        f.write(md)
    items = ctxaudit.audit_items(actual, folder)
    warns = ' | '.join(w for i in items for w in i['warnings'])
    assert 'compress' in warns
    assert '25 session entries' in warns


def test_audit_screen_smoke_and_prune_flow(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, folder = _seed_audit_project(sb)
    _many_sessions(sb, folder, 12)
    entries = '\n'.join(f'- **old{i}** (2 msgs): stale topic {i}' for i in range(30))
    md_path = os.path.join(actual, 'CLAUDE.md')
    md = open(md_path, encoding='utf-8').read().replace(
        '- **s1** (2 msgs): t\n- **s2** (2 msgs): t', entries)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md)

    # p → confirm Yes (RIGHT, ENTER) → back on audit screen → ESC out
    _ret, cap, _ex = run_flow(monkeypatch, [*typed('p'), *RIGHT, *ENTER, *ESC],
                              ctxaudit.audit_screen, actual, folder, 'alpha')
    assert 'CONTEXT WEIGHT' in cap.plain
    assert 'total always-on' in cap.plain
    out = open(md_path, encoding='utf-8').read()
    assert 'stale topic 29' not in out           # pruned on disk
    assert len([l for l in out.splitlines() if l.startswith('- **')]) == 10


def test_append_compact_section(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    md_path = os.path.join(actual, 'CLAUDE.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# alpha\nnotes\n{_MEMORY_START}\nd\n{_MEMORY_END}\n")
    assert ctxaudit.append_compact_section(actual) is True
    out = open(md_path, encoding='utf-8').read()
    assert '# Compact instructions' in out
    assert f"{_MEMORY_START}\nd\n{_MEMORY_END}" in out       # sentinels untouched
    assert ctxaudit.append_compact_section(actual) is False  # idempotent
    # audit hint disappears once present
    items = ctxaudit.audit_items(actual, folder)
    by = {i['label']: i for i in items}
    warns = ' '.join(by['CLAUDE.md · manual content']['warnings'])
    assert 'Compact instructions' not in warns
