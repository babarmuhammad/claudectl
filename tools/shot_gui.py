"""Screenshot the GUI against realistic stub data and audit for layout overflow.

Companion to tools/smoke_gui.py: that one checks behaviour, this one checks fit.
The overflow audit is the useful part — it walks every dashboard card and reports
any descendant whose box sticks out past it, which is how the oversized gauges
were caught (a square gauge in a width:100% slot grew as tall as the card was
wide and spilled its label out of the bottom).

    py -3 tools/shot_gui.py [outdir]
"""
import importlib.util
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_spec = importlib.util.spec_from_file_location('sg', os.path.join(_ROOT, 'tools', 'smoke_gui.py'))
sg = importlib.util.module_from_spec(_spec)
sg.__name__ = 'sg'
_spec.loader.exec_module(sg)

#: a positional argument overrides the scratch directory; flags are not it
_ARGS = [a for a in sys.argv[1:] if not a.startswith('-')]
OUT = _ARGS[0] if _ARGS else os.path.join(_ROOT, 'references')
PORT = 8801

# a workspace the size of a real one: 13 projects, 10 MCP servers, 3 accounts.
# Small stub data hides exactly the bugs that matter — an oversized gauge looks
# fine next to two projects and breaks the card next to thirteen.
_N = time.time()
# Deliberately fictional. These fixtures feed the README screenshots, so
# anything copied from a real workspace becomes a published project list.
_PROJ = [('acme-api', 4199000, 'now', ['default', 'teamA']),
         ('acme-web', 4562000, '12h', ['default', 'teamA']),
         ('acme-docs', 163000, '6h', ['default']),
         ('forecasting-model', 48000, '8h', ['default']),
         ('checkout-service', 91000, '1d', ['default', 'teamA']),
         ('vision-finetune', 22000, '2d', ['default']),
         ('mobile-client', 18000, '3d', ['default']),
         ('billing-worker', 9000, '4d', ['teamA']),
         ('search-index', 7000, '5d', ['default']),
         ('infra-terraform', 5000, '6d', ['teamB']),
         ('design-system', 4000, '7d', ['default']),
         ('data-pipeline', 3000, '8d', ['teamA']),
         ('scratchpad', 2000, '9d', ['default'])]
sg.DASH['breakdown']['projects'] = [
    {'name': n, 'enc': n.lower(), 'tokens': t, 'cost': t / 2e5, 'age': a,
     'mtime': _N - i * 4000, 'accounts': acc, 'omni': i % 3 == 0,
     'sparkline': [(i * j) % 9 + 1 for j in range(7)]}
    for i, (n, t, a, acc) in enumerate(_PROJ)]
sg.DASH['mcp'] = [{'name': 'server-%d' % i, 'running': i == 0} for i in range(10)]
sg.ROUTES['/api/mcp'] = {'servers': [{'name': 'server-%d' % i,
                                      'status': 'ok' if i == 0 else 'down'}
                                     for i in range(10)]}
_ACCTS = [('default', 87, '01:40', 59, 'Sun 09:00'),
          ('teamA', 82, '03:09', 57, 'Sat 08:59'),
          ('teamB', 45, '04:50', 48, 'Mon 02:00')]
sg.PLAN['accounts'] = [
    {'account': n, 'email': n, 'plan': 'max', 'status': 'ok',
     'windows': [{'label': 'session', 'pct': sp, 'resets': sr},
                 {'label': 'weekly', 'pct': wp, 'resets': wr}]}
    for n, sp, sr, wp, wr in _ACCTS]
sg.STATE['accounts'] = [{'name': n, 'dir': n, 'active': n == 'default'}
                        for n, *_ in _ACCTS]
sg.STATE['projects'] = [
    {'name': p['name'], 'path': '/demo/' + p['name'],
     'encoded': p['enc'], 'accounts': p['accounts'], 'primary_cfgdir': '',
     'auto_memory': i < 2, 'last_active': p['age']}
    for i, p in enumerate(sg.DASH['breakdown']['projects'])]

# A child of a scrolling container legitimately has a rect outside it — that is
# what scrolling means — so those are skipped or every long list reads as broken.
OVERFLOW_JS = """[...document.querySelectorAll('.dash>.card')].map(c=>{
  const cb=c.getBoundingClientRect();const bad=[];
  const clipped=e=>{
    for(let p=e.parentElement;p&&p!==c.parentElement;p=p.parentElement){
      const o=getComputedStyle(p).overflowY;
      if(o==='auto'||o==='scroll'||o==='hidden')return true;}
    return false;};
  c.querySelectorAll('*').forEach(e=>{
    const b=e.getBoundingClientRect();
    if(!b.height||clipped(e))return;
    const over=Math.max(b.bottom-cb.bottom,b.right-cb.right);
    if(over>1.5)bad.push((typeof e.className==='string'?e.className.split(' ')[0]:e.tagName)
      +'+'+Math.round(over)+'px');});
  return (c.className.match(/d-[\\w]+/)||['?'])[0]+': '+(bad.length?bad.join(', '):'clean');})"""


# Does the effort slider's thumb land on the label it names?
#
# Nothing here could answer that before: the audit walked boxes for overflow,
# and a label sitting under the WRONG tick overflows nothing. index.html carried
# six hand-typed labels while config.EFFORTS had grown to seven, so at xhigh the
# thumb sat at 4/6 of the track — under HIGH — and `ultracode` had no label at
# all. Every audit passed, partly because the stub offered two efforts.
#
# The thumb position is computed the way a browser lays a range out: centre to
# centre, inset by half the thumb at each end.
TICKS_JS = """(()=>{
  const sl=document.querySelector('#fEffort'),host=document.querySelector('#fEffTicks');
  if(!sl||!host)return ['effort slider or tick row missing'];
  const r=sl.getBoundingClientRect(),spans=[...host.querySelectorAll('span')],n=+sl.max;
  const thumb=parseFloat(getComputedStyle(document.documentElement)
      .getPropertyValue('--thumb'))||16;
  const bad=[];
  if(spans.length!==n+1)bad.push(`${n+1} stops but ${spans.length} labels`);
  const was=sl.value;
  for(let v=0;v<=n&&v<spans.length;v++){
    sl.value=v; sl.dispatchEvent(new Event('input'));
    const x=r.left+thumb/2+(n?v/n:0)*(r.width-thumb);
    const s=spans[v].getBoundingClientRect();
    const drift=Math.round(x-(s.left+s.width/2));
    if(Math.abs(drift)>12)
      bad.push(`${spans[v].textContent} thumb ${drift}px off its label`);
    const read=document.querySelector('#fEffLabel').textContent.trim();
    if(!read.includes(spans[v].textContent))
      bad.push(`label ${spans[v].textContent} but readout ${read}`);
  }
  sl.value=was; sl.dispatchEvent(new Event('input'));
  return bad;
})()"""

# Same idea as OVERFLOW_JS but scoped to whatever modal is open.
MODAL_JS = """(()=>{const m=document.querySelector('.ovl.show .modal');
  if(!m)return [];const mb=m.getBoundingClientRect();const bad=[];
  m.querySelectorAll('*').forEach(e=>{
    const b=e.getBoundingClientRect(); if(!b.height)return;
    for(let p=e.parentElement;p&&p!==m.parentElement;p=p.parentElement){
      const o=getComputedStyle(p).overflowY;
      if(o==='auto'||o==='scroll'||o==='hidden')return;}
    const over=Math.max(b.bottom-mb.bottom,b.right-mb.right);
    if(over>1.5)bad.push((typeof e.className==='string'?e.className.split(' ')[0]
      :e.tagName)+'+'+Math.round(over)+'px');});
  return [...new Set(bad)];})()"""

# A pill radius is right for something one line tall and catastrophic for
# anything taller: 999px on a 130px-high card is an ellipse. Flag any element
# whose corner radius exceeds half its own height while being visibly tall.
OVAL_JS = """(()=>{const m=document.querySelector('.ovl.show .modal');
  if(!m)return [];const out=[];
  m.querySelectorAll('*').forEach(e=>{
    const b=e.getBoundingClientRect();
    if(b.height<40||b.width<40)return;
    const r=parseFloat(getComputedStyle(e).borderTopLeftRadius)||0;
    if(r>b.height/2)out.push((typeof e.className==='string'
      ?e.className.split(' ')[0]:e.tagName)+' r='+Math.round(r)
      +' h='+Math.round(b.height));});
  return [...new Set(out)];})()"""


# The same two probes rooted anywhere. Manager pages are cards too, and they
# were as unaudited as modals were before the ellipse: `.preset` now appears on
# the Output styles page as well as in the launch modal, and a row of buttons in
# an `.hrow` overflows exactly the way a card's contents do.
def _rooted(js, root):
    return js.replace("document.querySelector('.ovl.show .modal')", root)


PAGE_ROOT = "document.querySelector('#content')"


def audit_page(pg, label, settle=6000):
    """Overflow + oval audit of one page. Waits for the page to actually RENDER
    first.

    A page still showing its spinner has no cards, so every probe below returns
    an empty list and it prints `clean` — a pass earned by measuring nothing.
    That is the same failure `smoke_gui`'s check floor exists for, one layer
    down: the memory tab grew a fourth fetch, went over the fixed 800ms wait,
    and was audited as a spinner for exactly as long as nobody looked at the
    screenshot."""
    try:
        pg.wait_for_function(
            "()=>{const c=document.querySelector('#content');"
            "return c && !c.querySelector('.spin') && c.querySelector('.card,.slist,.dash');}",
            timeout=settle)
    except Exception:
        print(f'  {label:<11} NEVER RENDERED (still loading after {settle}ms)')
        return False
    bad = pg.evaluate(_rooted(MODAL_JS, PAGE_ROOT))
    ovals = pg.evaluate(_rooted(OVAL_JS, PAGE_ROOT))
    state = ('OVERFLOW ' + '; '.join(bad)) if bad else             ('OVAL ' + '; '.join(ovals)) if ovals else 'clean'
    print(f'  {label:<11} {state}')
    return state == 'clean'


def main():
    from playwright.sync_api import sync_playwright
    os.makedirs(OUT, exist_ok=True)
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), sg.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    errs = []
    with sync_playwright() as pw:
        # SwiftShader, so the screenshots actually contain the stage rather than
        # the static-gradient fallback (same reason as smoke_gui).
        br = pw.chromium.launch(args=[
            '--use-gl=angle', '--use-angle=swiftshader',
            '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'])
        pg = br.new_page(viewport={'width': 1600, 'height': 1000})
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto(f'http://127.0.0.1:{PORT}/')
        pg.wait_for_timeout(2500)
        print('stage:', pg.evaluate(
            "STAGE.ok?('live · '+STAGE.scene+(STAGE._post?' + bloom':'')):'FALLBACK'"))

        print('— overflow audit (dashboard cards) —')
        for line in pg.evaluate(OVERFLOW_JS):
            print('  ' + line)
        print('\ncard heights:', pg.evaluate(
            "[...document.querySelectorAll('.dash>.card')].map(c=>"
            "(c.className.match(/d-[\\w]+/)||['?'])[0]+':'"
            "+Math.round(c.getBoundingClientRect().height))"))
        print('instrument row equal height:', pg.evaluate(
            "(()=>{const h=['d-i1','d-i2','d-i3','d-i4'].map(k=>Math.round("
            "document.querySelector('.'+k).getBoundingClientRect().height));"
            "return h.join('/')+(new Set(h).size===1?' ✓':' RAGGED');})()"))
        print('skeletons still on screen:',
              pg.evaluate("document.querySelectorAll('.shimmer').length"))
        print('readouts:', pg.evaluate(
            "[...document.querySelectorAll('.iread b')].map(e=>e.textContent)"))

        for name, w, h in (('dash', 1600, 1000), ('dash-narrow', 820, 1000)):
            pg.set_viewport_size({'width': w, 'height': h})
            pg.wait_for_timeout(700)
            pg.screenshot(path=os.path.join(OUT, f'_shot_{name}.png'))
        pg.set_viewport_size({'width': 1600, 'height': 1000})
        for page in ('usage', 'mcp', 'accounts', 'logs', 'settings'):
            pg.evaluate(f"go('{page}')")
            pg.wait_for_timeout(800)
            pg.screenshot(path=os.path.join(OUT, f'_shot_{page}.png'))

        # ── modals ──
        # The overflow audit only ever walked `.dash>.card`, so nothing in this
        # tool had ever looked at a modal. That is how a pill radius applied to
        # the launch presets shipped: every preset rendered as an ELLIPSE with
        # its own text outside the shape, on a screen the audit could not see.
        print(chr(10) + '— modal audit —')
        for name, opener in (
            ('launch', "askLaunch({title:'New session',sub:'Demo',isNew:true,"
                       "path:'/demo/acme-api',enc:'demo-acme-api',"
                       "choice:'new',cfgdir:''})"),
            ('guide', "openGuide&&openGuide()"),
            # the activity drawer renders from DASH_ACT, which the dashboard
            # poll has already populated by the time the audit reaches here
            ('activity', "openActivity&&openActivity()"),
        ):
            try:
                pg.evaluate(opener)
            except Exception as e:
                print(f'  {name:<8} could not open — {str(e)[:60]}')
                continue
            pg.wait_for_timeout(500)
            shown = pg.evaluate(
                "!!document.querySelector('.ovl.show .modal')")
            if not shown:
                print(f'  {name:<8} did not open')
                continue
            bad = pg.evaluate(MODAL_JS)
            # a control whose corner radius exceeds half its own height is a
            # pill, and a pill that is not one line tall is an ellipse
            ovals = pg.evaluate(OVAL_JS)
            state = 'clean'
            if bad:
                state = 'OVERFLOW ' + '; '.join(bad)
            elif ovals:
                state = 'OVAL ' + '; '.join(ovals)
            print(f'  {name:<8} {state}')
            if name == 'launch':
                # the effort slider lives behind Advanced ▸ "Pin an exact model"
                pg.evaluate("(()=>{const d=document.querySelector('.ovl.show details');"
                            "if(d)d.open=true;const c=document.querySelector('#fPinModel');"
                            "if(c&&!c.checked){c.checked=true;"
                            "c.dispatchEvent(new Event('change'));}})()")
                pg.wait_for_timeout(300)
                ticks = pg.evaluate(TICKS_JS)
                print('  %-8s %s' % ('  ticks', '; '.join(ticks) if ticks
                                     else 'every stop on its own label'))
            pg.screenshot(path=os.path.join(OUT, f'_modal_{name}.png'))
            pg.evaluate("document.querySelectorAll('.ovl').forEach("
                        "o=>o.classList.remove('show'))")
            pg.wait_for_timeout(200)

        # ── manager pages ──
        print(chr(10) + '— page audit —')
        # from NAV rather than a hardcoded list — see the same fix in
        # smoke_gui: the list had fallen a page behind, and an unaudited page
        # is where a wide table quietly breaks card fit
        for page in pg.evaluate('NAV.map(n => n[0])'):
            pg.evaluate(f"go('{page}')")
            pg.wait_for_timeout(700)
            audit_page(pg, page)
            # agents/skills/hooks are three of the densest pages in the app and
            # each groups its rows under a heading now; the overflow audit sees
            # a card that fits, not a list that reads
            if page in ('plugins', 'ostyles', 'client',
                        'agents', 'skills', 'hooks'):
                pg.screenshot(path=os.path.join(OUT, f'_shot_{page}.png'))
        # ── project tabs ──
        # The page walk above only drives the GLOBAL pages; the project side is
        # where most of the app lives, and it had never been captured.
        print(chr(10) + '— project tabs —')
        pg.evaluate("openProject(ST.projects[0])")
        pg.wait_for_timeout(800)
        for tab in pg.evaluate('TABS.map(t => t[0])'):   # derived, not a 3-of-9 copy
            pg.evaluate(f"TAB='{tab}';go('project')")
            pg.wait_for_timeout(800)
            audit_page(pg, 'tab ' + tab)
            pg.screenshot(path=os.path.join(OUT, f'_shot_tab_{tab}.png'))
        pg.evaluate("go('home')")
        pg.wait_for_timeout(600)

        # ── per-skin pass ──
        # A skin changes card geometry, so it can break fit in ways the default
        # skin never would (a 3px border and a hard shadow, a clip-path, a
        # 0-radius panel). Shoot and audit every one.
        print('\n— per-skin audit —')
        pg.evaluate("go('home')")
        pg.wait_for_timeout(900)
        # worlds first (each locks its own palette), then the classic skins
        looks = ([('world', w) for w in pg.evaluate("Object.keys(ST.worlds||{})")]
                 + [('skin', s) for s in pg.evaluate("ST.classic_skins||[]")])
        for kind, sk in looks:
            if kind == 'world':
                pg.evaluate(f"ST.world='{sk}';applyTheme(ST.theme)")
            else:
                pg.evaluate(f"ST.world='';ST.skin='{sk}';applyTheme(ST.theme)")
            pg.wait_for_timeout(500)
            bad = [ln for ln in pg.evaluate(OVERFLOW_JS) if 'clean' not in ln]
            heights = pg.evaluate(
                "['d-i1','d-i2','d-i3','d-i4'].map(k=>Math.round("
                "document.querySelector('.'+k).getBoundingClientRect().height))")
            even = len(set(heights)) == 1
            state = 'clean' if (not bad and even) else (
                ('OVERFLOW ' + '; '.join(bad)) if bad else f'RAGGED {heights}')
            print(f'  {sk:<10} {state}')
            pg.screenshot(path=os.path.join(OUT, f'_skin_{sk}.png'))
        pg.evaluate("ST.world='';ST.skin='';applyTheme(ST.theme)")
        br.close()
    srv.shutdown()
    print('\nJS errors:', errs if errs else 'none')
    print('shots →', OUT)
    if '--docs' in sys.argv:
        export_docs()


#: the captures the README uses. `references/` is gitignored — it holds
#: third-party design material we cannot redistribute — so the handful of shots
#: that ship are copied into a tracked directory deliberately, by name, rather
#: than by publishing the whole scratch folder.
DOC_SHOTS = {
    '_shot_dash.png': 'gui-dashboard.png',
    '_shot_tab_sessions.png': 'gui-sessions.png',
    '_shot_tab_memory.png': 'gui-memory.png',
    '_shot_client.png': 'gui-claude-code.png',
    '_shot_usage.png': 'gui-usage.png',
    '_skin_graph.png': 'gui-skin-graph.png',
    '_skin_crt.png': 'gui-skin-crt.png',
}


def export_docs():
    import shutil
    dest = os.path.join(_ROOT, 'docs', 'img')
    os.makedirs(dest, exist_ok=True)
    n = 0
    for src, name in DOC_SHOTS.items():
        p = os.path.join(OUT, src)
        if not os.path.isfile(p):
            print('  MISSING', src)
            continue
        shutil.copyfile(p, os.path.join(dest, name))
        n += 1
    print('exported %d/%d shots → %s' % (n, len(DOC_SHOTS), dest))


if __name__ == '__main__':
    main()
