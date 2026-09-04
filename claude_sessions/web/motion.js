'use strict';
/* ── motion: the micro-interaction layer ───────────────────────────────────
   Motion here is *transitional*, never decorative. Every primitive fires
   because something happened — a value changed, a node mounted, a job started
   — and then settles. Nothing loops for atmosphere. That is the whole
   difference from the ambient-wallpaper layer this replaced: 26 generative
   renderers looping at 6-30fps across ~30 canvases, encoding numbers nobody
   could read off a field of falling rain.

   Vanilla equivalents of the reference vocabulary (motion.dev, animate-ui,
   reactbits, aceternity):

     MO.count   number ticker — a changed number is *seen* to change
     MO.arrive  the skin's whole-page arrival sequence (anime.js timeline)
     MO.flip    layout animation (FLIP) — reordering reads as movement
     MO.patch   keyed list reconcile: enter / exit / move, no blink
     MO.beam    travelling border on things that are actually running
     MO.spot    pointer spotlight on cards
     MO.press   in-place button feedback (never a page-level overlay)

   MO also owns the app's ONE rAF chain (MO.frame), which instruments.js and
   the number tweens both register into. It parks itself the moment the job set
   empties, so a settled dashboard renders zero frames.

   Cost discipline (CLAUDE.md: QtWebEngine composites through a GPU hardware
   surface, and a blurred/readback layer tears the surface swap):
     · transform and opacity ONLY. Both are compositor-only, so an animation
       here never invalidates paint. No backdrop-filter, no blur, no filter.
     · CSS/WAAPI wherever the effect has a fixed duration — the compositor owns
       that timeline and JS is uninvolved per frame. The rAF chain is only for
       things whose target keeps moving (a gauge chasing a live number).
     · one delegated pointer listener for the spotlight, rAF-coalesced.
     · every effect checks MO.level first, so `off` is genuinely zero cost. */

const MO_REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
/* @property is what lets a custom prop be interpolated (the travelling border
   angle). Chrome 85+; QtWebEngine's Chromium is older on some Qt builds, so
   this is feature-detected rather than assumed — color-mix() got skipped for
   exactly this reason (see app.css). Without it the beam is a static glow. */
const MO_HAS_PROP = !!(window.CSS && CSS.registerProperty);

const MO = {
  /* full | subtle | off — one knob, replacing the old four. `subtle` keeps the
     motion that carries information (value tweens, meter fills, page fade) and
     drops the motion that carries only delight (beams, spotlight, stagger). */
  level: 'full',
  /* QtWebEngine reports the page 'visible' while minimized, so app.js drives
     this off blur/focus too. Held here rather than read from app.js: all three
     files share one script scope, and reaching forward into a `let` declared in
     a later file would hit the temporal dead zone. */
  vis: true,

  set(level) {
    this.level = MO_REDUCED ? 'off' : (level || 'full');
    const r = document.documentElement;
    r.classList.toggle('mo-off', this.level === 'off');
    r.classList.toggle('mo-subtle', this.level === 'subtle');
    r.classList.toggle('mo-beam', this.full && MO_HAS_PROP);
    if (!this.full) this._unspot();
    const A = this.ani;
    if (A) { try { A.engine.timeScale = this._scale(); } catch (e) {} }
    // motion:off means off — the stage is a 3D scene, which is emphatically
    // animation, so it goes with everything else rather than surviving it
    if (window.STAGE) { if (this.on) STAGE.kick(); else STAGE.stop(); }
    this.kick();
  },
  get on() { return this.level !== 'off'; },
  get full() { return this.level === 'full'; },

  /* ── anime.js ────────────────────────────────────────────────────────────
     The vendored engine (index.html loads it as a deferred module and publishes
     window.ANI) owns orchestration: timelines, grid-aware stagger, springs.
     MO keeps its own primitives — count/patch/flip/press are cheaper as they
     are and dozens of tests pin them — so anime is used for the thing MO never
     had, which is sequencing a whole page rather than each card alone.

     Critically: engine.useDefaultMainLoop is turned OFF. anime would otherwise
     start a second requestAnimationFrame chain, and this codebase has exactly
     one. MO._loop calls engine.update() instead, so the vendored engine lives
     inside the same frame budget as the gauges and parks with them.

     Everything below degrades to the existing WAAPI path when ANI is absent
     (no vendor bundle, offline, old Chromium) — never a hard dependency. */
  _aniOn: false,
  get ani() {
    const A = window.ANI;
    if (!A) return null;
    // the flag lives on MO, not on A: window.ANI is an ES module namespace
    // object, which is sealed — assigning a marker to it throws
    if (this._aniOn) return A;
    this._aniOn = true;
    try {
      A.engine.useDefaultMainLoop = false;   // MO owns the only rAF chain
      A.engine.pauseOnDocumentHidden = false; // MO.vis already handles Qt minimise
      A.engine.timeScale = this._scale();
    } catch (e) { /* older build: it keeps its own loop, still correct */ }
    return A;
  },
  _scale() { return this.level === 'subtle' ? 0.6 : 1; },
  _aniJob: null, _aniGrace: 0,
  /* Tick the vendored engine from our chain, and retire the moment it has
     nothing left to animate. `_head` is anime's own live-animation list; it is
     a `this.` property so minification leaves the name intact. The grace frames
     cover the gap between animate() returning and the engine linking it. */
  _aniTick() {
    const A = window.ANI;
    if (!A || !A.engine) { this._aniJob = null; return false; }
    try { A.engine.update(); } catch (e) { this._aniJob = null; return false; }
    if (A.engine._head) { this._aniGrace = 3; return true; }
    if (--this._aniGrace > 0) return true;
    this._aniJob = null; return false;
  },
  _aniHold() {
    this._aniGrace = 3;
    if (!this._aniJob) this._aniJob = this.frame(() => this._aniTick());
  },

  /* ── the single frame loop ──────────────────────────────────────────────
     A job is a function called with (dt seconds); it returns truthy to stay
     registered and falsy to retire. When the set empties the chain stops — no
     idle rAF, which is the point. Anything with a fixed duration belongs in
     CSS/WAAPI instead; this is for targets that keep moving.

     ONE job is allowed to stay registered indefinitely: the background stage.
     That is a deliberate, named exception, not a weakening of the rule — the
     chain still refuses to run while hidden or blurred (below), so "no frames
     when nothing is visible" holds exactly as before. Everything else still
     tweens to a value and retires. */
  _jobs: new Set(),
  _raf: null,
  _prev: 0,
  frame(fn) { this._jobs.add(fn); this.kick(); return fn; },
  unframe(fn) { this._jobs.delete(fn); },
  kick() {
    if (this._raf || !this._jobs.size) return;
    if (document.hidden || !this.vis) return;
    this._prev = performance.now();
    this._raf = requestAnimationFrame(t => this._loop(t));
  },
  stop() {
    if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
  },
  _loop(now) {
    this._raf = null;
    if (document.hidden || !this.vis) return;        // resumes via kick()
    const dt = Math.min(0.1, (now - this._prev) / 1000);
    this._prev = now;
    for (const fn of [...this._jobs]) {
      let keep = false;
      try { keep = fn(dt); } catch (e) { keep = false; }
      if (!keep) this._jobs.delete(fn);
    }
    if (!this._jobs.size) return;                    // parked
    this._raf = requestAnimationFrame(t => this._loop(t));
  },

  /* ── numbers ────────────────────────────────────────────────────────────
     A dashboard number that snaps from 12.1k to 12.4k reads as a repaint. The
     same number counting up over half a second reads as *the workspace doing
     work*. fmt keeps the caller's formatter (fmtTok, percentages, currency) so
     the tween never renders a shape the settled value wouldn't have.
     Cached on the element, so a poll re-reporting the same value costs nothing
     and — importantly — never restarts the tween. */
  count(el, to, fmt) {
    if (!el) return;
    to = Number(to);
    if (!isFinite(to)) to = 0;
    const f = fmt || MO_FMT.int;
    const from = el.__mv;
    if (from === to) return;
    el.__mv = to;
    if (!this.on || from == null) { el.textContent = f(to); return; }
    // direction tint: which way it moved matters more than the delta's size
    el.classList.remove('mo-up', 'mo-dn');
    el.classList.add(to > from ? 'mo-up' : 'mo-dn');
    clearTimeout(el.__mvt);
    el.__mvt = setTimeout(() => el.classList.remove('mo-up', 'mo-dn'), 700);
    // retarget an in-flight tween instead of racing a second one against it
    if (el.__mvj) { el.__mva = el.__mvc; el.__mvb = to; el.__mvp = 0; return; }
    el.__mva = from; el.__mvb = to; el.__mvp = 0; el.__mvc = from;
    el.__mvj = this.frame(dt => {
      el.__mvp = Math.min(1, el.__mvp + dt / 0.48);
      const e = 1 - Math.pow(1 - el.__mvp, 3);           // ease-out cubic
      el.__mvc = el.__mva + (el.__mvb - el.__mva) * e;
      el.textContent = f(el.__mvc);
      if (el.__mvp < 1 && el.isConnected) return true;
      el.textContent = f(el.__mvb); el.__mvc = el.__mvb; el.__mvj = null;
      return false;
    });
  },
  /* declarative form: <span data-num="1234" data-fmt="tok"> */
  counts(root) {
    (root || document).querySelectorAll('[data-num]').forEach(el => {
      this.count(el, parseFloat(el.dataset.num), MO_FMT[el.dataset.fmt] || MO_FMT.int);
    });
  },

  /* Entrance choreography is the skin's, not the app's: a HUD panel wipes in, a
     Sakura card blooms, a Mecha panel hard-cuts. SKIN_ENTER holds the keyframes;
     app.js sets MO.skin when the theme applies. Everything is transform/opacity
     (clip-path is compositor-friendly too and never triggers a readback). */
  skin: 'hud',
  arrive_: 'boot',
  enter(el, delay) {
    if (!this.on || !el || !el.animate) return;
    const k = SKIN_ENTER[this.skin] || SKIN_ENTER.hud;
    el.animate(k.frames, { duration: k.ms, delay: delay || 0,
      easing: k.ease, fill: 'backwards' });
  },

  /* ── page arrival ────────────────────────────────────────────────────────
     The difference between "each card animates" and "the page arrives" is
     whether the delays are a sequence or a coincidence. The old code was
     `Math.min(i*40,320)` — a linear ramp in DOM order, which on a bento grid
     reads as a diagonal wipe nobody designed.

     SKIN_ARRIVE gives each skin a real sequence: HUD boots panel by panel,
     Sakura blooms outward from the centre of the grid, Neon City glitches
     column by column, Mecha and Brutalist slam the whole thing in one step.
     anime's grid-aware stagger is what makes "outward from the centre" a
     parameter rather than an afternoon of index arithmetic.

     Falls back to the per-card WAAPI reveal when the vendor bundle is absent. */
  arrive(root, sel) {
    if (!this.full) return;
    const nodes = [...(root || document).querySelectorAll(sel || '.mo-in')]
      .filter(el => !el.__mor);
    if (!nodes.length) return;
    nodes.forEach(el => { el.__mor = 1; });
    const A = this.ani;
    const plan = SKIN_ARRIVE[this.arrive_] || SKIN_ARRIVE.boot;
    if (!A) {                                  // no vendor bundle: old path
      nodes.forEach((el, i) => this.enter(el, Math.min(i * 40, 320)));
      return;
    }
    try {
      A.animate(nodes, Object.assign({}, plan.p, {
        delay: A.stagger(plan.step, {grid: this._grid(nodes), from: plan.from}),
      }));
      this._aniHold();
    } catch (e) {
      nodes.forEach((el, i) => this.enter(el, Math.min(i * 40, 320)));
    }
  },
  /* Infer [cols, rows] from where the nodes actually landed. Reading the CSS
     grid would need getComputedStyle per node and would still be wrong for a
     bento with spans; counting how many share the first row is right for both,
     and costs one layout read total. */
  _grid(nodes) {
    if (nodes.length < 2) return [1, 1];
    const top = nodes[0].offsetTop;
    let cols = 0;
    for (const el of nodes) { if (el.offsetTop === top) cols++; else break; }
    cols = Math.max(1, cols);
    return [cols, Math.ceil(nodes.length / cols)];
  },

  /* A timeline for sequences that are not a list — a modal opening its header,
     then its body, then its buttons. Returns anime's timeline, or a no-op shim
     so a call site never has to null-check. */
  tl(opts) {
    const A = this.ani;
    if (!A || !this.on) return MO_TL_NOOP;
    const t = A.createTimeline(Object.assign({autoplay: true}, opts || {}));
    this._aniHold();
    return t;
  },
  /* anime's stagger, surfaced so call sites do not each reach into window.ANI */
  stagger(v, o) {
    const A = this.ani;
    return A ? A.stagger(v, o) : 0;
  },
  /* A real spring, not a cubic-bezier impression of one. Used for things that
     respond to a pointer, where the overshoot is the feedback. */
  spring(o) {
    const A = this.ani;
    if (!A || !A.createSpring) return 'cubicBezier(.34,1.56,.64,1)';
    return A.createSpring(Object.assign({stiffness: 120, damping: 12}, o || {}));
  },

  /* ── one-shot signature effects ────────────────────────────────────────
     A skin's flourish fires on an EVENT — a card mounting, a modal opening, a
     session launching — runs once, and removes itself. None of them loop.
     Petals falling forever is the ambient wallpaper this project deleted;
     petals bursting once when a session launches is feedback.

     Mounted as one absolutely-positioned, pointer-transparent overlay so the
     effect can never affect layout, and dropped on `finish`. Full motion only:
     at `subtle` these carry no information, and at `off` nothing runs. */
  burst(el, kind) {
    if (!this.full || !el) return;
    kind = kind || (SKIN_BURST[this.skin] || 'brackets');
    const mk = MO_BURST[kind];
    if (!mk || !el.animate) return;
    const host = document.createElement('div');
    host.className = 'burst b-' + kind;
    host.setAttribute('aria-hidden', 'true');
    // the effect needs a positioned ancestor to sit inside; give it one without
    // disturbing a layout that may already rely on static positioning
    const pos = getComputedStyle(el).position;
    if (pos === 'static') el.style.position = 'relative';
    el.appendChild(host);
    const anims = mk(host);
    let left = anims.length;
    const done = () => { if (--left <= 0 && host.parentNode) host.remove(); };
    anims.forEach(a => { a.onfinish = done; a.oncancel = done; });
    if (!anims.length) host.remove();
  },
  /* the launch moment: the one place a skin gets to celebrate. Two scales at
     once — the burst is local to the element, the shockwave goes through the
     whole background. */
  launched(el) {
    this.burst(el || document.querySelector('.main'), null);
    if (window.STAGE) STAGE.shock();
  },
  exit(el) {
    if (!el) return;
    if (!this.on || !el.animate) { el.remove(); return; }
    const a = el.animate([{ opacity: 1, transform: 'none' },
                          { opacity: 0, transform: 'translateY(-6px)' }],
      { duration: 160, easing: 'ease-in' });
    a.onfinish = a.oncancel = () => el.remove();
  },

  /* ── FLIP ───────────────────────────────────────────────────────────────
     First / Last / Invert / Play. Measure, let the caller mutate the DOM, then
     animate each survivor from its old box to its new one with a transform.
     Layout is read once before and once after — never inside the animation —
     so this costs two reflows total, not one per row. */
  flip(container, mutate) {
    if (!container || !this.on) { mutate(); return; }
    const before = new Map();
    for (const k of container.children) before.set(k, k.getBoundingClientRect());
    mutate();
    for (const k of container.children) {
      const b = before.get(k);
      if (!b || !k.animate) continue;
      const a = k.getBoundingClientRect();
      const dx = b.left - a.left, dy = b.top - a.top;
      if (!dx && !dy) continue;
      k.animate([{ transform: `translate(${dx}px,${dy}px)` }, { transform: 'none' }],
        { duration: 320, easing: 'cubic-bezier(.22,1,.36,1)' });
    }
  },

  /* ── keyed list reconcile ───────────────────────────────────────────────
     Why lists used to blink: setV() compares the whole innerHTML string and, on
     any difference, throws every row away and rebuilds. One session finishing
     re-created twenty unrelated rows — losing hover state, scroll anchoring,
     and any chance of animating what actually changed.

     patch() diffs by key. Unchanged rows keep their identity AND their node;
     only genuinely new rows enter, only departed rows exit, and rows that
     merely moved get FLIPped. html() is the same per-item renderer the call
     site already had, so adopting this is a small change there.

     cls may be a string or a per-item function — selection/state classes have
     to be re-applied every pass, since a row that merely became selected must
     not be torn down and rebuilt just to change one class. */
  patch(host, items, keyOf, html, cls) {
    if (!host) return;
    const prev = host.__mok || new Map();
    const next = new Map();
    const order = [];
    const clsOf = typeof cls === 'function' ? cls : () => (cls || 'mo-row');
    items.forEach((it, i) => {
      const k = String(keyOf(it, i));
      const markup = html(it, i);
      const kls = clsOf(it, i);
      let el = prev.get(k);
      if (el) {
        if (el.__moh !== markup) { el.innerHTML = markup; el.__moh = markup; }
        if (el.className !== kls) el.className = kls;
        prev.delete(k);
      } else {
        el = document.createElement('div');
        el.className = kls;
        el.innerHTML = markup;
        el.__moh = markup;
        el.__monew = 1;
      }
      el.__mokey = k;
      next.set(k, el);
      order.push(el);
    });
    const gone = [...prev.values()];
    // Children this function never created — a loading skeleton, an "offline"
    // placeholder, whatever the renderer put there first. They are not in the key
    // map, so without this they survive every patch and sit above the real rows
    // forever. (They did, until a screenshot showed four shimmer bars stacked on
    // top of the project list.)
    const foreign = [...host.children].filter(el => !next.has(el.__mokey));
    this.flip(host, () => {
      // appendChild on an existing child is a move, not a copy — that is what
      // keeps identity while reordering
      order.forEach(el => host.appendChild(el));
      gone.concat(foreign).forEach(el => {
        if (el.parentNode === host && !next.has(el.__mokey)) host.removeChild(el);
      });
    });
    let n = 0;
    order.forEach(el => {
      if (!el.__monew) return;
      el.__monew = 0;
      this.enter(el, Math.min(n++ * 30, 240));
    });
    host.__mok = next;
  },

  /* ── running state ──────────────────────────────────────────────────────
     "Is this thing still working?" should never need a second look. A spinner
     answers it for one widget; a travelling border answers it for a whole
     region without stealing the eye, which is what a 4-minute memory build
     needs. Applied ONLY while something runs — a beam on idle chrome is
     exactly the wallpaper this layer exists to stop being. */
  beam(el, on) { if (el) el.classList.toggle('beam', !!on); },
  /* a live status dot: one expanding ring, not an opacity blink. The old
     `pulse` keyframe stepped opacity at steps(8) ≈ 8 paint invalidations/sec;
     a scaled ring is compositor-only and reads calmer. */
  pip(el, on) { if (el) el.classList.toggle('pip', !!on); },

  /* ── pointer spotlight ──────────────────────────────────────────────────
     ONE delegated listener for the whole app, coalesced to a frame. Writing
     --mx/--my on the hovered card lets CSS paint a soft radial highlight; no
     filter, no blur, no readback. Per-card listeners would mean dozens of
     handlers and a layout read per pointermove. */
  /* A world's pointer response rides the SAME delegated listener rather than
     adding its own: 'speed' (anime speed-lines), 'glitch' (cyberpunk chromatic
     split), 'bracket' (deck corner snap), 'link' (graph edges lighting up). All
     of them are a class on the hovered element and CSS does the rest, so this
     costs one class toggle per hover and no extra handler. */
  hover: '',
  _hov: null,
  _mark(el) {
    if (this._hov === el) return;
    if (this._hov) this._hov.classList.remove('hv');
    this._hov = el;
    if (el && this.hover && this.full) el.classList.add('hv');
  },
  spot(root) {
    if (!root || root.__mosp) return;
    root.__mosp = 1;
    let pending = 0, target = null, px = 0, py = 0;
    root.addEventListener('pointermove', ev => {
      if (!this.full) return;
      const card = ev.target.closest && ev.target.closest('.spot');
      this._mark(card || null);
      if (!card) return;
      target = card; px = ev.clientX; py = ev.clientY;
      if (pending) return;
      pending = requestAnimationFrame(() => {
        pending = 0;
        if (!target || !target.isConnected) return;
        const b = target.getBoundingClientRect();
        target.style.setProperty('--mx', (px - b.left).toFixed(0) + 'px');
        target.style.setProperty('--my', (py - b.top).toFixed(0) + 'px');
      });
    }, { passive: true });
    root.addEventListener('pointerleave', () => {
      target = null; this._mark(null);
    }, { passive: true });
  },
  _unspot() {
    document.querySelectorAll('.spot').forEach(c => {
      c.style.removeProperty('--mx'); c.style.removeProperty('--my');
    });
  },

  /* ── page transition ────────────────────────────────────────────────────
     A short fade+rise on #content. Navigation should feel like a move, not a
     substitution. Deliberately shorter than the reveal stagger so the two read
     as one gesture rather than two. */
  page(el) {
    if (!this.on || !el || !el.animate) return;
    el.animate([{ opacity: 0, transform: 'translateY(6px)' },
                { opacity: 1, transform: 'none' }],
      { duration: 140, easing: 'cubic-bezier(.22,1,.36,1)' });
  },

  /* ── in-place button feedback ───────────────────────────────────────────
     A click that fires a fetch has to acknowledge itself *in the button*. The
     old answer was a full-screen modal for every job kind, which turned a
     300ms call into a blocking dialog. This swaps the label for a spinner,
     pins the width so nothing reflows, and restores it on settle — including
     on throw, which a naive .then() chain would leak. */
  press(btn, run) {
    if (!btn) return run();
    if (btn.__mobusy) return;
    btn.__mobusy = 1;
    const w = btn.offsetWidth, html = btn.innerHTML;
    btn.style.minWidth = w + 'px';
    btn.innerHTML = '<span class="spin"></span>';
    btn.classList.add('busy');
    const done = () => {
      btn.__mobusy = 0; btn.innerHTML = html;
      btn.style.minWidth = ''; btn.classList.remove('busy');
    };
    let r;
    try { r = run(); } catch (e) { done(); throw e; }
    if (r && r.then) return r.then(v => { done(); return v; },
                                  e => { done(); throw e; });
    done();
    return r;
  },

  /* ── skeletons ──────────────────────────────────────────────────────────
     A card that renders empty and fills half a second later reads as broken;
     the same card with bones reads as loading. Ragged widths on purpose —
     equal-length bars read as a progress bar, not as text. */
  skel(n, h) {
    const w = [92, 74, 61, 83, 68];
    let s = '';
    for (let i = 0; i < (n || 3); i++)
      s += `<div class="shimmer" style="height:${h || 12}px;width:${w[i % 5]}%;margin:7px 0"></div>`;
    return s;
  },
};

/* ── per-skin card entrances ───────────────────────────────────────────────
   Keyframes only; the skin data in themes.py names which one it wears. All
   transform/opacity/clip-path — nothing that invalidates paint. */
const SKIN_ENTER = {
  // plain: a short rise and fade, the least a card can do and still arrive.
  // Deliberately not a wipe, a pop or a slam — the whole point of Standard is
  // that nothing about it is a signature.
  standard: { ms: 200, ease: 'cubic-bezier(.22,1,.36,1)', frames: [
            { opacity: 0, transform: 'translateY(6px)' },
            { opacity: 1, transform: 'none' }] },
  // instrument panel powering up: a wipe across, no vertical travel
  hud:    { ms: 300, ease: 'cubic-bezier(.2,.9,.3,1)', frames: [
            { opacity: 0, clipPath: 'inset(0 100% 0 0)' },
            { opacity: 1, clipPath: 'inset(0 0 0 0)' }] },
  // same wipe, faster and tighter — a deck boots one strip at a time
  deck:   { ms: 220, ease: 'cubic-bezier(.2,.9,.3,1)', frames: [
            { opacity: 0, clipPath: 'inset(0 100% 0 0)' },
            { opacity: 1, clipPath: 'inset(0 0 0 0)' }] },
  // cel-shaded pop: overshoots once and settles, never eases in softly
  anime:  { ms: 320, ease: 'cubic-bezier(.34,1.6,.5,1)', frames: [
            { opacity: 0, transform: 'scale(.88) rotate(-1.5deg)' },
            { opacity: 1, transform: 'scale(1.03) rotate(.4deg)', offset: .6 },
            { opacity: 1, transform: 'none' }] },
  // glitch slice: two quick horizontal displacements, then settle
  cyber:  { ms: 240, ease: 'cubic-bezier(.16,1,.3,1)', frames: [
            { opacity: 0, transform: 'translateX(-7px)', clipPath: 'inset(0 0 62% 0)' },
            { opacity: 1, transform: 'translateX(5px)',  clipPath: 'inset(38% 0 0 0)', offset: .35 },
            { opacity: 1, transform: 'translateX(-2px)', clipPath: 'inset(0 0 0 0)', offset: .7 },
            { opacity: 1, transform: 'none' }] },
  // a node settling into the layout after the force solve
  graph:  { ms: 400, ease: 'cubic-bezier(.22,1,.36,1)', frames: [
            { opacity: 0, transform: 'translateY(12px) scale(.97)' },
            { opacity: 1, transform: 'none' }] },
  // typed in: revealed left-to-right in discrete steps, like text arriving
  crt:    { ms: 320, ease: 'steps(14,end)', frames: [
            { opacity: 1, clipPath: 'inset(0 100% 0 0)' },
            { opacity: 1, clipPath: 'inset(0 0 0 0)' }] },
  // slam: overshoots down-right then snaps back, linear throughout
  brutal: { ms: 120, ease: 'linear', frames: [
            { opacity: 0, transform: 'translate(-6px,-6px)' },
            { opacity: 1, transform: 'translate(2px,2px)', offset: .6 },
            { opacity: 1, transform: 'none' }] },
};
// `standard` reuses HUD's brackets rather than getting its own: a burst marks
// an action (a session launching), and a look with no signature has no reason
// to author a new 400ms flourish for it.
const SKIN_BURST = { standard: 'brackets', hud: 'brackets', deck: 'brackets',
  anime: 'sparkle', cyber: 'scan', graph: 'link', crt: 'caret', brutal: 'snap' };

/* ── per-skin page-arrival sequences (anime.js) ────────────────────────────
   `step` is the stagger in ms, `from` the grid origin it radiates out of, `p`
   the animation itself. Every property is transform/opacity/clip-path, same
   compositor contract as everywhere else.

   `from` is the whole point of routing this through anime: 'center' on a grid
   is what turns a list of cards into a page that blooms, and 'first'/0 with a
   large step is what turns it into a console booting one panel at a time. */
const SKIN_ARRIVE = {
  // HUD: panels power up in sequence, each wiping in from the left
  boot:   {step: 55, from: 'first', p: {
            opacity: [0, 1], clipPath: ['inset(0 100% 0 0)', 'inset(0 0 0 0)'],
            duration: 300, ease: 'cubicBezier(.2,.9,.3,1)'}},
  // Sakura: outward from the middle of the grid, scaling up softly
  bloom:  {step: 42, from: 'center', p: {
            opacity: [0, 1], scale: [.94, 1], translateY: [10, 0],
            duration: 420, ease: 'cubicBezier(.34,1.4,.5,1)'}},
  // Mecha / Brutalist: the whole page in one hard step. No ramp at all.
  slam:   {step: 8, from: 'first', p: {
            opacity: [0, 1], translateX: [-8, 0], translateY: [-8, 0],
            duration: 130, ease: 'linear'}},
  // Neon City: column by column, each slice displaced before it settles
  glitch: {step: 34, from: 'first', p: {
            opacity: [0, 1], translateX: [-9, 5, -2, 0],
            duration: 260, ease: 'cubicBezier(.16,1,.3,1)'}},
  // Terminal: revealed left to right in discrete steps, like text arriving
  type:   {step: 46, from: 'first', p: {
            clipPath: ['inset(0 100% 0 0)', 'inset(0 0 0 0)'],
            duration: 320, ease: 'steps(14)'}},
  // Glass: everything rises together, the last thing to settle is the furthest
  lift:   {step: 36, from: 'last', p: {
            opacity: [0, 1], translateY: [14, 0], scale: [.99, 1],
            duration: 440, ease: 'cubicBezier(.22,1,.36,1)'}},
};
/* returned by MO.tl() when anime is unavailable, so a call site can always
   chain without null-checking */
const MO_TL_NOOP = {add() { return this; }, then(f) { if (f) f(); return this; },
  play() { return this; }, pause() { return this; }, complete() { return this; }};

/* Each builder fills the overlay and returns its animations; MO.burst removes
   the overlay once they all finish. Every one is single-iteration by
   construction — no `iterations` option is ever passed, and
   tests/test_skins.py checks that stays true. */
const MO_BURST = {
  // Anime: a one-shot sparkle burst — hard-edged four-point stars, flat fill,
  // no gradient. Cel shading does not do soft glows.
  sparkle(host) {
    const out = [];
    for (let i = 0; i < 10; i++) {
      const p = document.createElement('i');
      p.className = 'sp';
      p.style.left = (8 + (i * 71) % 84) + '%';
      p.style.top = (10 + (i * 43) % 76) + '%';
      p.style.setProperty('--d', (0.7 + ((i * 29) % 60) / 100).toFixed(2));
      host.appendChild(p);
      out.push(p.animate([
        { opacity: 0, transform: 'scale(0) rotate(0deg)' },
        { opacity: 1, transform: 'scale(1) rotate(45deg)', offset: .45 },
        { opacity: 0, transform: 'scale(0) rotate(90deg)' }],
        { duration: 620, delay: i * 34, easing: 'cubic-bezier(.3,.1,.4,1)' }));
    }
    return out;
  },
  // Graph: an edge draws itself across the node and retracts
  link(host) {
    const out = [];
    for (let i = 0; i < 3; i++) {
      const e = document.createElement('i');
      e.className = 'lk lk' + i;
      host.appendChild(e);
      out.push(e.animate([
        { transform: 'scaleX(0)', opacity: 0 },
        { transform: 'scaleX(1)', opacity: 1, offset: .5 },
        { transform: 'scaleX(1)', opacity: 0 }],
        { duration: 700, delay: i * 90, easing: 'cubic-bezier(.22,1,.36,1)' }));
    }
    return out;
  },
  // HUD: four corner brackets draw themselves in and fade
  brackets(host) {
    const out = [];
    for (let i = 0; i < 4; i++) {
      const b = document.createElement('i');
      b.className = 'bk bk' + i;
      host.appendChild(b);
      out.push(b.animate([{ opacity: 0, transform: 'scale(.4)' },
                          { opacity: 1, transform: 'scale(1)', offset: .4 },
                          { opacity: 0, transform: 'scale(1)' }],
        { duration: 900, delay: i * 60, easing: 'cubic-bezier(.2,.9,.3,1)' }));
    }
    return out;
  },
  // Cyberpunk: one scanline passes top to bottom
  scan(host) {
    const b = document.createElement('i');
    b.className = 'sc';
    host.appendChild(b);
    return [b.animate([{ transform: 'translateY(-8px)', opacity: 0 },
                       { opacity: 1, offset: .2 },
                       { transform: 'translateY(var(--h,240px))', opacity: 0 }],
      { duration: 750, easing: 'cubic-bezier(.16,1,.3,1)' })];
  },
  // Terminal: a caret runs the width of the element and vanishes
  caret(host) {
    const b = document.createElement('i');
    b.className = 'cr';
    host.appendChild(b);
    return [b.animate([{ transform: 'translateX(0)', opacity: 1 },
                       { transform: 'translateX(var(--w,200px))', opacity: 1, offset: .85 },
                       { opacity: 0 }],
      { duration: 620, easing: 'steps(18,end)' })];
  },
  // Brutalist: the hard shadow snaps out and back
  snap(host) {
    const b = document.createElement('i');
    b.className = 'sn';
    host.appendChild(b);
    return [b.animate([{ transform: 'translate(0,0)', opacity: .9 },
                       { transform: 'translate(10px,10px)', opacity: 0 }],
      { duration: 260, easing: 'linear' })];
  },
};

/* formatters for the declarative data-num path. `tok` defers to the app's own
   fmtTok so a tweening token count never formats differently from a settled
   one (fmtTok lives in app.js, later in the same script scope — reached via
   window to stay clear of the temporal dead zone). */
const MO_FMT = {
  int: v => String(Math.round(v)),
  tok: v => (window.fmtTok ? window.fmtTok(Math.round(v)) : String(Math.round(v))),
  pct: v => Math.round(v) + '%',
  usd: v => '$' + v.toFixed(2),
  ratio: v => String(Math.round(v)),
};

if (MO_HAS_PROP) {
  // registered so the angle interpolates instead of snapping between keyframes
  try {
    CSS.registerProperty({ name: '--beam-a', syntax: '<angle>',
                           inherits: false, initialValue: '0deg' });
  } catch (e) { /* already registered — a soft reload re-runs this file */ }
}

window.MO = MO;
