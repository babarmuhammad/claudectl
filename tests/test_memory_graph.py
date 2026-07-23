import os

from claude_sessions import connections as cn
from claude_sessions import memory as mem


def _seed(tmp):
    g = mem._empty()
    g['entities'] = [
        {'id': 'entity:R:m:A', 'name': 'A', 'type': 'component', 'repo': 'R',
         'module': 'm', 'summary': 'does A', 'valid': True, 'rank': 3, 'hits': 2},
        {'id': 'entity:R:m:B', 'name': 'B', 'type': 'concept', 'repo': 'R',
         'module': 'm', 'summary': 'does B', 'valid': False, 'rank': 1, 'hits': 0},
        {'id': 'lesson:s:0', 'name': 'L1', 'type': 'lesson', 'summary': 'learned x',
         'status': 'approved', 'kind': 'decision', 'sid': 's'},
    ]
    g['relations'] = [{'source': 'A', 'target': 'B', 'rel': 'uses', 'unit': 'R/m'}]
    mem.save_memory(str(tmp), None, g)


def test_build_memory_hierarchy(tmp_path):
    _seed(tmp_path)
    ad = os.path.join(str(tmp_path), '.claude', 'agents')
    os.makedirs(ad, exist_ok=True)
    with open(os.path.join(ad, 'helper.md'), 'w', encoding='utf-8') as f:
        f.write('---\nname: helper\n---\nx')

    g = cn.build_memory_hierarchy(str(tmp_path))
    ids = {n['id']: n for n in g['nodes']}

    assert ids['entity:R:m:A']['type'] == 'component'
    assert ids['lesson:s:0']['type'] == 'lesson'
    assert ids['entity:R:m:B']['dim'] is True                 # invalidated fact dimmed
    assert any(n['type'] == 'agent' and n['label'] == 'helper' for n in g['nodes'])
    assert any(e['source'] == 'entity:R:m:A' and e['target'] == 'entity:R:m:B'
               for e in g['dep_edges'])                        # relation -> edge by id
    assert any(n['id'].startswith('mrepo:') for n in g['nodes'])   # namespaced groups
    assert g['meta']['counts']['entities'] == 2
    assert g['meta']['counts']['lessons'] == 1
    # cluster key: entities carry their module zone; lessons/agents their own
    ent = ids['entity:R:m:A']
    assert ent['zone'] == 'mmod:R/m' and ent['zone'] == ent['parent']
    assert ids['lesson:s:0']['zone'] == 'zlessons'


def test_render_html_embeds_both(tmp_path):
    _seed(tmp_path)
    code = {'nodes': [{'id': 'root:', 'label': 'x', 'parent': None, 'type': 'root',
                       'repo': 'root', 'total_files': 0, 'rank': 0}],
            'dep_edges': [],
            'meta': {'project_name': 'x', 'languages': [],
                     'counts': {'files': 0, 'dirs': 0, 'repos': 0, 'deps': 0}}}
    memg = cn.build_memory_hierarchy(str(tmp_path))

    html = cn.render_html(code, memory=memg, default_view='memory')
    assert 'data-v="memory"' in html and 'data-v="both"' in html
    assert '"memory"' in html                                  # default view baked in
    assert 'function setView' in html
    assert 'function zkey' in html                             # module clustering
    assert 'id="cLess"' in html and 'id="cAg"' in html         # type filters

    html2 = cn.render_html(code)                               # no memory -> clean omission
    assert 'MEMDATA={}' in html2
    assert '"code"' in html2
