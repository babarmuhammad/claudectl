"""Test that the built GUI HTML contains flicker-prevention structures.

QtWebEngine composites the page through a GPU hardware surface on Windows, and
anything that makes the compositor read the framebuffer back — a blur, a filter,
a blend mode — tears the surface swap. A plain Chromium tab handles the same DOM
fine, so a glitch in the Qt shell but NOT the browser is always the embedding,
not the markup. See the CLAUDE.md gotcha.

The rules that fall out of that, and which these tests pin:
  · animate transform and opacity only; both are compositor-only
  · ONE requestAnimationFrame chain for the whole app, and it parks when idle
  · never write DOM on a poll tick unless the value actually changed
  · no backdrop-filter / blur / mix-blend-mode anywhere
"""
import re

from claude_sessions.gui_html import PAGE

#: the page with every /* comment */ and // note stripped. Needed because these
#: tests search for the very constructs the comments explain — a comment saying
#: "backdrop-filter was removed because…" would otherwise fail the test asserting
#: backdrop-filter is gone.
def _decomment(src):
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    return re.sub(r'^\s*//.*$', '', src, flags=re.M)


_CSS = _decomment(PAGE[PAGE.index('<style>'):PAGE.index('</style>')])
_CODE = _decomment(PAGE)


def _keyframes(css):
    """(name, body) for every @keyframes block, brace-matched.

    A regex can't do this: keyframe bodies nest one level (`0%{…}`), and a
    non-greedy `\\{.*?\\}` stops at the first inner close brace while a greedy one
    runs to the end of the stylesheet."""
    out = []
    for m in re.finditer(r'@keyframes\s+([\w-]+)\s*\{', css):
        i = m.end() - 1
        depth = 0
        for j in range(i, len(css)):
            if css[j] == '{':
                depth += 1
            elif css[j] == '}':
                depth -= 1
                if depth == 0:
                    out.append((m.group(1), css[i + 1:j]))
                    break
    return out


def test_poll_uses_cache_vars():
    """The job poll must skip DOM writes when values are unchanged. It runs every
    600ms; an unconditional write destroys and recreates text nodes each tick."""
    assert '__plMsgs' in PAGE, 'expected __plMsgs cache variable'
    assert '__plSub' in PAGE, 'expected __plSub cache variable'
    assert '__plLabel' in PAGE, 'expected __plLabel cache variable'
    assert 'msgsHtml!==__plMsgs' in PAGE, 'expected conditional DOM update for messages'
    assert 'innerHTML=msgsHtml' in PAGE, 'expected innerHTML for messages only'
    # the inline presentation needs the same discipline — it polls identically
    assert 'host.__jsub' in PAGE and 'host.__jmsgs' in PAGE


def test_poll_uses_textcontent():
    """Label/sub are text, so they must be written with textContent. innerHTML
    would reparse and rebuild the node on every tick."""
    assert 'lab.textContent=J.label' in PAGE, 'expected textContent for label'
    assert "$('#jSub').textContent=sub" in PAGE, 'expected textContent for sub (elapsed)'
    assert 'sub.textContent=J.sub' in PAGE, 'expected textContent for the inline sub'


def test_loading_bar_exists():
    """The loading bar infrastructure must be present."""
    assert 'id="loading"' in PAGE, 'expected loading bar element'
    assert 'setLoading' in PAGE, 'expected setLoading() function'
    assert '__loadingCount' in PAGE, 'expected __loadingCount variable'


def test_escape_closes_modals():
    """Escape key handler must close all modals."""
    assert "'Escape'" in PAGE, 'expected Escape key handler'
    assert "classList.remove('show')" in PAGE, 'expected class-based modal close'


def test_modals_have_aria():
    """Modals should have dialog role for accessibility."""
    assert 'role="dialog"' in PAGE, 'expected role="dialog" on modals'
    assert 'aria-modal="true"' in PAGE, 'expected aria-modal="true" on modals'


def test_no_backdrop_filter():
    """A blurred backdrop makes QtWebEngine's compositor read back and reblur the
    whole framebuffer every composite — the original flicker source."""
    assert 'backdrop-filter:' not in _CODE


def test_no_blurred_or_readback_layers():
    """Belt-and-braces alongside test_no_backdrop_filter: the pointer spotlight
    and the travelling border are both new gradient effects, and either one could
    have reached for a blur to soften itself."""
    for dead in ('backdrop-filter:', 'filter:blur', 'mix-blend-mode:'):
        assert dead not in _CODE, dead


def test_header_status_chips_are_styled():
    """.hchip/.hdrchips are emitted by refreshDashboard; without CSS they render
    as one unspaced run ('failover:20129mcp 1/10')."""
    assert '.hdrchips{' in PAGE and '.hchip{' in PAGE and '.hchip.ok{' in PAGE


def test_plan_exec_failure_is_persistent():
    """A plan job runs for minutes — its failure must outlive a 3.5s toast."""
    assert 'peDismissError' in PAGE and '.perun.err{' in PAGE


def test_long_jobs_back_off():
    """A job that has run a minute will not finish in the next 600ms; polling it
    at that rate is pure round-trips."""
    assert 'PE.elapsed>60?4000' in PAGE
    assert 'J.elapsed>60?4000' in PAGE


def test_no_page_can_paint_over_the_one_you_are_on():
    """A slow renderer must not overwrite the page you navigated to.

    This used to assert one renderer's local variable name, which is exactly why
    it kept passing while the bug was live: NAV_ID existed from the start and
    exactly ONE of fifteen async renderers remembered to check it, so a slow page
    landed on top of whatever you had moved to —

        "la pagina MCP ci mette un po' a caricare… quando si carica sovrascrive
         la pagina su cui stai davvero. Ero in settings ma con la schermata di MCP."

    A convention living in fifteen call sites is not a guarantee. The guard is
    now inside the only two functions allowed to touch #content, and what this
    test pins is that no sixteenth renderer can go around them."""
    assert 'let NAV_ID=0' in PAGE
    assert 'function paint(nav,html)' in PAGE
    assert 'if(nav!==NAV_ID)return false;' in PAGE
    # every write must be one of the two helpers, and nothing else
    writes = re.findall(r"\$\('#content'\)\.innerHTML\s*=", PAGE)
    assert len(writes) == 2, f'{len(writes)} direct #content writes — must go through paint()'
    # and the router has to take its token before it starts fetching
    assert 'const nav=paintNow(LOADING);' in PAGE
    # the renderer map collapsed into the NAV tuple, so pin the PROPERTY —
    # drawPage hands its token to whatever it dispatches to — rather than the
    # shape of the dispatch, which is what made this line need editing.
    router = PAGE[PAGE.index('async function drawPage(id){'):]
    router = router[:router.index('\n}')]
    assert 'const nav=paintNow(LOADING);' in router
    assert '(nav)' in router.split('paintNow(LOADING);', 1)[1], (
        'drawPage does not thread the nav token into the renderer')


def test_a_finished_job_cannot_repaint_a_view_you_have_left():
    """The same bug as the test above, one level up.

    A job outlives navigation on purpose — that is what the inline banner is
    for — and its completion handler used to call `drawMemory()` /
    `drawPage('skills')` unconditionally. `paint()`'s NAV_ID check cannot see
    that: `onDone` starts a NEW render, so the token matches and the write
    lands. Building memory and walking away put you back on the memory tab
    minutes later, over whatever you had opened.

    So the refresh moved into the runner as `redraw`, gated on the view the job
    was started from, and no handler may repaint by hand.
    """
    assert 'function viewKey()' in PAGE
    assert 'function jobRedraw(J,st){' in PAGE
    assert "if(viewKey()!==J.from)return;" in PAGE
    # every completion handler in the file: nothing there may draw. The line is
    # cut at `redraw:` — that half IS the guarded path and is allowed to draw.
    for m in re.finditer(r'onDone:([^\n]*)', _CODE):
        body = m.group(1).split('redraw:')[0]
        assert not re.search(r'\b(drawPage\(|draw[A-Z]\w*\()', body), (
            'an onDone handler repaints directly — use redraw: %r' % body[:90])


def test_the_theme_gallery_does_not_apply_on_hover():
    """Click to select. Hover-preview was unwanted, and expensive with it:
    applyTheme reaches STAGE.setTheme, which disposes and rebuilds the whole
    three.js scene — so sweeping the pointer across the gallery rebuilt one
    scene per card it crossed."""
    for dead in ("card.addEventListener('mouseenter',()=>applyTheme(n))",
                 "card.addEventListener('mouseleave',()=>applyTheme(ST.theme))",
                 'applySkin(n||skinFor(ST.theme));'):
        assert dead not in PAGE, dead
    assert "card.addEventListener('click',()=>themePick(card.dataset.v));" in PAGE


def test_esc_escapes_quotes_without_dom():
    """esc() feeds attribute values too, so quotes must be escaped; the old
    textContent round-trip escaped only & < > and allocated a node per call."""
    esc = PAGE[PAGE.index('function esc(s)'):]
    esc = esc[:esc.index('\n')]
    assert 'createElement' not in esc
    assert "'\"':'&quot;'" in PAGE


# ── the motion layer ──────────────────────────────────────────
# These replaced the test_live_layer_* cases. Those pinned an ambient-wallpaper
# layer: 26 generative canvas renderers mounted into ~30 places, looping at
# 6-30fps whether or not anything had happened. The invariants below are the same
# ones — one scheduler, bounded backing store, unmount on detach, reduced-motion
# honoured — asserted against instruments that tween to a value and then stop.


def test_exactly_one_raf_chain_in_the_app():
    """Gauges, number tweens, the vendored anime engine and the 3D background all
    register into ONE loop (MO.frame). Two chains was the previous state — the
    live layer plus the dashboard constellation — and it put both on the
    compositor independently.

    anime.js is the interesting one: it ships its own rAF loop and would have
    quietly become a second chain, so useDefaultMainLoop is turned off and
    MO._loop drives engine.update() instead."""
    assert PAGE.count('_loop(now)') == 1
    # entered from exactly two places: kick(), and the loop's own tail
    assert PAGE.count('requestAnimationFrame(t => this._loop(t))') == 2
    assert 'A.engine.useDefaultMainLoop = false;' in PAGE
    # the stage renders from a registered job, never from a loop of its own
    stage = PAGE[PAGE.index('const STAGE = {'):PAGE.index('window.STAGE = STAGE;')]
    assert 'requestAnimationFrame' not in stage, 'the stage started its own chain'
    assert 'MO.frame(dt => this._tick(dt))' in stage
    # no renderer may start a loop of its own
    for dead in ('function liveLoop', 'function constLoop', 'function moLoop'):
        assert dead not in PAGE, dead


def test_the_frame_loop_parks_when_nothing_is_visible():
    """The property this has always protected is "no frames when nothing is
    visible" — NOT "no frames ever". Those were the same statement while every
    job was a value tween that settled; the background stage makes them differ,
    because a scene that is meant to be running has nothing to settle to.

    So the rule is now stated exactly. The chain still drops jobs that return
    falsy and stops once the set empties, which is what makes an idle dashboard
    with the stage off render zero frames. And with the stage ON it still refuses
    to run while the page is hidden or the window blurred — Qt reports a
    minimized window as 'visible', which is why MO.vis exists alongside
    document.hidden.

    tools/smoke_gui.py exercises both halves against a real browser; string
    matching cannot prove a loop parks."""
    assert '// parked' in PAGE
    assert 'if (!this._jobs.size) return;' in PAGE
    assert 'this.job = null;' in PAGE
    assert 'if (document.hidden || !this.vis) return;' in PAGE
    assert 'function setVis(v)' in PAGE
    # the one long-lived job has to be named as such where the rule is stated,
    # so the next person does not read the exception as a bug and "fix" it
    assert 'ONE job is allowed to stay registered indefinitely' in PAGE
    # …and every way it can be switched off must exist
    stage = PAGE[PAGE.index('const STAGE = {'):PAGE.index('window.STAGE = STAGE;')]
    assert "this.tier === 'off'" in stage
    assert '!MO.on' in stage, 'motion:off must stop the stage'
    assert 'MO.unframe(this._job)' in stage, 'no way to deregister the stage'


def test_instruments_use_one_shared_draw_path():
    """One resize/clear/draw dance for every gauge. Duplicating it per renderer is
    what made adding a surface expensive in the old layer."""
    assert PAGE.count('setTransform(dpr, 0, 0, dpr, 0, 0)') == 1
    assert PAGE.count('draw(force, dt)') == 1


def test_instruments_are_frame_capped_and_dpr_bounded():
    """A gauge has nothing to say at 60fps, and an unbounded backing store costs
    4x the fill on a 4K panel. DPR is capped at 2 rather than the ambient layer's
    1: these draw 1px ticks and hairline arcs, which alias badly at 1x."""
    assert 'const INST_FPS = 30;' in PAGE
    assert 'if (this.acc < 1 / INST_FPS) return true;' in PAGE
    assert 'Math.min(window.devicePixelRatio || 1, 2)' in PAGE


def test_instruments_unmount_detached_canvases():
    """The SPA rewrites #content wholesale; targets pointing at dead nodes must
    drop out or the registry grows without bound."""
    assert 'if (t.el.isConnected) continue;' in PAGE
    assert 'this.reg.splice(i, 1)' in PAGE
    assert 'const INST_MAX = 24;' in PAGE


def test_motion_respects_reduced_motion():
    """An OS reduce-motion preference overrides the app setting, and must stop the
    WAAPI/JS animations too — a CSS `transition:none` alone would not."""
    assert "MO_REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches" in PAGE
    assert "this.level = MO_REDUCED ? 'off' : (level || 'full');" in PAGE
    assert '@media (prefers-reduced-motion: reduce)' in PAGE


def test_animations_target_only_compositor_properties():
    """Every keyframe must animate transform/opacity (or the registered custom
    property the border angle uses), never a paint property. The old shimmer
    animated background-position and repainted the element on every step."""
    blocks = _keyframes(_CSS)
    assert blocks, 'no keyframes found'
    allowed = {'transform', 'opacity', '--beam-a'}
    for name, body in blocks:
        props = set(re.findall(r'([-\w]+)\s*:', body))
        bad = props - allowed
        assert not bad, f'@keyframes {name} animates paint properties: {bad}'


def test_no_frame_throttling_steps():
    """steps(8)/steps(20)/steps(30) were frame-rate throttles — a workaround for
    animating PAINT properties, trading smoothness for fewer invalidations. Every
    keyframe is transform/opacity now, so the compositor owns them and stepping
    buys nothing.

    steps() as a deliberate aesthetic still stands, and it is a short list: the
    CRT caret must snap rather than fade (a caret that eases is not a caret), and
    the Cyberpunk hover must jump between displacements (a glitch that eases is
    not a glitch). Those are design decisions, not frame-rate workarounds — the
    difference is that both animate `transform`, so the compositor still owns
    them and stepping costs nothing."""
    for dead in ('steps(8)', 'steps(20)', 'steps(30)'):
        assert dead not in _CSS, dead
    stepped = re.findall(r'animation:\s*([\w-]+)[^;}]*steps\(', _CSS)
    assert set(stepped) <= {'crtcaret', 'cyglitch'}, \
        f'unexpected stepped animation: {stepped}'


def test_custom_property_animation_is_feature_detected():
    """@property is Chrome 85+; QtWebEngine's Chromium is older on some Qt builds.
    The travelling border must degrade to a static frame there, not break —
    color-mix() was skipped for exactly this reason."""
    assert 'CSS.registerProperty' in PAGE
    assert 'MO_HAS_PROP' in PAGE
    assert 'html:not(.mo-beam) .beam{' in PAGE


def test_lists_reconcile_instead_of_rebuilding():
    """setV() caches by HTML string, so any change rebuilt every row — losing
    hover state and making one session finishing look like the list flashing."""
    assert 'patch(host, items, keyOf, html, cls)' in PAGE
    assert 'appendChild on an existing child is a move' in PAGE
    for host in ("$('#dashRecent')", "$('#dashProjects')"):
        assert f'MO.patch({host}' in PAGE, host
    assert 'MO.patch(box,list,p=>p.encoded' in PAGE, 'sidebar rows still rebuilt'


def test_jobs_run_inline_and_escalate_to_the_modal():
    """Jobs must not hold the UI hostage for the minutes they spend in a Claude
    call. They start inline and take the modal only when the job actually parks at
    an approval gate — reacting to what the job did, not to a per-kind allowlist
    that would go stale the moment another code path called diffview.confirm."""
    assert 'function inlineJob(host,kind,params,o)' in PAGE
    assert 'if(!J.modal){J.modal=true;inlineClear(J);}   // escalate' in PAGE
    for kind in ('memory_build', 'review', 'ai_scaffold', 'ai_compress',
                 'lessons_scan', 'rules_sync', 'agent_ai', 'skill_ai', 'hook_ai'):
        assert f"inlineJob('#jban','{kind}'" in PAGE, kind
        assert f"runJob('{kind}'" not in PAGE, f'{kind} still opens the modal'


def test_instrument_refit_is_observer_driven():
    """The Qt shell and the 720px transcript drawer both change #content's width
    WITHOUT firing a window resize, so onresize alone leaves gauges mis-sized."""
    assert 'new ResizeObserver(()=>INST.refit())' in PAGE


def test_an_unfocused_window_stops_painting_and_leaves_no_hole():
    """"When I'm on another app it flickers a lot" — three separate causes.

    The rAF chain already parked correctly on blur (`motion.js` refuses to
    reschedule while `!MO.vis`). What did not stop was CSS, and what was left
    behind was a hole:

    1. `stage-blur` hides #stage INSTANTLY (`visibility` is not in its
       transition list) while the two washes fade back over 500ms, and
       `html.stage-on body` is transparent with no `html{background}` anywhere
       — so the ground for that half second was the root canvas colour.
    2. A world's `.ovl-fx` is a full-viewport fixed layer on a 1.1s transform
       loop, gated only by `mo-off`.
    3. `.spin` / `.beam` / `.pip` / `.shimmer` / the CRT caret / the four
       `.ov-*` drifts all keep running: Chromium throttles keyframes when a page
       is HIDDEN, and a merely-unfocused window is not hidden — least of all
       under Qt, which reports even a minimised one as visible.
    """
    # the class must come from setVis, so it is independent of the stage: STAGE
    # .blur() returns early with no canvas, so `stage-blur` never appears with
    # stage:off, a dead GL context, or the static fallback tier
    assert "classList.toggle('win-blur',!v)" in _CODE, \
        'setVis does not mark the window unfocused'
    assert 'html.win-blur body{background:var(--bg)}' in _CSS, \
        'the blur swap still falls through to the root canvas colour'
    assert 'html.win-blur .ovl-fx{display:none}' in _CSS, \
        'a world overlay still animates while the window is in the background'
    assert 'animation-play-state:paused' in _CSS, \
        'infinite CSS animations still run while unfocused'
    # paused, not ended: mo-off's `animation-duration:.01ms;iteration-count:1`
    # would bring a spinner back stopped
    assert 'html.win-blur *' in _CSS


def test_the_blur_swap_is_debounced_but_the_return_is_not():
    """Qt fires blur/focus pairs on things that are not really a focus change
    (a native menu opening, a child window taking focus for a frame). Each pair
    cost a full background swap plus stopDashboard/startDashboard — an immediate
    re-fetch and a whole-page repaint. Going away waits; coming back must not,
    because a delay there is the one you would actually notice.

    The dashboard half was left OUT of the debounce when this was written — the
    handlers called stopDashboard/startDashboard directly, so every spurious
    pair still paid the round trip the debounce existed to remove. Both halves
    live inside setVisSoon now, which is what the docstring above always
    claimed.
    """
    assert 'function setVisSoon(v)' in _CODE
    assert 'if(v){setVis(true);if(PAGE_===\'home\')startDashboard();return;}' in _CODE, \
        'focus is delayed too, or the dashboard restart is outside the debounce'
    assert 'setVis(false);stopDashboard();},150)' in _CODE, \
        'the dashboard teardown is not debounced with the swap'
    assert "window.addEventListener('blur',()=>setVisSoon(false))" in _CODE
    assert "window.addEventListener('focus',()=>setVisSoon(true))" in _CODE


def test_a_pending_blur_cannot_fire_on_a_page_that_came_back():
    """`visibilitychange` calls setVis DIRECTLY, bypassing the timer. A blur that
    armed the 150ms debounce, followed by the tab becoming visible inside that
    window, left the timer to run setVis(false) on a focused page — animations
    paused and the GL surface down, with nothing to clear it until the next
    focus event. Cancelling inside setVis covers every caller, present and
    future; cancelling in setVisSoon only covered the two that went through it.
    """
    body = _CODE[_CODE.index('function setVis(v)'):]
    body = body[:body.index('function setVisSoon')]
    assert 'clearTimeout(_VIS_T)' in body, \
        'setVis does not cancel a pending debounce'
    assert body.index('clearTimeout(_VIS_T)') < body.index('_VIS=v'), \
        'the cancel must happen before the state changes'


def test_the_qt_page_background_follows_the_palette():
    """It was hardcoded to #0d1117 on the reasoning that "the GUI has no light
    theme". It has four light palettes and six at #050505, and this colour is
    what shows through both the first paint and the blur swap — so on any of
    those it WAS the flash rather than the fix."""
    import inspect
    import claude_sessions.config as cfg
    from claude_sessions import gui_qt
    from claude_sessions.themes import PALETTES, WORLDS

    src = inspect.getsource(gui_qt.run_desktop)
    assert 'setBackgroundColor(QColor(_page_bg()))' in src
    # comments stripped for the same reason this module strips them from PAGE:
    # the comment explaining the old hardcoded value would fail the check
    code = re.sub(r'^\s*#.*$', '', src, flags=re.M)
    assert not re.search(r'#[0-9a-fA-F]{6}', code), \
        'run_desktop names a colour literally again'

    # and it really tracks the setting, including a world's palette override
    real = cfg.load_settings
    try:
        for theme, world, want in (
                ('paper', '', PALETTES['paper']['bg']),
                ('oled-red', '', PALETTES['oled-red']['bg']),
                ('paper', 'graph', PALETTES[WORLDS['graph']['palette']]['bg']),
                ('no-such-theme', '', PALETTES['default']['bg'])):
            cfg.load_settings = lambda t=theme, w=world: {'theme': t, 'world': w}
            assert gui_qt._page_bg() == want, (theme, world)
    finally:
        cfg.load_settings = real
