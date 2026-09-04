"""The plugin bundle: it exists, it is valid, and it does not fight the tool.

`claude plugin validate` is the same check the community review pipeline runs.
It is not available in every environment, so the shape is asserted here too —
a manifest that only CI can check is a manifest nobody checks locally.
"""

import io
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKET = os.path.join(ROOT, '.claude-plugin', 'marketplace.json')
PLUGIN = os.path.join(ROOT, 'plugin')
MANIFEST = os.path.join(PLUGIN, '.claude-plugin', 'plugin.json')


def _json(p):
    return json.loads(io.open(p, encoding='utf-8').read())


# ── manifests ────────────────────────────────────────────────

def test_the_marketplace_manifest_is_where_claude_code_looks():
    assert os.path.isfile(MARKET), '.claude-plugin/marketplace.json must be at repo root'


def test_the_marketplace_declares_its_required_fields():
    m = _json(MARKET)
    for k in ('name', 'owner', 'plugins'):
        assert k in m, k
    assert isinstance(m['owner'], dict) and m['owner'].get('name')
    assert m['plugins'] and all(p.get('name') and p.get('source') for p in m['plugins'])


def test_the_marketplace_points_at_a_plugin_that_exists():
    for p in _json(MARKET)['plugins']:
        src = p['source']
        assert isinstance(src, str) and src.startswith('./'), \
            'a same-repo source must be a relative path'
        d = os.path.join(ROOT, src.replace('/', os.sep))
        assert os.path.isdir(d), src
        assert os.path.isfile(os.path.join(d, '.claude-plugin', 'plugin.json'))


def test_the_plugin_manifest_is_the_only_file_in_its_manifest_dir():
    """Everything except plugin.json belongs at the plugin ROOT."""
    d = os.path.join(PLUGIN, '.claude-plugin')
    assert sorted(os.listdir(d)) == ['plugin.json']


def test_the_plugin_name_is_kebab_case():
    name = _json(MANIFEST)['name']
    assert name and name == name.lower()
    assert ' ' not in name


#: every file that states the version, and the pattern that carries it.
#: pyproject.toml is the source; these are copies Claude Code's own formats and
#: the docs page require literally. A release bumps ALL of them — the plugin
#: manifest was missed once and the release commit went red on six CI jobs with
#: the tag already pushed.
VERSION_COPIES = (
    ('plugin/.claude-plugin/plugin.json', r'"version":\s*"([^"]+)"'),
    ('.claude-plugin/marketplace.json',   r'"version":\s*"([^"]+)"'),
    ('docs/index.md',                     r'"softwareVersion":\s*"([^"]+)"'),
    ('CITATION.cff',                      r'(?m)^version:\s*(\S+)\s*$'),
)


def _package_version():
    src = io.open(os.path.join(ROOT, 'pyproject.toml'), encoding='utf-8').read()
    return src.split('version = "', 1)[1].split('"', 1)[0]


def test_the_plugin_version_tracks_the_package():
    ver = _package_version()
    assert _json(MANIFEST)['version'] == ver, \
        'plugin.json and pyproject.toml disagree about the version'
    assert _json(MARKET)['plugins'][0]['version'] == ver


def test_every_file_that_states_the_version_agrees_with_pyproject():
    """The gate covered two of four copies.

    `docs/index.md` carries the version in its JSON-LD and nothing checked it,
    so it sat at 1.6.0 through a 1.7.0 release. A copy no test names is a copy
    that rots; this names them all in one tuple.
    """
    import re
    ver = _package_version()
    bad = []
    for rel, pat in VERSION_COPIES:
        path = os.path.join(ROOT, rel.replace('/', os.sep))
        m = re.search(pat, io.open(path, encoding='utf-8').read())
        if not m:
            bad.append('%s: no version found (pattern moved?)' % rel)
        elif m.group(1) != ver:
            bad.append('%s: %s != %s' % (rel, m.group(1), ver))
    assert not bad, 'files disagreeing with pyproject.toml: %s' % bad


# ── contents ─────────────────────────────────────────────────

def test_every_bundled_skill_is_a_real_skill():
    d = os.path.join(PLUGIN, 'skills')
    names = sorted(os.listdir(d))
    assert len(names) >= 8, names
    for n in names:
        p = os.path.join(d, n, 'SKILL.md')
        assert os.path.isfile(p), n
        text = io.open(p, encoding='utf-8').read()
        assert text.startswith('---\n'), '%s has no frontmatter' % n
        head = text.split('---', 2)[1]
        assert 'description:' in head, '%s declares no description' % n


def test_the_bundled_skills_are_generated_from_the_package_templates():
    """One copy of each template. The package ships them because package-data
    cannot reach outside the package; the plugin needs them at its own root."""
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    import gen_plugin
    assert gen_plugin.main(['--check']) == 0, \
        'plugin/skills is stale — run tools/gen_plugin.py'


def test_every_command_declares_a_description():
    d = os.path.join(PLUGIN, 'commands')
    names = sorted(n for n in os.listdir(d) if n.endswith('.md'))
    assert names, 'no slash commands'
    for n in names:
        text = io.open(os.path.join(d, n), encoding='utf-8').read()
        assert text.startswith('---\n'), n
        assert 'description:' in text.split('---', 2)[1], n


def test_the_commands_say_what_to_do_when_the_cli_is_missing():
    """They shell out to `claudectl`, which is a separate pip install."""
    d = os.path.join(PLUGIN, 'commands')
    for n in os.listdir(d):
        text = io.open(os.path.join(d, n), encoding='utf-8').read()
        assert 'claudectl' in text, n
        assert 'not installed' in text or 'not found' in text, \
            '%s does not handle the CLI being absent' % n


def test_the_plugin_ships_no_hooks():
    """claudectl already installs its own through a manager that places them
    per account. Two owners for one settings.json entry means installing both
    runs the recall hook twice per prompt, and uninstalling either leaves the
    other behind looking broken. The README says so; this keeps it true."""
    assert not os.path.isdir(os.path.join(PLUGIN, 'hooks'))
    assert 'hooks' not in _json(MANIFEST)
    readme = io.open(os.path.join(PLUGIN, 'README.md'), encoding='utf-8').read()
    assert 'Hooks' in readme, 'the omission is not explained'


# ── the real validator ───────────────────────────────────────

def _claude():
    from claude_sessions.config import get_claude_exe
    return get_claude_exe()


@pytest.mark.parametrize('target', ['.', 'plugin'])
def test_the_claude_cli_validates_both_manifests(target):
    exe = _claude()
    if not exe:
        pytest.skip('the claude CLI is not installed here')
    r = subprocess.run([exe, 'plugin', 'validate', target], cwd=ROOT,
                       capture_output=True, text=True, encoding='utf-8',
                       errors='ignore', timeout=120)
    assert r.returncode == 0, (r.stdout or '') + (r.stderr or '')
