import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, ESC

from claude_sessions import connections


def _mkfile(base, rel, content=''):
    p = os.path.join(base, rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)
    return p


def _ids(g):
    return {n['id'] for n in g['nodes']}


def _dep_set(g):
    return {(e['source'], e['target']) for e in g['dep_edges']}


def _by_id(g, nid):
    return next(n for n in g['nodes'] if n['id'] == nid)


# ── hierarchy ────────────────────────────────────────────────

def test_hierarchy_nodes_parent_totals(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'lib/core.py', 'X=1\n')
    _mkfile(actual, 'lib/util.py', 'Y=2\n')
    _mkfile(actual, 'app/main.py', 'Z=3\n')
    g = connections.build_hierarchy(actual, folder)
    ids = _ids(g)
    assert 'root:' in ids and 'dir:lib' in ids and 'dir:app' in ids
    assert 'file:lib/core.py' in ids and 'file:app/main.py' in ids   # file leaves
    assert _by_id(g, 'dir:lib')['parent'] == 'root:'
    assert _by_id(g, 'file:lib/core.py')['parent'] == 'dir:lib'
    assert _by_id(g, 'dir:lib')['total_files'] == 2
    assert _by_id(g, 'root:')['total_files'] == 3       # whole tree counted


def test_file_dep_python(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'lib/__init__.py', '')
    _mkfile(actual, 'lib/core.py', 'X=1\n')
    _mkfile(actual, 'app/main.py', 'from lib import core\n')
    g = connections.build_hierarchy(actual, folder)
    assert ('file:app/main.py', 'file:lib/core.py') in _dep_set(g)


def test_file_dep_csharp(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('cs')
    _mkfile(actual, 'core/A.cs', 'namespace App.Core { class A {} }\n')
    _mkfile(actual, 'web/B.cs', 'using App.Core;\nnamespace App.Web { class B {} }\n')
    g = connections.build_hierarchy(actual, folder)
    assert ('file:web/B.cs', 'file:core/A.cs') in _dep_set(g)


def test_file_dep_cpp(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('cpp')
    _mkfile(actual, 'src/main.cpp', '#include "../inc/util.h"\n')
    _mkfile(actual, 'inc/util.h', '#pragma once\n')
    g = connections.build_hierarchy(actual, folder)
    assert ('file:src/main.cpp', 'file:inc/util.h') in _dep_set(g)


def test_rank_and_top_repos(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'big/a.py', 'x=1\n')
    _mkfile(actual, 'big/b.py', 'y=1\n')
    _mkfile(actual, 'small/c.py', 'z=1\n')
    g = connections.build_hierarchy(actual, folder)
    tops = connections.top_repos(g)
    assert tops and tops[0]['label'] == 'big'           # most files first


# ── cache ────────────────────────────────────────────────────

def test_cache_roundtrip(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'a/x.py', 'x=1\n')
    g1 = connections.build_hierarchy(actual, folder)
    cache = os.path.join(actual, '.claudectl', connections._CACHE_NAME)
    assert os.path.isfile(cache)
    g2 = connections.build_hierarchy(actual, folder)            # served from cache
    assert g2['meta']['signature'] == g1['meta']['signature']
    # adding a file changes the signature → fresh build
    _mkfile(actual, 'a/y.py', 'y=1\n')
    g3 = connections.build_hierarchy(actual, folder)
    assert g3['meta']['signature'] != g1['meta']['signature']
    assert g3['meta']['counts']['files'] == 2


# ── HTML ─────────────────────────────────────────────────────

def test_render_html_self_contained(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'a/x.py', 'x=1\n')
    html = connections.render_html(connections.build_hierarchy(actual, folder))
    for needle in ('<canvas', 'const CODE', 'id="search"', 'expanded', 'id="fit"', 'drawDodec'):
        assert needle in html, needle
    assert 'http://' not in html and 'https://' not in html
    assert '<script src=' not in html


def test_render_html_escapes_injection(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'a/x.py', 'x=1\n')
    g = connections.build_hierarchy(actual, folder)
    g['nodes'].append({'id': 'x', 'label': '</script><b>pwn', 'parent': 'root:',
                       'type': 'dir', 'own_files': 0, 'total_files': 0, 'repo': 'x',
                       'depth': 1, 'rank': 0})
    html = connections.render_html(g)
    assert '<\\/script>' in html
    assert html.count('</script>') == 1


def test_write_and_open(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'a/x.py', 'x=1\n')
    g = connections.build_hierarchy(actual, folder)
    p = connections.write_graph_html(g, actual, folder)
    assert p and os.path.isfile(p)
    opened = []
    monkeypatch.setattr(os, 'startfile', lambda x: opened.append(x), raising=False)
    ok, err = connections.open_graph(p)
    assert ok is True and err == '' and opened == [p]


# ── TUI ──────────────────────────────────────────────────────

def test_connections_screen_renders(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    _mkfile(actual, 'a/x.py', 'x=1\n')
    monkeypatch.setattr(os, 'startfile', lambda x: None, raising=False)
    _res, cap, _ = run_flow(monkeypatch, ESC, connections.connections_screen,
                            actual, folder, 'alpha')
    assert 'ARCHITECTURE' in cap.plain
    assert 'Files' in cap.plain
