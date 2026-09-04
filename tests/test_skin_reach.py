"""Does a look actually reach the whole app, or only the cards?

This is the test this round exists for. The previous roster changed corner
radius, border weight, one surface texture and a heading font — about 20 of
~130 component selectors — so every theme still read as the same application
in a different hue, and the verdict was blunt:

    "Glass, Mecha e Sakura da buttare."
    "devono proprio cambiare la vista della gui, ma proprio tutta tutta,
     ogni piccola cosa"

The fix was not more per-skin CSS (7 x 130 rules is unmaintainable); it was a
token vocabulary that the components read, so a look writes ~30 tokens and the
whole interface follows. What keeps that true is this file: every component
family must be reached by a token or by a look-scoped rule, so the NEXT look
cannot quietly go back to being chrome-deep.
"""
import re

from claude_sessions.gui_html import PAGE
from claude_sessions.themes import SKINS, SKIN_KEYS, WORLDS

_CSS = re.sub(r'/\*.*?\*/', '',
              PAGE[PAGE.index('<style>'):PAGE.index('</style>')], flags=re.S)

#: family -> a selector that must be reached, and the token that reaches it.
#: Not exhaustive by design — one representative per family, because a family
#: that has any token at all was thought about.
FAMILIES = {
    'buttons':     ('.btn', '--sk-btn-r'),
    'segmented':   ('.seg', '--sk-btn-r'),
    'chips/tags':  ('.chip,.tag,.hchip', '--sk-pill-r'),
    'inputs':      ('.fld input', '--sk-in-r'),
    'cards':       ('.card', '--sk-r'),
    'list rows':   ('.sess,.proj', '--sk-row-r'),
    'tables':      ('.tbl td', '--sk-cell-pad'),
    'nav':         ('.nav .it', '--sk-nav-r'),
    'icons':       ('.nav .it .ic', '--sk-icon-plate'),
    'meters':      ('.bar', '--sk-bar-r'),
    'spinner':     ('.spin', '--sk-spin-r'),
    'scrollbars':  ('::-webkit-scrollbar', '--sk-sb-w'),
    'selection':   ('::selection', '--sk-sel-bg'),
    'focus':       (':focus-visible', '--sk-focus'),
    'drawer':      ('.drawer', '--sk-drawer-r'),
}


def test_every_component_family_is_token_driven():
    """Each family reads a token, so a look restyles it without touching CSS."""
    for family, (sel, token) in FAMILIES.items():
        first = sel.split(',')[0]
        assert first in _CSS, f'{family}: {first} not styled at all'
        assert token in _CSS, f'{family}: {token} never used — look cannot reach it'


def test_a_look_that_forgets_a_token_is_not_silently_half_applied():
    """A missing token falls back to the default and the look quietly resembles
    another one. Every skin declares every key."""
    for name, sk in SKINS.items():
        missing = [k for k in SKIN_KEYS if k not in sk]
        assert not missing, f'{name} is missing {missing}'


def test_each_world_restyles_far_more_than_a_card():
    """A world is supposed to change everything. Counting the selectors each one
    actually touches is the crudest possible proxy, and it is the one that would
    have caught the previous roster: Sakura reached four rules."""
    for name in WORLDS:
        rules = re.findall(r'html\.skin-%s\b[^{]*\{' % re.escape(name), _CSS)
        assert len(rules) >= 8, f'{name} touches only {len(rules)} rules — chrome-deep'
        # …and it must declare a block of tokens, not just paint a few surfaces
        block = re.search(r'html\.skin-%s\{([^}]*)\}' % re.escape(name), _CSS)
        assert block, f'{name} declares no tokens'
        assert block.group(1).count('--sk-') >= 8, \
            f'{name} sets only {block.group(1).count("--sk-")} tokens'


def test_every_world_has_the_parts_a_token_cannot_express():
    """Icons, overlay, hover, cursor: the things that make it a world rather
    than a skin. Each must exist on both sides."""
    for name, w in WORLDS.items():
        assert f"'{w['icons']}':{{" in PAGE.replace(' ', '') \
            or f'{w["icons"]}:{{' in PAGE.replace(' ', ''), f'{name}: no icon set'
        assert f'.ov-{w["overlay"]}' in _CSS, f'{name}: no overlay'
        assert f'html.world-{name}' in _CSS or f'.skin-{w["skin"]} .spot.hv' in _CSS, \
            f'{name}: no world-scoped rule'


def test_world_hover_is_scoped_and_costs_one_class():
    """Per-world hover rides the existing delegated listener — a class toggle,
    not a new handler per card.

    The count is of PERSISTENT pointermove listeners. A drag handle attaches one
    on pointerdown and removes it on pointerup, which is not the thing this
    guards against (a listener per card, alive for the life of the page) — so
    listeners added inside a handler are excluded, and the removal is asserted
    instead. Counting every occurrence would have made the next drag handle
    "fail" the world-hover rule it has nothing to do with.
    """
    assert "_mark(el)" in PAGE
    assert "el.classList.add('hv')" in PAGE
    persistent = len(re.findall(r"(?<!document\.)addEventListener\('pointermove'", PAGE))
    assert persistent == 1, 'a second always-on pointermove listener appeared'
    # every transient one must be torn down, or a drag keeps moving after mouseup
    assert (PAGE.count("document.addEventListener('pointermove'")
            == PAGE.count("document.removeEventListener('pointermove'")), \
        'a transient pointermove listener is never removed'
    for skin in ('anime', 'cyber', 'deck', 'graph'):
        assert f'html.skin-{skin} .spot.hv' in _CSS, skin


def test_overlays_cannot_swallow_clicks_or_survive_motion_off():
    """One element, above the app, inert, and killable.

    An overlay DOES drift — a world whose scanlines are frozen is not a world,
    which is what the first cut got wrong by treating brightness and motion as
    one knob. What has to stay true is everything else: exactly one element, it
    cannot take a click, `motion:off` removes it entirely, and its animation is
    compositor-only (the global keyframes audit in test_gui_flicker.py already
    rejects any property outside transform/opacity)."""
    assert PAGE.count('id="overlay"') == 1
    # BEHIND the app (negative z), never over it: on top, the scanlines crossed
    # the body text, the logo and the TUI/GUI toggle and read as a broken screen
    assert '.ovl-fx{position:fixed;inset:0;z-index:-1;pointer-events:none}' in _CSS
    assert 'html.mo-off .ovl-fx{display:none!important}' in _CSS
    for name in {w['overlay'] for w in WORLDS.values()}:
        assert re.search(r'\.ov-%s\{' % re.escape(name), _CSS), name
        anim = re.search(r'\.ov-%s\{animation:(\w+)' % re.escape(name), _CSS)
        assert anim, f'.ov-{name} does not drift — a frozen overlay is a texture'
        kf = re.search(r'@keyframes %s\{([^}]*\})' % re.escape(anim.group(1)), _CSS)
        assert kf, f'{anim.group(1)} has no keyframes'


def test_the_background_still_moves_when_nothing_is_happening():
    """Brightness and motion are separate knobs, and only brightness was the
    complaint. Turning both down produced a background that had stopped being
    animated at all — the regression this pins.

        calm = how bright (capped, that is the anti-overstimulating lever)
        flow = how much it moves (per skin, must stay well above zero)
    """
    from claude_sessions.themes import SKINS
    assert re.search(r'STAGE_FPS_IDLE = 2\d;', PAGE), 'idle fps too low to read as motion'
    m = re.search(r'this\._T \+= fdt \* flow \* \(([\d.]+)', PAGE)
    assert m, 'scene clock is not flow-scaled'
    assert float(m.group(1)) >= 0.4, f'idle time multiplier {m.group(1)} is frozen'
    for name, sk in SKINS.items():
        assert sk['flow'] >= 0.45, f'{name}: flow={sk["flow"]} is effectively static'
        assert sk['calm'] <= 0.5, f'{name}: calm={sk["calm"]} is back to overstimulating'


def test_a_world_locks_the_classic_pickers():
    """All or nothing. Leaving the palette gallery live while a world is on is
    just a way to get half a world."""
    assert 'function curWorld()' in PAGE
    assert 'if(w)return w.skin;' in PAGE, 'skinFor does not honour the world'
    assert 'if(w)name=w.palette;' in PAGE, 'applyTheme does not honour the world'
    assert "cb.classList.toggle('locked',!!ST.world)" in PAGE
    assert '#classicBlock.locked{opacity:' in _CSS


def test_world_palettes_and_skins_stay_out_of_the_classic_pickers():
    from claude_sessions.themes import CLASSIC_SKINS, PALETTES
    for w in WORLDS.values():
        assert w['skin'] not in CLASSIC_SKINS, w['skin']
        assert PALETTES[w['palette']].get('hidden'), w['palette']
    # and the classic three are all still offered
    # Standard is offered too — the plain one, added because "auto" resolves to
    # whichever of the other three the palette names and never to nothing
    assert set(CLASSIC_SKINS) == {'standard', 'hud', 'crt', 'brutal'}


def test_no_look_puts_an_effect_on_the_type_itself():
    """Effects belong on the chrome. Text renders flat.

        "ci può stare sugli elementi grafici come il coso nero su cui stanno i
         testi, ma i testi stessi, il logo, TUI e GUI no … un dislessico
         potrebbe fare fatica a leggerlo … non dovrebbe esistere il caso in cui
         l'utente possa usarlo così"

    That last clause is why this is a test and not a default: a chromatic split
    or a glow on glyphs is not a preference to dial back, it is a state the app
    should not be able to reach. Terminal's phosphor is the single documented
    exception — it is one soft halo in the skin both users chose, not a
    per-channel split, and it was never the complaint."""
    import re
    bad = []
    for m in re.finditer(r'html\.skin-([\w-]+)([^{]*)\{([^}]*)\}', _CSS):
        skin, sel, body = m.group(1), m.group(2), m.group(3)
        if skin == 'crt':
            continue
        if 'text-shadow' in body:
            bad.append(f'{skin}:{sel.strip() or "(root)"}')
    assert not bad, f'effects applied to text: {bad}'
    # and the hover glitch must move an edge, not the element the text is in
    assert 'html.skin-cyber .spot.hv::after' in _CSS
    assert 'html.skin-cyber .spot.hv{animation' not in _CSS


def test_no_look_may_change_how_big_the_ui_is():
    """A look changes what the app LOOKS like, never how big it is.

        "non mi fa impazzire che cambiano le forme delle cose con i vari temi …
         tecnicamente le dimensioni della UI dovrebbero essere fisse perché
         anche quelle hanno una influenza sul design … magari di certi temi non
         è lo sfondo che non ti piace, ma inconsciamente la UI è strutturata un
         po' peggio e ti dà fastidio a livello di subconscio"

    Exactly right, and the reason this is enforced rather than merely fixed: the
    spacing and type scales were tuned once for legibility, so a per-theme
    multiplier does not make a theme *different*, it makes some themes *worse* —
    felt without being nameable. One canonical geometry, seven appearances."""
    for dead in ('--sk-scale', '--sk-dens'):
        assert dead not in _CSS, f'{dead} is back — a look can resize the UI'
        assert dead not in PAGE, f'{dead} is back in the JS'
    for name, sk in SKINS.items():
        for dead in ('scale', 'density'):
            assert dead not in sk, f'{name} declares {dead}'
    # the sizes themselves must still be declared in exactly one place
    assert '.card h3{font-size:14px}' in _CSS
    assert '.kpi .kv2{font-size:20px}' in _CSS


# ── form controls ────────────────────────────────────────────

def test_a_bare_input_cannot_render_as_a_white_browser_default():
    """Only `.fld input` was styled, so any control written without that
    wrapper fell through to the browser default and rendered as a WHITE box on
    a dark page. That shipped: the first cut of the Claude Code settings editor
    was three columns of white rectangles.

    The fix is not a convention to remember — it is that the element itself is
    styled inside `#content`, which is where every page renders.
    """
    assert '#content input:not([type=range])' in _CSS, \
        'a bare <input> in a page is unstyled again'
    assert '#content select' in _CSS and '#content textarea' in _CSS
    # the block those selectors join has to be the one that paints the surface
    block = _CSS[_CSS.index('#content input:not([type=range])'):]
    block = block[:block.index('}')]
    assert 'background:var(--bg)' in block and 'color:var(--txt)' in block


def test_the_native_controls_keep_their_own_painting():
    """A range slider or a checkbox restyled as a text field is worse than the
    default, not better. The checkbox stays excluded here because it has its
    own painting below, not because it is native."""
    sel = _CSS[_CSS.index('#content input:not([type=range])'):]
    sel = sel[:sel.index('{')]
    for kind in ('range', 'checkbox', 'radio'):
        assert ':not([type=%s])' % kind in sel, kind


def test_the_checkbox_is_drawn_by_us_and_its_tick_moves_on_transform_only():
    """`accent-color` takes ONE colour and paints a native tick, so the
    checkbox was the one control that ignored the palette and the skin. It is
    drawn here instead — and the draw has to obey the same compositor contract
    as everything else: transform/opacity only, geometry in percent so a skin
    with a 3px border does not push the tick outside a 10px inner box."""
    block = _CSS[_CSS.index('input[type=checkbox]{'):_CSS.index('.fld textarea{')]
    assert 'input[type=checkbox]{appearance:none' in _CSS, 'the native control is back'
    # `accent-color` stays for the range slider only — never on a checkbox
    for rule in _CSS.split('}'):
        if 'accent-color' in rule:
            assert 'type=range' in rule, rule
    assert 'var(--sk-in-r' in block, 'the box does not take the skin radius'
    # the two arms draw with scaleX from their own end — nothing else animates
    assert block.count('scaleX(1)') == 2 and 'transform-origin:left center' in block
    for prop in ('transition:transform', 'transition-delay'):
        assert prop in block, prop
    for paint in ('width .', 'height .', 'clip-path', 'stroke-dashoffset'):
        assert paint not in block, paint
    # px geometry breaks under --sk-in-bw:3px (anime); the arms are in percent
    arms = [r for r in block.split('}') if 'rotate(' in r and 'left:' in r]
    assert len(arms) == 2, arms
    for arm in arms:
        for edge in ('left:', 'top:', 'width:'):
            val = arm.split(edge, 1)[1].split(';')[0]
            assert val.endswith('%'), (edge, val)


def test_the_tab_row_shares_the_content_column_left_edge():
    """The strip's padding and the tab's own padding used to stack, so the
    first tab label sat 16px right of the page title and of every card below
    it — the one thing on the column that did not line up."""
    # `.tabs{` appears twice — the narrow-window override comes first in the
    # file, so match the declaration that actually sets the inset
    tabs = _CSS[_CSS.index('.tabs{--tab-px'):]
    tabs = tabs[:tabs.index('}')]
    assert 'calc(26px - var(--tab-px))' in tabs
    assert '.tab{padding:9px var(--tab-px)' in _CSS, \
        'the tab no longer takes its padding from the shared inset'


def test_an_in_card_empty_state_is_not_pushed_down_a_screen():
    """`.empty` carries margin-top:7vh for a whole page with nothing in it.
    Inside a card that leaves a one-line message floating 70px down."""
    assert '.card .empty{margin-top:0' in _CSS


# ── values travelling into inline handlers ───────────────────

