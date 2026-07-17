"""Project architecture graph — expandable, importance-based hierarchy.

Builds the WHOLE project as a directory hierarchy (root → repos → dirs) with
cross-directory dependency edges (Python AST + C/C++ #include + C# using +
JS/TS import), aggregated to directory level. Renders a self-contained
interactive HTML: starts at root + repos (sized by importance), click a node to
drill into its modules; dependency edges lift to the visible level. The full
tree is embedded once and the build is cached, so any size opens fast and shows
the whole project via progressive disclosure. Pure stdlib; best-effort.
"""

import os
import re
import ast
import json
import html as _htmlmod

from . import config as _c
from . import render

SKIP_DIRS = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', '.tox',
             'dist', 'build', '.mypy_cache', '.pytest_cache', '.claudectl',
             '.claude', 'site-packages', '.next', 'target', 'bin', 'obj',
             '.idea', '.vscode', 'coverage', '.cache'}
SKIP_EXT = {'.pyc', '.pyo', '.so', '.dll', '.exe', '.bin', '.png', '.jpg',
            '.jpeg', '.gif', '.webp', '.ico', '.svg', '.lock', '.map', '.min.js',
            '.woff', '.woff2', '.ttf', '.zip', '.gz', '.pdf', '.mp4', '.mp3'}
SOURCE_EXT = {'.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java', '.rb',
              '.c', '.h', '.cpp', '.hpp', '.cs', '.php', '.swift', '.kt', '.md'}

GROUP_MAX_FILES = 12000   # whole-project walk cap (only dir nodes emitted)
MAX_DEPTH = 12            # directory depth cap
MAX_DEP_EDGES = 8000      # file→file dependency edges cap (lifted client-side)
AUTO_EXPAND_NODES = 60    # small projects: expand everything at once

_CACHE_NAME = 'connections-cache.json'
MODEL_VERSION = 'v2-files'   # bump to invalidate caches when the model changes


# ── helpers ──────────────────────────────────────────────────

def _rel(root, path):
    try:
        return os.path.relpath(path, root).replace('\\', '/')
    except Exception:
        return os.path.basename(path)


def _module_key(rel_path):
    p = rel_path[:-3] if rel_path.endswith('.py') else rel_path
    parts = [x for x in p.split('/') if x]
    if parts and parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts)


def _match_modmap(modmap, dotted):
    if not dotted:
        return None
    if dotted in modmap:
        return modmap[dotted]
    parts = dotted.split('.')
    while parts:
        k = '.'.join(parts)
        if k in modmap:
            return modmap[k]
        parts.pop()
    return None


def _read(f):
    try:
        with open(f, encoding='utf-8', errors='ignore') as fh:
            return fh.read()
    except Exception:
        return ''


def _walk_source_files(root, max_files):
    """Collect source-file paths (pruned, depth/count-capped). Pure — no graph
    mutation. Returns (abs_paths, truncated)."""
    files = []
    truncated = False
    root = os.path.abspath(root)
    for cur, dirs, names in os.walk(root):
        depth = _rel(root, cur).count('/') if cur != root else 0
        if depth >= MAX_DEPTH:
            dirs[:] = []
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
        for nm in sorted(names):
            ext = os.path.splitext(nm)[1].lower()
            if ext in SKIP_EXT or ext not in SOURCE_EXT:
                continue
            files.append(os.path.join(cur, nm))
            if len(files) >= max_files:
                truncated = True
                break
        if truncated:
            break
    return files, truncated


def _discover_repos(root, proj_folder):
    """Absolute repo paths under the project + linked extra paths."""
    try:
        from .claude_md import find_git_repos
        from .sessions import read_extra_paths
    except Exception:
        return []
    roots = [root]
    try:
        roots += [p for p in (read_extra_paths(proj_folder) or []) if os.path.isdir(p)]
    except Exception:
        pass
    seen = []
    for r in roots:
        try:
            for repo in find_git_repos(r, max_depth=2):
                rp = os.path.abspath(repo)
                if rp not in seen:
                    seen.append(rp)
        except Exception:
            continue
    return seen


def _cluster_of(path, root, rsorted):
    """Project key for a path: owning git repo, else top-level dir, else 'root'."""
    if path:
        ap = os.path.abspath(path)
        for rp in rsorted:
            if ap == rp or ap.startswith(rp + os.sep):
                return os.path.basename(rp) or rp
    rel = _rel(root, path) if path else ''
    parts = rel.split('/')
    return parts[0] if len(parts) > 1 else 'root'


# ── language breakdown ───────────────────────────────────────

_EXT_LANG = {'.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript',
             '.tsx': 'TypeScript', '.jsx': 'JavaScript', '.mjs': 'JavaScript',
             '.cjs': 'JavaScript', '.go': 'Go', '.rs': 'Rust', '.java': 'Java',
             '.rb': 'Ruby', '.cs': 'C#', '.cpp': 'C++', '.hpp': 'C++', '.cc': 'C++',
             '.hh': 'C++', '.cxx': 'C++', '.hxx': 'C++', '.inl': 'C++', '.c': 'C',
             '.h': 'C/C++', '.php': 'PHP', '.swift': 'Swift', '.kt': 'Kotlin', '.md': 'Docs'}


def _language_breakdown(files):
    counts = {}
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext in SOURCE_EXT:
            lang = _EXT_LANG.get(ext, ext.lstrip('.'))
            counts[lang] = counts.get(lang, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


# ── dependency resolvers (multi-language) ────────────────────

def _py_import_targets(root, files):
    root = os.path.abspath(root)
    pyfiles = [f for f in files if f.lower().endswith('.py')]
    if not pyfiles:
        return []
    modmap = {_module_key(_rel(root, f)): f"file:{_rel(root, f)}" for f in pyfiles}
    pairs = []
    for f in pyfiles:
        rel = _rel(root, f)
        fid = f"file:{rel}"
        try:
            tree = ast.parse(_read(f), filename=f)
        except Exception:
            continue
        cur_dir = rel[:-3].split('/')[:-1]
        targets = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    t = _match_modmap(modmap, a.name)
                    if t:
                        targets.add(t)
            elif isinstance(node, ast.ImportFrom):
                level = node.level or 0
                if level > 0:
                    base = cur_dir[:len(cur_dir) - (level - 1)] if (level - 1) <= len(cur_dir) else []
                    mod_parts = base + (node.module.split('.') if node.module else [])
                else:
                    mod_parts = node.module.split('.') if node.module else []
                if mod_parts:
                    t = _match_modmap(modmap, '.'.join(mod_parts))
                    if t:
                        targets.add(t)
                for a in node.names:
                    cand = '.'.join(mod_parts + [a.name]) if mod_parts else a.name
                    t = _match_modmap(modmap, cand)
                    if t:
                        targets.add(t)
        for t in targets:
            if t != fid:
                pairs.append((fid, t))
    return pairs


_CPP_EXT = {'.c', '.h', '.cpp', '.hpp', '.cc', '.hh', '.cxx', '.hxx', '.inl'}
_JS_EXT = {'.js', '.ts', '.tsx', '.jsx', '.mjs', '.cjs'}
_INC_RE = re.compile(r'#\s*include\s*"([^"]+)"')
_NS_RE = re.compile(r'\bnamespace\s+([A-Za-z_][\w.]*)')
_USING_RE = re.compile(r'\busing\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;')
_JS_RE = re.compile(r"""(?:from|require\(|import\(|import)\s*['"](\.{1,2}/[^'"]+)['"]""")


def _cpp_include_targets(root, files):
    cfiles = [f for f in files if os.path.splitext(f)[1].lower() in _CPP_EXT]
    if not cfiles:
        return []
    relmap, basemap = {}, {}
    for f in files:
        rel = _rel(root, f)
        relmap[rel] = f"file:{rel}"
        basemap.setdefault(os.path.basename(f), []).append(f"file:{rel}")
    pairs = []
    for f in cfiles:
        rel = _rel(root, f)
        fid = f"file:{rel}"
        cur = '/'.join(rel.split('/')[:-1])
        for inc in _INC_RE.findall(_read(f)):
            inc = inc.replace('\\', '/')
            cand = os.path.normpath((cur + '/' + inc) if cur else inc).replace('\\', '/')
            t = relmap.get(cand)
            if not t:
                lst = basemap.get(os.path.basename(inc))
                t = lst[0] if lst and len(lst) == 1 else None
            if t and t != fid:
                pairs.append((fid, t))
    return pairs


def _cs_using_targets(root, files):
    cs = [f for f in files if f.lower().endswith('.cs')]
    if not cs:
        return []
    texts, ns_map = {}, {}
    for f in cs:
        t = _read(f)
        texts[f] = t
        fid = f"file:{_rel(root, f)}"
        for ns in _NS_RE.findall(t):
            ns_map.setdefault(ns, []).append(fid)
    pairs = []
    for f, t in texts.items():
        fid = f"file:{_rel(root, f)}"
        for ns in set(_USING_RE.findall(t)):
            for tgt in ns_map.get(ns, []):
                if tgt != fid:
                    pairs.append((fid, tgt))
    return pairs


def _js_import_targets(root, files):
    js = [f for f in files if os.path.splitext(f)[1].lower() in _JS_EXT]
    if not js:
        return []
    relmap = {_rel(root, f): f"file:{_rel(root, f)}" for f in files}
    exts = ['', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs',
            '/index.ts', '/index.js', '/index.tsx']
    pairs = []
    for f in js:
        rel = _rel(root, f)
        fid = f"file:{rel}"
        cur = '/'.join(rel.split('/')[:-1])
        for spec in _JS_RE.findall(_read(f)):
            base = os.path.normpath((cur + '/' + spec) if cur else spec).replace('\\', '/')
            for e in exts:
                t = relmap.get(base + e)
                if t and t != fid:
                    pairs.append((fid, t))
                    break
    return pairs


def _all_import_targets(root, files):
    """Intra-project dependency edges across languages: (src_fid, dst_fid)."""
    root = os.path.abspath(root)
    pairs = []
    for fn in (_py_import_targets, _cpp_include_targets, _cs_using_targets, _js_import_targets):
        try:
            pairs += fn(root, files)
        except Exception:
            _c.log.exception('connections: %s failed', fn.__name__)
    return pairs


# ── hierarchy builder + cache ────────────────────────────────

def _signature(root, files):
    mt = 0.0
    for f in files:
        try:
            mt = max(mt, os.path.getmtime(f))
        except OSError:
            pass
    return f"{MODEL_VERSION}|{os.path.abspath(root)}|{len(files)}|{int(mt)}"


def _cache_path(project_path, proj_folder):
    for base in (project_path, proj_folder):
        if base:
            return os.path.join(base, '.claudectl', _CACHE_NAME)
    return ''


def _load_cache(project_path, proj_folder):
    p = _cache_path(project_path, proj_folder)
    if p and os.path.isfile(p):
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_cache(graph, project_path, proj_folder):
    for base in (project_path, proj_folder):
        if not base:
            continue
        try:
            d = os.path.join(base, '.claudectl')
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, _CACHE_NAME), 'w', encoding='utf-8') as f:
                json.dump(graph, f)
            return True
        except Exception:
            continue
    return False


def build_hierarchy(project_path, proj_folder=None, *, max_files=GROUP_MAX_FILES, force=False):
    """Whole-project directory hierarchy + dir-level dependency edges, cached by
    signature. Returns {nodes, dep_edges, meta}. Best-effort."""
    root = os.path.abspath(project_path)
    files, trunc = _walk_source_files(root, max_files)
    sig = _signature(root, files)
    if not force:
        cached = _load_cache(project_path, proj_folder)
        if cached and cached.get('meta', {}).get('signature') == sig:
            return cached

    repos = _discover_repos(root, proj_folder)
    repo_set = {os.path.abspath(p) for p in repos}
    rsorted = sorted(repo_set, key=len, reverse=True)

    nodes = {'root:': {'id': 'root:', 'label': os.path.basename(root) or root,
                       'parent': None, 'type': 'root', 'own_files': 0,
                       'total_files': 0, 'repo': 'root', 'depth': 0, 'rank': 0}}

    def ensure_dir(parts):
        if not parts:
            return 'root:'
        acc, parent = [], 'root:'
        for i, part in enumerate(parts):
            acc.append(part)
            did = 'dir:' + '/'.join(acc)
            if did not in nodes:
                ap = os.path.join(root, *acc)
                is_repo = ap in repo_set or i == 0
                nodes[did] = {'id': did, 'label': part, 'parent': parent,
                              'type': 'repo' if is_repo else 'dir', 'own_files': 0,
                              'total_files': 0, 'repo': _cluster_of(ap, root, rsorted),
                              'depth': i + 1, 'rank': 0}
            parent = did
        return parent

    # leaf file nodes (the deepest level — single components)
    for f in files:
        rel = _rel(root, f)
        did = ensure_dir(rel.split('/')[:-1])
        fid = f"file:{rel}"
        nodes[fid] = {'id': fid, 'label': os.path.basename(f), 'parent': did,
                      'type': 'file', 'own_files': 1, 'total_files': 0,
                      'repo': _cluster_of(f, root, rsorted), 'depth': rel.count('/') + 1,
                      'rank': 0}

    # total_files: roll subtree counts up to ancestors
    for n in sorted(nodes.values(), key=lambda x: x['depth'], reverse=True):
        n['total_files'] += n['own_files']
        if n['parent']:
            nodes[n['parent']]['total_files'] += n['total_files']

    # dependency edges at FILE level — the renderer lifts them to the visible
    # level (repo↔repo when collapsed, file↔file when fully expanded)
    agg = {}
    for s, d in _all_import_targets(root, files):
        if s != d and s in nodes and d in nodes:
            agg[(s, d)] = agg.get((s, d), 0) + 1
    dep_edges = sorted(({'source': a, 'target': b, 'weight': w} for (a, b), w in agg.items()),
                       key=lambda e: e['weight'], reverse=True)
    dep_trunc = len(dep_edges) > MAX_DEP_EDGES
    dep_edges = dep_edges[:MAX_DEP_EDGES]

    deg = {}
    for e in dep_edges:
        deg[e['source']] = deg.get(e['source'], 0) + e['weight']
        deg[e['target']] = deg.get(e['target'], 0) + e['weight']
    for nid, n in nodes.items():
        n['rank'] = deg.get(nid, 0)

    graph = {
        'nodes': list(nodes.values()),
        'dep_edges': dep_edges,
        'meta': {
            'root': root, 'project_name': os.path.basename(root) or root,
            'signature': sig, 'languages': _language_breakdown(files),
            'truncated': trunc or dep_trunc,
            'counts': {'files': len(files),
                       'dirs': sum(1 for n in nodes.values() if n['type'] in ('dir', 'repo')),
                       'repos': len(repos), 'deps': len(dep_edges)},
        },
    }
    _save_cache(graph, project_path, proj_folder)
    return graph


def top_repos(graph, n=8):
    repos = [nd for nd in graph['nodes'] if nd['type'] == 'repo' and nd['depth'] == 1]
    if not repos:
        repos = [nd for nd in graph['nodes'] if nd['parent'] == 'root:']
    repos.sort(key=lambda nd: (nd.get('total_files', 0), nd.get('rank', 0)), reverse=True)
    return repos[:n]


# ── HTML renderer (collapsible, importance-based) ────────────

_HTML_TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>__TITLE__ — architecture</title><style>
html,body{margin:0;height:100%;background:radial-gradient(circle at 50% 42%,#0a1430,#04060c 72%);overflow:hidden;font:12px ui-monospace,Consolas,monospace;color:#cdd2da}
#c{display:block;cursor:grab}#c:active{cursor:grabbing}
#hud{position:fixed;top:10px;left:12px;pointer-events:none;text-shadow:0 0 6px #000}
#hud b{color:#fff;font-size:15px}#hud #sub{color:#8b93a3;white-space:pre-line}
#panel{position:fixed;top:10px;right:12px;background:rgba(14,16,22,.9);border:1px solid #262a35;border-radius:8px;padding:10px 12px;width:210px}
#panel h4{margin:0 0 6px;font-size:11px;color:#9aa3b2;text-transform:uppercase;letter-spacing:.5px}
#panel label{display:block;cursor:pointer;line-height:1.7;user-select:none}
#panel input[type=checkbox]{vertical-align:middle;margin-right:6px}
#search{width:100%;box-sizing:border-box;background:#0c0e14;border:1px solid #333;color:#eee;padding:5px 7px;border-radius:5px;margin:4px 0}
.btn{display:inline-block;background:#1c2029;border:1px solid #333;color:#cdd2da;padding:4px 10px;border-radius:5px;cursor:pointer;margin:5px 6px 0 0}
.btn:hover{background:#272c38}#panel hr{border:0;border-top:1px solid #222732;margin:9px 0}
#hint{position:fixed;bottom:10px;left:12px;color:#6b7280}
#legend{position:fixed;bottom:10px;right:12px;text-align:right;line-height:1.6;max-width:50%}
#legend span{margin-left:11px}#legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px}
#tip{position:fixed;pointer-events:none;background:#0d0f15;border:1px solid #343a47;padding:6px 9px;border-radius:5px;color:#e8ebf0;display:none;max-width:420px;white-space:pre-wrap;z-index:5}
</style></head><body>
<canvas id="c"></canvas>
<div id="hud"><b>__TITLE__</b><div id="sub"></div></div>
<div id="panel">
 <h4>Architecture</h4>
 <input id="search" placeholder="search…" autocomplete="off">
 <label><input type="checkbox" id="deps" checked> dependency links</label>
 <label><input type="checkbox" id="tree" checked> containment links</label>
 <label><input type="checkbox" id="hulls" checked> project hulls</label>
 <label><input type="checkbox" id="lbl" checked> labels</label>
 <hr>
 <div><span class="btn" id="expand">Expand all</span><span class="btn" id="collapse">Collapse</span></div>
 <div><span class="btn" id="fit">Fit</span><span class="btn" id="reset">Reset</span></div>
</div>
<div id="hint">click node: expand/collapse · drag: move · wheel: zoom · hover: focus</div>
<div id="legend"></div><div id="tip"></div>
<script>
const GRAPH=__GRAPH_JSON__,COLORS=__COLORS_JSON__;
const cv=document.getElementById('c'),ctx=cv.getContext('2d'),tip=document.getElementById('tip');
let W,H;function resize(){W=cv.width=innerWidth;H=cv.height=innerHeight;}resize();addEventListener('resize',resize);

const N={};for(const n of GRAPH.nodes)N[n.id]=Object.assign({x:0,y:0,vx:0,vy:0},n);
const kids={};for(const id in N)kids[id]=[];
for(const id in N){const p=N[id].parent;if(p&&kids[p])kids[p].push(id);}
function hasKids(id){return kids[id]&&kids[id].length>0;}
const deps=GRAPH.dep_edges.filter(e=>(e.source in N)&&(e.target in N));

// cohesive neural palette: every project a hue inside blue→indigo→cyan
function hue(s){let h=0;for(let i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))&0xffffffff;return Math.abs(h)%360;}
const repos=[...new Set(GRAPH.nodes.map(n=>n.repo))];
const rhue={};repos.forEach(r=>rhue[r]=(r==='root')?208:195+hue(r)%92);
const rcol={};for(const r in rhue)rcol[r]='hsl('+rhue[r]+',72%,64%)';
function color(n){const h=rhue[n.repo]!=null?rhue[n.repo]:208;
 const imp=Math.min(1,(n.total_files||1)/220+(n.rank||0)/45);
 return 'hsl('+h+',74%,'+(56+imp*16).toFixed(0)+'%)';}

// expansion state
const expanded=new Set(['root:']);
if(GRAPH.nodes.length<=__AUTOEXP__){for(const id in N)if(hasKids(id))expanded.add(id);}
let view={x:0,y:0,k:0.8},temp=1.0;
document.getElementById('sub').textContent=
 GRAPH.meta.counts.files+" files · "+GRAPH.meta.counts.dirs+" dirs · "+GRAPH.meta.counts.repos+" repos · "+GRAPH.meta.counts.deps+" deps"
 +"\n"+(GRAPH.meta.languages||[]).slice(0,6).map(p=>p[0]+" "+p[1]).join(" · ");

// visible set: a node is visible iff all ancestors are expanded
let VIS=new Set(),VARR=[];
function recompute(){
 VIS=new Set();const q=['root:'];VIS.add('root:');
 while(q.length){const id=q.pop();if(expanded.has(id))for(const c of kids[id]){VIS.add(c);q.push(c);}}
 VARR=[...VIS].map(id=>N[id]);
 // seed freshly shown children evenly AROUND their parent (radial, all directions)
 for(const n of VARR){if(!n._placed){const p=N[n.parent];const sibs=p?kids[p.id]:['root:'];
  const idx=Math.max(0,sibs.indexOf(n.id)),m=sibs.length||1;
  const a=6.2831*idx/m+(p?(p._ph||0):0),r=120+Math.min(260,m*9);
  n.x=(p?p.x:0)+Math.cos(a)*r;n.y=(p?p.y:0)+Math.sin(a)*r;n._ph=Math.random()*6.28;n._placed=1;}}
}
function lift(id){let g=0;while(!VIS.has(id)&&N[id].parent&&g++<64)id=N[id].parent;return VIS.has(id)?id:'root:';}
function visibleDeps(){const m=new Map();
 for(const e of deps){const a=lift(e.source),b=lift(e.target);if(a===b)continue;
  const k=a<b?a+'|'+b:b+'|'+a;m.set(k,(m.get(k)||0)+e.weight);}
 return [...m].map(([k,w])=>{const[a,b]=k.split('|');return{a,b,w};});}
let VDEPS=[];
let repoZones={};                                 // repo -> {x,y,zr} non-overlapping bubbles
function computeZones(){
 const cnt={};for(const n of VARR){if(n.id==='root:')continue;cnt[n.repo]=(cnt[n.repo]||0)+1;}
 const rs=Object.keys(cnt);if(!rs.length){repoZones={};return;}
 const zr={};let sum=0;for(const r of rs){zr[r]=Math.max(150,Math.min(1600,Math.sqrt(cnt[r])*64));sum+=zr[r];}
 // ring radius so the zones fit around the circle with breathing room
 const ringR=rs.length<=1?0:Math.max(260,(sum*2.3)/(2*Math.PI));
 repoZones={};let acc=0;for(const r of rs){const share=zr[r]/sum,a=2*Math.PI*(acc+share/2);
  repoZones[r]={x:Math.cos(a)*ringR,y:Math.sin(a)*ringR,zr:zr[r]};acc+=share;}
}
let animOn=false,animP=0;
function refresh(){
 recompute();computeZones();VDEPS=visibleDeps();
 for(const n of VARR){n._sx=n.x;n._sy=n.y;}     // start = current (new nodes seeded near parent)
 settle();                                       // solve final layout synchronously
 for(const n of VARR){n._tx=n.x;n._ty=n.y;n.x=n._sx;n.y=n._sy;}  // target captured; rewind to start
 animP=0;animOn=true;                            // smooth tween into place (no physics → no jitter)
 buildLegend();
}
refresh();

function size(n){const f=n.total_files||1;return Math.max(5,Math.min(38,5+Math.log2(f+1)*4+Math.sqrt(n.rank||0)*0.6));}

// ── force layout (visible nodes; never fully freezes — stays alive) ──
function repulse(act){const cell=200,grid=new Map(),K=(a,b)=>a+'|'+b;
 for(const n of act){const k=K(Math.floor(n.x/cell),Math.floor(n.y/cell));(grid.get(k)||grid.set(k,[]).get(k)).push(n);}
 for(const n of act){const cx=Math.floor(n.x/cell),cy=Math.floor(n.y/cell);
  for(let gx=cx-1;gx<=cx+1;gx++)for(let gy=cy-1;gy<=cy+1;gy++){const a=grid.get(K(gx,gy));if(!a)continue;
   for(const m of a){if(m===n)continue;let dx=n.x-m.x,dy=n.y-m.y,d2=dx*dx+dy*dy+.01;if(d2>cell*cell*4)continue;
    let d=Math.sqrt(d2),f=(size(n)+size(m)+30)*620/d2;n.vx+=dx/d*f;n.vy+=dy/d*f;}}}}
// hard anti-overlap: separate any pair closer than their combined radius
function collide(act){for(let it=0;it<5;it++){const cell=100,grid=new Map(),K=(a,b)=>a+'|'+b;
 for(const n of act){const k=K(Math.floor(n.x/cell),Math.floor(n.y/cell));(grid.get(k)||grid.set(k,[]).get(k)).push(n);}
 for(const n of act){if(n.id==='root:')continue;const cx=Math.floor(n.x/cell),cy=Math.floor(n.y/cell);
  for(let gx=cx-1;gx<=cx+1;gx++)for(let gy=cy-1;gy<=cy+1;gy++){const a=grid.get(K(gx,gy));if(!a)continue;
   for(const m of a){if(m===n||m.id==='root:')continue;let dx=m.x-n.x,dy=m.y-n.y,d=Math.hypot(dx,dy)||0.01,min=size(n)+size(m)+18;
    if(d<min){const pu=(min-d)/2;dx/=d;dy/=d;if(!n.pin){n.x-=dx*pu;n.y-=dy*pu;}if(!m.pin){m.x+=dx*pu;m.y+=dy*pu;}}}}}}}
// one full-strength physics iteration (used both for instant pre-settle and
// the small live re-settle after a drag)
function relax(act){
 repulse(act);
 for(const n of act){const p=N[n.parent];if(p&&VIS.has(p.id)){const rest=90+(kids[p.id].length)*5;let dx=p.x-n.x,dy=p.y-n.y,d=Math.sqrt(dx*dx+dy*dy)+.01,f=(d-rest)*0.02;n.vx+=dx/d*f;n.vy+=dy/d*f;}}
 for(const n of act){if(n.id==='root:'||n.pin)continue;const z=repoZones[n.repo];
  if(z){let dx=z.x-n.x,dy=z.y-n.y,d=Math.sqrt(dx*dx+dy*dy)+.01,k=d>z.zr?0.06:0.012;n.vx+=dx*k;n.vy+=dy*k;}
  n.x+=Math.max(-60,Math.min(60,n.vx));n.y+=Math.max(-60,Math.min(60,n.vy));n.vx*=.8;n.vy*=.8;}
}
// instant settle: solve the layout synchronously so expansion snaps into place
function settle(){const act=VARR;const it=act.length>900?40:90;
 for(let i=0;i<it;i++){relax(act);if(i%2===0)collide(act);}
 collide(act);N['root:'].x=0;N['root:'].y=0;temp=0;}
function step(){const act=VARR;
 if(temp>0.02){relax(act);temp*=0.9;}             // only a brief live re-settle (e.g. after drag)
 collide(act);
 N['root:'].x=0;N['root:'].y=0;}

// ── draw (additive bloom + flowing particles) ──
let optDeps=1,optTree=1,optHulls=1,optLabels=1,query='',hoverN=null;
const bokeh=[];for(let i=0;i<22;i++)bokeh.push({x:Math.random(),y:Math.random(),r:70+Math.random()*170,p:Math.random()*6.28,h:200+Math.random()*72});
function nbrSet(id){const s=new Set([id]);for(const e of VDEPS)if(e.a===id)s.add(e.b);else if(e.b===id)s.add(e.a);
 const p=N[id].parent;if(p&&VIS.has(p))s.add(p);for(const c of kids[id])if(VIS.has(c))s.add(c);return s;}
function bg(){const g=ctx.createRadialGradient(W/2,H*0.42,0,W/2,H*0.42,Math.max(W,H)*0.78);
 g.addColorStop(0,'#0a1430');g.addColorStop(1,'#04060c');ctx.fillStyle=g;ctx.fillRect(0,0,W,H);
 ctx.globalCompositeOperation='lighter';
 for(const b of bokeh){const x=b.x*W,y=b.y*H+Math.sin(T*0.2+b.p)*22,a=0.05+0.03*Math.sin(T*0.5+b.p);
  const rg=ctx.createRadialGradient(x,y,0,x,y,b.r);rg.addColorStop(0,'hsla('+b.h+',70%,60%,'+a+')');rg.addColorStop(1,'hsla('+b.h+',70%,60%,0)');
  ctx.fillStyle=rg;ctx.fillRect(x-b.r,y-b.r,b.r*2,b.r*2);}
 ctx.globalCompositeOperation='source-over';}
function curve(a,b){const mx=(a.x+b.x)/2,my=(a.y+b.y)/2,nx=-(b.y-a.y),ny=(b.x-a.x),L=Math.sqrt(nx*nx+ny*ny)+.01,cf=Math.min(48,L*0.14);return{cx:mx+nx/L*cf,cy:my+ny/L*cf};}
function qpt(a,c,b,t){const u=1-t;return{x:u*u*a.x+2*u*t*c.cx+t*t*b.x,y:u*u*a.y+2*u*t*c.cy+t*t*b.y};}
// ── rotating 3D dodecahedron geometry (20 verts, 30 edges) ──
const PHI=1.6180339887,IPH=1/PHI;
const DV=[[1,1,1],[1,1,-1],[1,-1,1],[1,-1,-1],[-1,1,1],[-1,1,-1],[-1,-1,1],[-1,-1,-1],
 [0,IPH,PHI],[0,IPH,-PHI],[0,-IPH,PHI],[0,-IPH,-PHI],[IPH,PHI,0],[IPH,-PHI,0],[-IPH,PHI,0],
 [-IPH,-PHI,0],[PHI,0,IPH],[PHI,0,-IPH],[-PHI,0,IPH],[-PHI,0,-IPH]];
for(const v of DV){const L=Math.hypot(v[0],v[1],v[2]);v[0]/=L;v[1]/=L;v[2]/=L;}
const DE=[];(function(){let mn=9;for(let i=0;i<DV.length;i++)for(let j=i+1;j<DV.length;j++){const d=Math.hypot(DV[i][0]-DV[j][0],DV[i][1]-DV[j][1],DV[i][2]-DV[j][2]);if(d<mn)mn=d;}
 for(let i=0;i<DV.length;i++)for(let j=i+1;j<DV.length;j++){const d=Math.hypot(DV[i][0]-DV[j][0],DV[i][1]-DV[j][1],DV[i][2]-DV[j][2]);if(d<mn*1.1)DE.push([i,j]);}})();
function drawDodec(n,r,col,alpha,bright){
 const ax=T*0.5+(n._ph||0),ay=T*0.37+(n._ph||0)*1.7,ca=Math.cos(ax),sa=Math.sin(ax),cb=Math.cos(ay),sb=Math.sin(ay);
 const P=DV.map(v=>{let x=v[0]*cb+v[2]*sb,z=-v[0]*sb+v[2]*cb,y=v[1];let y2=y*ca-z*sa,z2=y*sa+z*ca;return{x:n.x+x*r,y:n.y+y2*r,s:0.8+0.2*((z2+1)/2)};});
 ctx.globalAlpha=alpha;ctx.strokeStyle=col;ctx.lineWidth=(bright?1.8:1.05)/view.k;
 ctx.beginPath();for(const e of DE){ctx.moveTo(P[e[0]].x,P[e[0]].y);ctx.lineTo(P[e[1]].x,P[e[1]].y);}ctx.stroke();
 // small joint circles where the edges meet (vertices)
 const vr=Math.max(1.6,r*0.13);
 for(const p of P){const rr=vr*p.s/view.k;
  ctx.fillStyle=col;ctx.beginPath();ctx.arc(p.x,p.y,rr,0,7);ctx.fill();
  ctx.fillStyle='rgba(255,255,255,'+(0.6*p.s)+')';ctx.beginPath();ctx.arc(p.x,p.y,rr*0.5,0,7);ctx.fill();}
 ctx.globalAlpha=1;}
function draw(){
 ctx.setTransform(1,0,0,1,0,0);bg();
 ctx.setTransform(view.k,0,0,view.k,W/2+view.x,H/2+view.y);
 const hi=query?new Set(VARR.filter(n=>n.label.toLowerCase().includes(query)).map(n=>n.id)):(hoverN?nbrSet(hoverN.id):null);
 if(optHulls)drawHulls(hi);
 if(optTree){ctx.lineWidth=0.7/view.k;
  for(const n of VARR){const p=N[n.parent];if(!p||!VIS.has(p.id))continue;const on=!hi||(hi.has(n.id)&&hi.has(p.id));
   ctx.strokeStyle=on?'rgba(150,170,210,.16)':'rgba(150,170,210,.04)';ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(n.x,n.y);ctx.stroke();}}
 const mx=VDEPS.reduce((m,e)=>Math.max(m,e.w),1);
 if(optDeps){ctx.globalCompositeOperation='lighter';
  const sorted=VDEPS.slice().sort((p,q)=>q.w-p.w);
  for(let i=0;i<sorted.length;i++){const e=sorted[i],a=N[e.a],b=N[e.b];if(!a||!b)continue;const on=!hi||(hi.has(e.a)&&hi.has(e.b));
   const c=curve(a,b),al=(on?0.20:0.04)+0.42*e.w/mx;
   ctx.strokeStyle='hsla(202,92%,68%,'+al+')';ctx.lineWidth=(0.6+2.8*e.w/mx)/view.k;
   ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.quadraticCurveTo(c.cx,c.cy,b.x,b.y);ctx.stroke();
   if(on&&i<300){const np=1+Math.min(2,Math.round(e.w/mx*2));
    for(let k=0;k<np;k++){const t=((T*0.22)+k/np+i*0.13)%1,pp=qpt(a,c,b,t),pr=(2.4+2.4*e.w/mx)/view.k;
     const rg=ctx.createRadialGradient(pp.x,pp.y,0,pp.x,pp.y,pr*3);rg.addColorStop(0,'rgba(200,235,255,.95)');rg.addColorStop(1,'rgba(200,235,255,0)');
     ctx.fillStyle=rg;ctx.beginPath();ctx.arc(pp.x,pp.y,pr*3,0,7);ctx.fill();}}}
  ctx.globalCompositeOperation='source-over';}
 // node halos (additive, pulsing)
 ctx.globalCompositeOperation='lighter';
 for(const n of VARR){const on=!hi||hi.has(n.id);const r=size(n),col=color(n);const gr=r*(2.4+(on?0.7:0))*(1+0.14*Math.sin(T*1.6+(n._ph||0)));
  const g=ctx.createRadialGradient(n.x,n.y,0,n.x,n.y,gr);
  g.addColorStop(0,col.replace('hsl(','hsla(').replace(')',','+(on?0.55:0.12)+')'));
  g.addColorStop(1,col.replace('hsl(','hsla(').replace(')',',0)'));
  ctx.fillStyle=g;ctx.beginPath();ctx.arc(n.x,n.y,gr,0,7);ctx.fill();}
 ctx.globalCompositeOperation='source-over';
 // node bodies — rotating dodecahedra (dot fallback when very dense)
 const dod=VARR.length<=250;
 for(const n of VARR){const on=!hi||hi.has(n.id);const r=size(n)*(1+0.05*Math.sin(T*1.5+(n._ph||0)));const col=color(n);
  if(dod&&r>=6){drawDodec(n,r,col,on?1:0.3,!!(hi&&hi.has(n.id)));}
  else{ctx.globalAlpha=on?1:0.28;ctx.beginPath();ctx.arc(n.x,n.y,r,0,7);ctx.fillStyle=col;ctx.fill();
   ctx.beginPath();ctx.arc(n.x-r*0.28,n.y-r*0.28,r*0.42,0,7);ctx.fillStyle='rgba(255,255,255,'+(on?0.45:0.14)+')';ctx.fill();ctx.globalAlpha=1;}
  if(hasKids(n.id)&&!expanded.has(n.id)){ctx.lineWidth=1.3/view.k;ctx.strokeStyle='rgba(255,255,255,'+(on?0.7:0.2)+')';ctx.beginPath();ctx.arc(n.x,n.y,r+3.5/view.k,0,7);ctx.stroke();}}
 if(optLabels){ctx.font=(11/view.k)+'px ui-monospace,monospace';const few=VARR.length<=40;
  for(const n of VARR){
   // label scales with ZOOM: only nodes big enough on screen (+ hovered, repos,
   // few) — so zooming out hides the wall of labels, zooming in reveals them
   const px=size(n)*view.k;
   const lab=(hi&&hi.has(n.id))||few||n.type==='root'||(n.type==='repo'&&px>=7)||px>=15;
   if(!lab)continue;ctx.globalAlpha=(!hi||hi.has(n.id))?0.96:0.12;
   const tx=n.x+size(n)+4/view.k,ty=n.y+3/view.k;ctx.fillStyle='#000';ctx.fillText(n.label,tx+0.6,ty+0.6);
   ctx.fillStyle='#dfe9ff';ctx.fillText(n.label,tx,ty);ctx.globalAlpha=1;}}
}
function drawHulls(hi){const groups={};for(const n of VARR){if(n.id==='root:')continue;(groups[n.repo]=groups[n.repo]||[]).push(n);}
 for(const r in groups){const pts=groups[r];if(pts.length<3)continue;let cx=0,cy=0;for(const p of pts){cx+=p.x;cy+=p.y;}cx/=pts.length;cy/=pts.length;
  let rad=0;for(const p of pts)rad=Math.max(rad,Math.hypot(p.x-cx,p.y-cy)+size(p));const h=rhue[r]!=null?rhue[r]:208;
  ctx.beginPath();ctx.arc(cx,cy,rad+16,0,7);ctx.fillStyle='hsla('+h+',70%,55%,.05)';ctx.fill();
  ctx.strokeStyle='hsla('+h+',70%,62%,.15)';ctx.lineWidth=1/view.k;ctx.stroke();}}
let T=0;function loop(){T=performance.now()/1000;
 if(animOn){animP=Math.min(1,animP+0.045);const e=1-Math.pow(1-animP,3);   // easeOutCubic
  for(const n of VARR){if(n.pin)continue;n.x=n._sx+(n._tx-n._sx)*e;n.y=n._sy+(n._ty-n._sy)*e;}
  if(animP>=1)animOn=false;}
 else step();
 draw();requestAnimationFrame(loop);}

// ── interaction ──
function toWorld(px,py){return{x:(px-W/2-view.x)/view.k,y:(py-H/2-view.y)/view.k};}
function hit(px,py){const w=toWorld(px,py);let best=null,bd=1e9;for(const n of VARR){const dx=n.x-w.x,dy=n.y-w.y,d=dx*dx+dy*dy,r=size(n)+6;if(d<r*r&&d<bd){bd=d;best=n;}}return best;}
let drag=null,pan=null,moved=false;
cv.addEventListener('mousedown',e=>{animOn=false;const n=hit(e.clientX,e.clientY);moved=false;if(n){drag=n;n.pin=true;}else pan={x:e.clientX-view.x,y:e.clientY-view.y};});
addEventListener('mousemove',e=>{if(drag){const w=toWorld(e.clientX,e.clientY);drag.x=w.x;drag.y=w.y;drag.vx=drag.vy=0;moved=true;temp=Math.max(temp,.3);}
 else if(pan){view.x=e.clientX-pan.x;view.y=e.clientY-pan.y;moved=true;}
 hoverN=(drag||pan)?hoverN:hit(e.clientX,e.clientY);
 if(hoverN&&!pan){tip.style.display='block';tip.style.left=(e.clientX+13)+'px';tip.style.top=(e.clientY+13)+'px';
  tip.textContent=hoverN.type.toUpperCase()+': '+hoverN.label+'\n'+(hoverN.total_files||0)+' files · rank '+(hoverN.rank||0)+(hasKids(hoverN.id)?(expanded.has(hoverN.id)?'\n(click: collapse)':'\n(click: expand '+kids[hoverN.id].length+')'):'');}
 else tip.style.display='none';});
addEventListener('mouseup',e=>{if(drag){drag.pin=false;if(!moved&&hasKids(drag.id)){expanded.has(drag.id)?expanded.delete(drag.id):expanded.add(drag.id);refresh();}}drag=null;pan=null;});
cv.addEventListener('wheel',e=>{e.preventDefault();const f=e.deltaY<0?1.12:0.89;const mx=e.clientX-W/2,my=e.clientY-H/2;view.x=mx-(mx-view.x)*f;view.y=my-(my-view.y)*f;view.k*=f;},{passive:false});
function fit(){if(!VARR.length)return;let a=1e9,b=1e9,c=-1e9,d=-1e9;for(const n of VARR){a=Math.min(a,n.x);b=Math.min(b,n.y);c=Math.max(c,n.x);d=Math.max(d,n.y);}
 const w=c-a+120,h=d-b+120;view.k=Math.min(W/w,H/h,1.6);view.x=-((a+c)/2)*view.k;view.y=-((b+d)/2)*view.k;}
document.getElementById('deps').onchange=e=>optDeps=e.target.checked;
document.getElementById('tree').onchange=e=>optTree=e.target.checked;
document.getElementById('hulls').onchange=e=>optHulls=e.target.checked;
document.getElementById('lbl').onchange=e=>optLabels=e.target.checked;
document.getElementById('search').oninput=e=>{query=e.target.value.trim().toLowerCase();
 if(query){for(const n of GRAPH.nodes)if(n.label.toLowerCase().includes(query)){let p=n.parent;while(p){expanded.add(p);p=N[p].parent;}}refresh();setTimeout(fit,300);}};
document.getElementById('expand').onclick=()=>{for(const id in N)if(hasKids(id))expanded.add(id);refresh();setTimeout(fit,300);};
document.getElementById('collapse').onclick=()=>{expanded.clear();expanded.add('root:');refresh();setTimeout(fit,300);};
document.getElementById('fit').onclick=fit;
document.getElementById('reset').onclick=()=>{view={x:0,y:0,k:0.8};temp=1;};
function buildLegend(){const seen=[...new Set(VARR.map(n=>n.repo))].filter(r=>r!=='root').slice(0,8);
 document.getElementById('legend').innerHTML=seen.map(r=>'<span><i style="background:'+rcol[r]+'"></i>'+r+'</span>').join('');}
loop();setTimeout(fit,500);
</script></body></html>"""


def render_html(graph):
    payload = json.dumps(graph, ensure_ascii=False).replace('</', '<\\/')
    title = _htmlmod.escape(graph['meta'].get('project_name', 'project'))
    return (_HTML_TEMPLATE
            .replace('__GRAPH_JSON__', payload)
            .replace('__COLORS_JSON__', json.dumps(TYPE_COLORS))
            .replace('__AUTOEXP__', str(AUTO_EXPAND_NODES))
            .replace('__TITLE__', title))


TYPE_COLORS = {'root': '#ffffff', 'repo': '#7dcfff', 'dir': '#8a8a8a'}


def graph_html_path(project_path, proj_folder=None):
    for base in (project_path, proj_folder):
        if base:
            return os.path.join(base, '.claudectl', 'connections-graph.html')
    return ''


def write_graph_html(graph, project_path, proj_folder=None):
    for base in (project_path, proj_folder):
        if not base:
            continue
        try:
            d = os.path.join(base, '.claudectl')
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, 'connections-graph.html')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(render_html(graph))
            return p
        except Exception:
            continue
    return None


def open_graph(path):
    if not path:
        return False, 'no graph file to open'
    try:
        os.startfile(path)   # Windows default browser
        return True, ''
    except Exception as e:
        _c.log.exception('connections: open_graph failed')
        return False, str(e)


# ── TUI summary screen ───────────────────────────────────────

def connections_screen(project_path, proj_folder, project_name):
    from .ui import wait_event, flash, text_input, pager, run_with_progress_stdin  # noqa
    from .ui import flush_input  # noqa

    def _build(force=False):
        return build_hierarchy(project_path, proj_folder, force=force)

    graph = _build()
    R, D = _c.C_RESET, _c.C_DIM
    while True:
        c = graph['meta']['counts']
        frame = [render.header('CLAUDECTL', project_name, 'ARCHITECTURE'), '', render.hline(), '']
        langs = graph['meta'].get('languages') or []
        lang_str = '  '.join(f"{n} {k}" for n, k in langs[:6]) or '?'
        frame.append(f"  {D}Languages   {R}{render.trunc(lang_str, render.content_width() - 18)}")
        frame.append(f"  {D}Files       {R}{c.get('files', 0)}")
        frame.append(f"  {D}Dirs        {R}{c.get('dirs', 0)}")
        frame.append(f"  {D}Repos       {R}{c.get('repos', 0)}")
        frame.append(f"  {D}Dependencies{R} {c.get('deps', 0)}")
        if graph['meta'].get('truncated'):
            frame.append(f"  {_c.C_WARN}large project — capped for display (whole tree still cached){R}")
        tops = top_repos(graph, 8)
        if tops:
            frame += ['', f"  {_c.C_BOLD}Top projects (by size){R}"]
            for nd in tops:
                frame.append(f"    {render.trunc(nd['label'], 32):<32} {D}{nd.get('total_files', 0)} files · {nd.get('rank', 0)} deps{R}")
        frame += ['', render.hline(), '', render.hint_keys(
            [('o', 'open graph'), ('r', 'rebuild'), ('m', 'build memory (Claude)'),
             ('a', 'ask'), ('p', 'preview injection'), ('h', 'prompt-hook on/off'),
             ('ENTER/ESC', 'back')])]
        render.render_frame(frame)
        ev = wait_event()
        if ev[0] in ('enter', 'esc'):
            return
        if ev[0] == 'char' and ev[1] == 'o':
            p = write_graph_html(graph, project_path, proj_folder)
            if not p:
                flash("Could not write graph HTML (check disk/permissions)", ok=False, secs=2.5)
            else:
                ok, err = open_graph(p)
                flash(f"Opened {p}" if ok else f"Could not open graph: {err}",
                      ok=ok, secs=1.2 if ok else 2.5)
        elif ev[0] == 'char' and ev[1] == 'r':
            graph = _build(force=True)
            flash("Graph rebuilt")
        elif ev[0] == 'char' and ev[1] == 'm':
            try:
                from . import memory
                mem = memory.refresh_memory(project_path, proj_folder, project_name)
                n_ent = len(mem.get('entities', []))
                pend = mem.get('pending_units', 0)
                msg = (f"Memory built: {n_ent} entities" if n_ent
                       else "Claude returned no entities (cancelled or empty)")
                if n_ent and pend:
                    msg += f" — coverage incomplete ({pend} units pending, raise memory_max_calls)"
                flash(msg, ok=bool(n_ent), secs=2.5 if pend else 1.8)
            except Exception as e:
                flash(f"Memory build failed: {e}", ok=False, secs=2)
        elif ev[0] == 'char' and ev[1] == 'p':
            try:
                from . import recall
                recall.preview_screen(project_path, proj_folder, project_name)
            except Exception as e:
                flash(f"Preview failed: {e}", ok=False, secs=2)
        elif ev[0] == 'char' and ev[1] == 'h':
            try:
                from .config import load_settings, save_settings
                from .paths import encode_component
                from . import hooks as hooks_mod
                s = load_settings()
                enc = encode_component(os.path.abspath(project_path))
                proj = s.setdefault('project_defaults', {}).setdefault(enc, {})
                new_state = not proj.get('memory_hook', s.get('memory_prompt_hook', False))
                proj['memory_hook'] = new_state
                save_settings(s)
                if new_state and not hooks_mod.memory_hook_installed():
                    hooks_mod.install_memory_hook()
                flash(f"Per-prompt memory hook {'ENABLED' if new_state else 'disabled'} "
                      f"for this project", ok=new_state, secs=1.8)
            except Exception as e:
                flash(f"Hook toggle failed: {e}", ok=False, secs=2)
        elif ev[0] == 'char' and ev[1] == 'a':
            q = text_input("Ask about this project:")
            if q:
                try:
                    from . import memory
                    ans = memory.ask_memory(project_path, proj_folder, q)
                except Exception as e:
                    ans = f"(failed: {e})"
                pager(('CLAUDECTL', project_name, 'ASK'),
                      (ans or '(no answer)').splitlines(), hint='ESC back')
