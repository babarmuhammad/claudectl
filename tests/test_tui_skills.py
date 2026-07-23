import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, DOWN, ENTER, ESC

from claude_sessions import skills, config


def _lib(sb, monkeypatch):
    """Point the user skill library into the sandbox tmp dir."""
    d = str(sb.root / 'skills-lib')
    monkeypatch.setattr(config, 'skills_library_dir', d)
    return d


# ── pure helpers ─────────────────────────────────────────────

def test_parse_write_roundtrip(tmp_path):
    d = str(tmp_path / 'commit-message')
    assert skills.write_skill(d, {'name': 'commit-message',
                                  'description': 'write commits',
                                  'allowed-tools': 'Read, Bash'},
                              '# Commit\n\nDo it.')
    meta, body = skills.parse_skill(d)
    assert meta['name'] == 'commit-message'
    assert meta['allowed-tools'] == 'Read, Bash'
    assert '# Commit' in body
    assert os.path.isfile(os.path.join(d, 'SKILL.md'))


def test_bundled_templates_all_valid():
    """Every shipped template parses and carries an attribution footer."""
    tmpls = skills.list_skills(skills.bundled_templates_dir())
    assert len(tmpls) >= 6
    for name, desc, d in tmpls:
        meta, body = skills.parse_skill(d)
        assert meta.get('name'), f'{name} missing name'
        assert meta.get('description'), f'{name} missing description'
        assert '<!--' in body and 'claudectl' in body.lower(), \
            f'{name} missing attribution footer'


def test_list_templates_merges_library(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    lib = _lib(sb, monkeypatch)
    skills.write_skill(os.path.join(lib, 'my-skill'),
                       {'name': 'my-skill', 'description': 'mine'}, 'body')
    merged = skills.list_templates()
    names = [n for n, *_ in merged]
    assert 'my-skill' in names
    assert 'commit-message' in names          # bundled still present
    src = {n: s for n, _d, _dir, s in merged}
    assert src['my-skill'] == 'library'
    assert src['commit-message'] == 'template'


def test_install_and_remove_project(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    proj = tmp_path / 'proj'
    proj.mkdir()
    # install a bundled template into the project
    src = os.path.join(skills.bundled_templates_dir(), 'commit-message')
    dest = skills.install_skill(src, str(proj))
    assert dest and os.path.isfile(os.path.join(dest, 'SKILL.md'))
    listed = skills.list_skills(skills.project_skills_dir(str(proj)))
    assert any(n == 'commit-message' for n, *_ in listed)
    # remove it
    assert skills.delete_skill(dest)
    assert skills.list_skills(skills.project_skills_dir(str(proj))) == []


def test_save_to_library(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    lib = _lib(sb, monkeypatch)
    src = os.path.join(skills.bundled_templates_dir(), 'test-writer')
    dest = skills.save_to_library(src)
    assert dest and dest.startswith(lib)
    assert os.path.isfile(os.path.join(dest, 'SKILL.md'))


def test_slug():
    assert skills._slug('Commit Message!') == 'commit-message'
    assert skills._slug('') == 'skill'


# ── TUI flow ─────────────────────────────────────────────────

def test_new_manual_into_library(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    lib = _lib(sb, monkeypatch)
    # skills_menu(None): 8 selectable template rows, sep, New manual, New AI.
    # DOWN past the 8 templates lands on '＋ New skill (manual)'.
    seq = []
    for _ in range(8):
        seq += DOWN
    seq += ENTER                            # New skill (manual)
    seq += typed('note-taker') + ENTER      # name
    seq += typed('take notes') + ENTER      # description
    seq += ENTER                            # tools: none selected, confirm
    seq += ESC                              # leave menu
    run_flow(monkeypatch, seq, skills.skills_menu, None)
    d = os.path.join(lib, 'note-taker')
    assert os.path.isfile(os.path.join(d, 'SKILL.md'))
    meta, _ = skills.parse_skill(d)
    assert meta['name'] == 'note-taker'
