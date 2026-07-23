# GUI Rework Notes

## Recon (Phase A)

### Exported name from gui_html.py
- `PAGE` is now a module-level string built by `_build_html()` at import time
- gui.py imports: `from .gui_html import PAGE` (line 294)
- gui.py serves: `self._send(200, PAGE.encode("utf-8"), ctype="text/html")`

### Web asset layout
```
claude_sessions/
  web/
    app.css      — all CSS extracted from the old inline <style> block
    app.js       — all JS extracted from the old inline <script> block
    index.html   — HTML body with <!--CSS-->, <!--JS--> marker comments
  gui_html.py    — 22-line builder that reads web/ files and inlines them
```

### Plan assumptions vs reality
- Plan assumed a "times modal" with `TIMES_FN`, `POLL_FN`, `times-modal`, etc.
- Reality: NO separate times modal. Plan execution uses the job modal (`#jovl`) for progress.
- Job modal shows/hides via `.ovl` class `.show` toggle (CSS: `display:none`/`display:flex`)

### Flicker root cause
The primary flicker source when clicking "Write the plan" was the `poll()` function's
`innerHTML` replacement of `#jMsgs` and `#jSub.textContent` changing every 600ms.
The messages list was recreated from scratch each tick, causing DOM churn.

**Fix:** Added `__plMsgs`, `__plSub`, `__plLabel`, `__plGateTitle` cache variables
that compare against previous values before writing DOM.

### Changes made
1. **Flicker fix** — poll() cache variables skip DOM writes when data unchanged
2. **Web asset extraction** — 22-line builder replaces 2312-line raw string
3. **UX improvements** — loading bar, Escape closes all modals, a11y dialog role + focus management, MutationObserver overlay click handling
4. **Theme swatches** — visual colored dots in Settings theme picker (18 themes)
5. **Tests** — 5 new flicker regression tests, 533 total passing
