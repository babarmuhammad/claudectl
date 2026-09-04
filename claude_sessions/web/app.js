
'use strict';
const $=s=>document.querySelector(s);
/* Per-run secret, substituted into this page by gui._Handler when it is served.
   `/` is token-gated, so the launcher puts the token in the query string — a
   top-level navigation cannot carry a header, which is the same reason /graph
   has always taken it that way.

   It STAYS in the address bar, and that is deliberate. Stripping it with
   history.replaceState looked strictly better and broke reload: F5 re-requests
   whatever the address bar holds, which was then a bare `/`, and the answer to
   that is 403 {"error":"missing or bad token"} — a dead page with no way back
   except relaunching claudectl. A cookie is not the escape hatch either;
   cookies ignore the port, so any other loopback server the user visits would
   be sent it. The exposure left is a URL in this machine's own browser history,
   holding a secret that dies with the process. */
const CK='__CLAUDECTL_TOKEN__';
const api=(p,opt={})=>fetch(p,{...opt,headers:{'X-Claudectl':CK,
  'Content-Type':'application/json',...(opt.headers||{})}}).then(r=>r.json());
const post=(p,body)=>api(p,{method:'POST',body:JSON.stringify(body||{})});

let ST=null, CUR=null, TAB='sessions', PAGE_='home', PENDING=null, SESS=[];
let NAV_ID=0;               // bumped on every navigation; stale draws check it
let ACTIVE_MEM=new Set();   // project paths whose memory is refreshing right now
let _VIS=true;              // QtWebEngine stays 'visible' when minimized — blur toggles this

/* ── writing #content ──────────────────────────────────────────────────────
   THE ONLY two places allowed to assign #content.innerHTML. Everything else
   goes through them, and tests/test_gui_flicker.py enforces it.

   Why: almost every renderer is async — it paints a spinner, awaits a fetch,
   then writes the real markup. Nothing stopped that second write from landing
   after you had already navigated somewhere else, so a slow page would
   overwrite the page you were actually looking at:

     "la pagina MCP ci mette un po' a caricare… se ti sposti su altre pagine,
      quando si carica sovrascrive la pagina su cui stai davvero.
      Ero in settings ma con la schermata di MCP."

   NAV_ID has existed since the beginning but exactly one renderer (drawMemory)
   remembered to check it, which is the failure mode of any convention that
   lives in fifteen places. Now the check is in the function itself: you cannot
   write #content without going through the guard.

     const nav = paintNow(LOADING);     // pre-await, always safe, returns the token
     …await…
     if(!paint(nav, html)) return;      // post-await, drops if we navigated away */
function paintNow(html){$('#content').innerHTML=html;return NAV_ID;}
function paint(nav,html){
  if(nav!==NAV_ID)return false;         // navigated away mid-fetch — drop it
  $('#content').innerHTML=html;
  return true;
}
const LOADING='<div class="empty"><span class="spin"></span> Loading…</div>';

function toast(msg,cls){const w=$('#toast-wrap');const t=document.createElement('div');
  t.className='toast '+(cls||'');t.textContent=msg;w.appendChild(t);
  requestAnimationFrame(()=>{t.classList.add('show');});
  setTimeout(()=>{t.classList.remove('show');setTimeout(()=>t.remove(),300);},3500);}
/* ── loading indicator (counter-based, multiple concurrent fetches) ── */
let __loadingCount=0;
function setLoading(on){
  __loadingCount+=on?1:-1;
  if(__loadingCount<0)__loadingCount=0;
  const el=document.getElementById('loading');
  if(el)el.classList.toggle('on',__loadingCount>0);
}
/* also escapes quotes, which the old textContent round-trip did NOT — every
   attribute-value call site (data-v="${esc(v)}", title="${esc(v)}") was open */
const _ESC_MAP={'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
function esc(s){return s==null?'':String(s).replace(/[&<>"']/g,c=>_ESC_MAP[c]);}
/* A value going into a JS literal inside an HTML attribute. JSON.stringify
   makes it a valid JS literal; esc() then makes that literal survive the HTML
   parser. JSON.stringify alone escapes NOTHING for HTML -- one apostrophe in a
   filename, an MCP server name or a git branch closes onclick='...' and
   everything after it is parsed as new attributes, which is script execution
   same-origin with the API token. Never interpolate a bare JSON.stringify into
   markup; tests/test_xss_gate.py fails the build if you do. */
function hesc(v){return esc(JSON.stringify(v));}
function C(){return {path:CUR.path,enc:CUR.encoded,cfgdir:CUR.primary_cfgdir};}
/* stable per-account color: default = the theme's green, others take a FIXED
   (hue, lightness) ramp — not a generated hue wheel. Each slot's lightness is
   tuned so every colour lands in the OKLCH mid band and stays distinguishable
   under deuteranopia on BOTH dark and light theme surfaces (checked with the
   dataviz palette validator). A 7th+ account folds to neutral, never a new hue. */
const ACCT_RAMP=[[265,65],[25,45],[190,38],[330,54],[70,32],[215,58]];
function acctColor(name){
  if(!name||name==='default')return 'var(--ok)';
  const i=(ST.accounts||[]).findIndex(a=>a.name===name);
  if(i<0||i>=ACCT_RAMP.length)return 'var(--dim)';
  return `hsl(${ACCT_RAMP[i][0]} 80% ${ACCT_RAMP[i][1]}%)`;
}
function qs(o){return Object.entries(o).map(([k,v])=>k+'='+encodeURIComponent(v)).join('&');}

/* ── inline SVG icons (Google Material Icons path data, 24×24) ── */
const ICONS={
play:'M8 5v14l11-7z',
add:'M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z',
close:'M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z',
check:'M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z',
refresh:'M17.65 6.35A7.95 7.95 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4z',
del:'M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6zm13-15h-3.5l-1-1h-5l-1 1H5v2h14z',
edit:'M3 17.25V21h3.75L17.81 9.94l-3.75-3.75zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75z',
download:'M19 9h-4V3H9v6H5l7 7zM5 18v2h14v-2z',
doc:'M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8zm2 16H8v-2h8zm0-4H8v-2h8zm-3-5V3.5L18.5 9z',
folder:'M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8z',
newfolder:'M20 6h-8l-2-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-1 8h-3v3h-2v-3h-3v-2h3V9h2v3h3z',
label:'M17.63 5.84A2 2 0 0 0 16 5H5c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h11a2 2 0 0 0 1.63-.84L22 12z',
archive:'M20.54 5.23 19.15 3.55A2 2 0 0 0 17.6 3H6.4c-.5 0-.96.2-1.3.55L3.46 5.23A2 2 0 0 0 3 6.5V19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6.5c0-.48-.17-.93-.46-1.27zM12 17.5 6.5 12H10v-2h4v2h3.5zM5.12 5l.81-1h12l.94 1z',
history:'M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6a7 7 0 1 1 7 7v2a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8z',
search:'M15.5 14h-.79l-.28-.27a6.5 6.5 0 1 0-.7.7l.27.28v.79l5 4.99L20.49 19zm-6 0A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z',
settings:'M19.14 12.94c.04-.3.06-.61.06-.94s-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94L14.4 2.81a.48.48 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.5.5 0 0 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.49.49 0 0 0-.12-.61zM12 15.6A3.6 3.6 0 1 1 15.6 12 3.6 3.6 0 0 1 12 15.6z',
group:'M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5s-3 1.34-3 3 1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z',
terminal:'M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4V8h16zm-2-1h-6v-2h6zM7.5 17l-1.41-1.41L8.67 13 6.09 10.41 7.5 9l4 4z',
robot:'M20 9V7c0-1.1-.9-2-2-2h-3c0-1.66-1.34-3-3-3S9 3.34 9 5H6c-1.1 0-2 .9-2 2v2c-1.66 0-3 1.34-3 3s1.34 3 3 3v4c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2v-4c1.66 0 3-1.34 3-3s-1.34-3-3-3zM7.5 11.5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5S9.83 13 9 13s-1.5-.67-1.5-1.5zM16 17H8v-2h8zm-1-4c-.83 0-1.5-.67-1.5-1.5S14.17 10 15 10s1.5.67 1.5 1.5S15.83 13 15 13z',
link:'M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7a5 5 0 0 0 0 10h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4a5 5 0 0 0 0-10z',
plug:'M16 7V3h-2v4h-4V3H8v4c-1.1 0-2 .9-2 2v5.5L9.5 18v3h5v-3l3.5-3.5V9c0-1.1-.9-2-2-2z',
chart:'M5 9.2h3V19H5zM10.6 5h2.8v14h-2.8zm5.6 8H19v6h-2.8z',
help:'M11 18h2v-2h-2zm1-16A10 10 0 1 0 22 12 10 10 0 0 0 12 2zm0 18a8 8 0 1 1 8-8 8 8 0 0 1-8 8zm0-14a4 4 0 0 0-4 4h2a2 2 0 1 1 4 0c0 2-3 1.75-3 5h2c0-2.25 3-2.5 3-5a4 4 0 0 0-4-4z',
fork:'M14 4l2.29 2.29-2.88 2.88 1.42 1.42 2.88-2.88L20 10V4zm-4 0H4v6l2.29-2.29 4.71 4.7V20h2v-8.41l-5.29-5.3z',
eye:'M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17a5 5 0 1 1 5-5 5 5 0 0 1-5 5zm0-8a3 3 0 1 0 3 3 3 3 0 0 0-3-3z',
chat:'M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z',
school:'M5 13.18v4L12 21l7-3.82v-4L12 17zM12 3 1 9l11 6 9-4.91V17h2V9z',
pin:'M16 9V4h1c.55 0 1-.45 1-1s-.45-1-1-1H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3z',
bolt:'M11 21h-1l1-7H7.5c-.58 0-.57-.32-.38-.66.19-.34.05-.08.07-.12C8.48 10.94 10.42 7.54 13 3h1l-1 7h3.5c.49 0 .56.33.47.51l-.07.15C12.96 17.55 11 21 11 21z',
ai:'M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25zm-7.5.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25z',
cut:'M9.64 7.64A2.98 2.98 0 0 0 10 6a3 3 0 1 0-3 3c.6 0 1.15-.18 1.62-.48L11 11l-2.38 2.48c-.47-.3-1.02-.48-1.62-.48a3 3 0 1 0 3 3c0-.6-.18-1.15-.48-1.62L12 12l7 7h3v-1zM6 8a2 2 0 1 1 2-2 2 2 0 0 1-2 2zm0 12a2 2 0 1 1 2-2 2 2 0 0 1-2 2zm6-7.5a.5.5 0 1 1 .5-.5.5.5 0 0 1-.5.5zM19 3l-6 6 2 2 7-7V3z',
shrink:'M7.41 18.59 8.83 20 12 16.83 15.17 20l1.41-1.41L12 14zm9.18-13.18L15.17 4 12 7.17 8.83 4 7.41 5.41 12 10z',
ext:'M19 19H5V5h7V3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2v-7h-2zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3z',
share:'M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81a3 3 0 1 0-3-3c0 .24.04.47.09.7L8.04 9.81A2.99 2.99 0 0 0 3 12a3 3 0 0 0 5.04 2.19l7.12 4.16c-.05.21-.08.43-.08.65a2.92 2.92 0 1 0 2.92-2.92z',
map:'M20.5 3l-.16.03L15 5.1 9 3 3.36 4.9c-.21.07-.36.25-.36.48V20.5c0 .28.22.5.5.5l.16-.03L9 18.9l6 2.1 5.64-1.9c.21-.07.36-.25.36-.48V3.5c0-.28-.22-.5-.5-.5zM15 19l-6-2.11V5l6 2.11z',
inject:'M21 3H3c-1.1 0-2 .9-2 2v3h2V5h18v14H3v-3H1v3c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM11 15l4-3-4-3v2H1v2h10z',
star:'M12 17.27 18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z',
palette:'M12 3a9 9 0 0 0 0 18c.83 0 1.5-.67 1.5-1.5 0-.39-.15-.74-.39-1.01-.23-.26-.38-.61-.38-.99 0-.83.67-1.5 1.5-1.5H16a5 5 0 0 0 5-5c0-4.42-4.03-8-9-8zm-5.5 9a1.5 1.5 0 1 1 1.5-1.5A1.5 1.5 0 0 1 6.5 12zm3-4A1.5 1.5 0 1 1 11 6.5 1.5 1.5 0 0 1 9.5 8zm5 0A1.5 1.5 0 1 1 16 6.5 1.5 1.5 0 0 1 14.5 8zm3 4a1.5 1.5 0 1 1 1.5-1.5 1.5 1.5 0 0 1-1.5 1.5z'};
/* ── per-world icon sets ────────────────────────────────────────────────────
   A world redraws only the ~16 glyphs that actually appear in the nav and the
   primary actions, and falls back to the base Material set for the other ~18.
   Redrawing all 34 four times would be 136 paths for glyphs most people never
   see; a missing key here is a fallback, never a blank box.

   Each set is a genuine restyle, not a recolour: Cyberpunk is angular and cut,
   Anime is round and sticker-like, Deck is hairline geometry, Graph builds every
   glyph out of nodes and edges. */
const ICON_SETS={
  cyber:{
    play:'M7 4l12 8-12 8zM4 4h2v16H4z',
    add:'M11 4h2v7h7v2h-7v7h-2v-7H4v-2h7z',
    search:'M4 4h10v2H6v8H4zm6 6h10v10H10zm2 2v6h6v-6z',
    settings:'M4 4h6v2H6v4H4zm10 0h6v6h-2V6h-4zm6 10v6h-6v-2h4v-4zM4 14h2v4h4v2H4z',
    chart:'M4 20V8h3v12zm5 0V4h3v16zm5 0v-8h3v8zm5 0v-5h3v5z',
    terminal:'M3 3h18v18H3zm3 4l4 4-4 4v2l6-6-6-6zm7 9h6v2h-6z',
    robot:'M8 2h8v3h4v14H4V5h4zm-1 7v3h3V9zm7 0v3h3V9zM8 15h8v2H8z',
    plug:'M8 2h2v6H8zm6 0h2v6h-2zM5 9h14v3l-3 4v4h-2v-4l-3-3-3 3v4H6v-4l-1-4z',
    doc:'M5 2h9l5 5v15H5zm9 1v5h5',
    folder:'M3 5h7l2 2h9v12H3zm2 4v8h14V9z',
    del:'M6 6h12l-1 15H7zM9 3h6v2H9z',
    check:'M4 12l2-2 4 4 8-8 2 2-10 10z',
    close:'M5 5h3l4 4 4-4h3l-5.5 7L19 19h-3l-4-4-4 4H5l5.5-7z',
    history:'M4 4h2v4h4v2H4zm8 0a8 8 0 1 1-7.4 11h2.3A6 6 0 1 0 12 6v3L8 5.5 12 2z',
    label:'M3 6h11l6 6-6 6H3zm3 5v2h2v-2z',
    help:'M12 2l8 5v10l-8 5-8-5V7zm-1 5v2h2V7zm0 4v6h2v-6z',
  },
  anime:{
    play:'M9 6.5c0-1.2 1.3-1.9 2.3-1.3l7 4.8c1 .7 1 2.2 0 2.9l-7 4.8c-1 .7-2.3 0-2.3-1.2z',
    add:'M12 3a1.6 1.6 0 0 1 1.6 1.6v5.8h5.8a1.6 1.6 0 0 1 0 3.2h-5.8v5.8a1.6 1.6 0 0 1-3.2 0v-5.8H4.6a1.6 1.6 0 0 1 0-3.2h5.8V4.6A1.6 1.6 0 0 1 12 3z',
    search:'M11 3a8 8 0 1 1-5 14.3l-2.4 2.4a1.5 1.5 0 0 1-2.2-2.1L3.8 15A8 8 0 0 1 11 3zm0 3.2A4.8 4.8 0 1 0 11 16a4.8 4.8 0 0 0 0-9.8z',
    settings:'M12 8.4a3.6 3.6 0 1 0 0 7.2 3.6 3.6 0 0 0 0-7.2zm8.4 4.8-1.9 1.5.5 2.4-2.2 1.3-2-1.4-2.3.9-.5 2.4h-2.5l-.5-2.4-2.3-.9-2 1.4-2.2-1.3.5-2.4-1.9-1.5v-2.4l1.9-1.5-.5-2.4L4.7 5.6l2 1.4 2.3-.9.5-2.4h2.5l.5 2.4 2.3.9 2-1.4 2.2 1.3-.5 2.4 1.9 1.5z',
    chart:'M5 13.5c0-.8.7-1.5 1.5-1.5S8 12.7 8 13.5v5c0 .8-.7 1.5-1.5 1.5S5 19.3 5 18.5zm5.5-7c0-.8.7-1.5 1.5-1.5s1.5.7 1.5 1.5v12c0 .8-.7 1.5-1.5 1.5s-1.5-.7-1.5-1.5zm5.5 4c0-.8.7-1.5 1.5-1.5s1.5.7 1.5 1.5v8c0 .8-.7 1.5-1.5 1.5s-1.5-.7-1.5-1.5z',
    terminal:'M5 3.5h14A2.5 2.5 0 0 1 21.5 6v12a2.5 2.5 0 0 1-2.5 2.5H5A2.5 2.5 0 0 1 2.5 18V6A2.5 2.5 0 0 1 5 3.5zm2.6 4.4a1.3 1.3 0 0 0 0 1.9L9.8 12l-2.2 2.2a1.3 1.3 0 1 0 1.9 1.9l3.1-3.2a1.3 1.3 0 0 0 0-1.8L9.5 7.9a1.3 1.3 0 0 0-1.9 0zM13.5 15h4v2h-4z',
    robot:'M12 2a1.4 1.4 0 0 1 1.4 1.4V5h3.4A3.2 3.2 0 0 1 20 8.2v8.6a3.2 3.2 0 0 1-3.2 3.2H7.2A3.2 3.2 0 0 1 4 16.8V8.2A3.2 3.2 0 0 1 7.2 5h3.4V3.4A1.4 1.4 0 0 1 12 2zM9 9.6a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2zm6 0a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2zM8.6 15.4a1.1 1.1 0 0 0 0 2.2h6.8a1.1 1.1 0 0 0 0-2.2z',
    plug:'M8.5 2a1.3 1.3 0 0 1 1.3 1.3V8h4.4V3.3a1.3 1.3 0 1 1 2.6 0V8h.9a1.3 1.3 0 0 1 1.3 1.3v2.2a6.5 6.5 0 0 1-5.2 6.4v3.8a1.3 1.3 0 1 1-2.6 0v-3.8A6.5 6.5 0 0 1 6 11.5V9.3A1.3 1.3 0 0 1 7.2 8h.1V3.3A1.3 1.3 0 0 1 8.5 2z',
    doc:'M7 2.5h7L19.5 8v11.5A2 2 0 0 1 17.5 21.5h-11A2 2 0 0 1 4.5 19.5V4.5a2 2 0 0 1 2-2zM13.5 4v4.5H18z',
    folder:'M4.5 4.5h5l2.2 2.2h7.8a2 2 0 0 1 2 2v8.8a2 2 0 0 1-2 2h-15a2 2 0 0 1-2-2V6.5a2 2 0 0 1 2-2z',
    del:'M9.6 2.5h4.8a1.4 1.4 0 0 1 1.4 1.4v.6h3.4a1.2 1.2 0 0 1 0 2.4h-.5l-1 12.4a2.2 2.2 0 0 1-2.2 2h-6a2.2 2.2 0 0 1-2.2-2l-1-12.4h-.5a1.2 1.2 0 0 1 0-2.4h3.4v-.6a1.4 1.4 0 0 1 1.4-1.4z',
    check:'M20.3 6.4a1.6 1.6 0 0 1 0 2.3L10.6 18.4a1.6 1.6 0 0 1-2.3 0L3.7 13.8a1.6 1.6 0 1 1 2.3-2.3l3.5 3.5 8.5-8.6a1.6 1.6 0 0 1 2.3 0z',
    close:'M5.7 4.3a1.5 1.5 0 0 0-2.1 2.1L9.9 12l-6.3 6.3a1.5 1.5 0 1 0 2.1 2.1L12 14.1l6.3 6.3a1.5 1.5 0 0 0 2.1-2.1L14.1 12l6.3-6.3a1.5 1.5 0 1 0-2.1-2.1L12 9.9z',
    history:'M12 3a9 9 0 1 1-8.6 11.6 1.4 1.4 0 1 1 2.7-.8A6.2 6.2 0 1 0 12 5.8c-1.5 0-2.9.5-4 1.4l1.7 1.7H4.5V3.6l1.6 1.6A8.9 8.9 0 0 1 12 3zm0 3.9a1.3 1.3 0 0 1 1.3 1.3v3.3l2.4 1.4a1.3 1.3 0 1 1-1.3 2.3l-3-1.8a1.3 1.3 0 0 1-.7-1.1V8.2A1.3 1.3 0 0 1 12 6.9z',
    label:'M4.5 5.5h9.1a2 2 0 0 1 1.5.7l4.6 5a1.2 1.2 0 0 1 0 1.6l-4.6 5a2 2 0 0 1-1.5.7H4.5a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2zm3 5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z',
    help:'M12 2.5a9.5 9.5 0 1 1 0 19 9.5 9.5 0 0 1 0-19zm0 13.2a1.3 1.3 0 1 0 0 2.6 1.3 1.3 0 0 0 0-2.6zm0-9.4a3.5 3.5 0 0 0-3.5 3.4 1.2 1.2 0 1 0 2.4 0 1.1 1.1 0 1 1 1.7.9c-.9.6-1.8 1.4-1.8 2.7a1.2 1.2 0 0 0 2.4 0c0-.2.4-.5.8-.7A3.5 3.5 0 0 0 12 6.3z',
  },
  deck:{
    play:'M9 5.5v13l10-6.5zM9 5.5 19 12 9 18.5z',
    add:'M11.6 4h.8v7.6H20v.8h-7.6V20h-.8v-7.6H4v-.8h7.6z',
    search:'M10.5 3.5a7 7 0 1 1 0 14 7 7 0 0 1 0-14zm0 .9a6.1 6.1 0 1 0 0 12.2 6.1 6.1 0 0 0 0-12.2zm5 11.4 5 5-.6.6-5-5z',
    settings:'M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm0 .9a2.1 2.1 0 1 0 0 4.2 2.1 2.1 0 0 0 0-4.2zM11.5 2h1v3h-1zm0 17h1v3h-1zM2 11.5h3v1H2zm17 0h3v1h-3zM4.6 4l2.2 2.1-.7.7L4 4.6zm12.6 12.6 2.2 2.1-.7.7-2.1-2.2zM19.4 4l.6.6-2.1 2.2-.7-.7zM6.8 16.6l.7.7L5.3 20l-.7-.7z',
    chart:'M3 20h18v.8H3zM5 14h1.6v5.2H5zm4 -4h1.6v9.2H9zm4 6h1.6v3.2H13zm4 -8h1.6v11.2H17z',
    terminal:'M2.5 4.5h19v15h-19zm.9.9v13.2h17.2V5.4zM5 8.2l3.6 3.6L5 15.4l.6.6 4.2-4.2L5.6 7.6zM11 15h6v.9h-6z',
    robot:'M11.6 2h.8v3h4.1a2 2 0 0 1 2 2v11.5a2 2 0 0 1-2 2H7.5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4.1zM6.4 7v11.5a1.1 1.1 0 0 0 1.1 1.1h9a1.1 1.1 0 0 0 1.1-1.1V7a1.1 1.1 0 0 0-1.1-1.1h-9A1.1 1.1 0 0 0 6.4 7zM9 9.5h1.8v1.8H9zm4.2 0H15v1.8h-1.8zM9 15h6v.9H9z',
    plug:'M8.5 2.5h.9V8h5.2V2.5h.9V8h2.1v3.3a6 6 0 0 1-5.1 5.9v4.3h-.9v-4.3a6 6 0 0 1-5.1-5.9V8h2.1zM7.3 8.9v2.4a5.1 5.1 0 0 0 10.2 0V8.9z',
    doc:'M6 2.5h8.2L18.5 6.8V21.5H6zm.9.9v17.2h10.7V7.6h-3.9V3.4zM15 4v2.7h2.7z',
    folder:'M2.5 4.5h7.2l2 2h9.8v13h-19zm.9.9v11.2h17.2V7.4h-9.3l-2-2z',
    del:'M9 2.5h6v2h5v.9H4v-.9h5zM5.5 7h13l-.9 14.5H6.4zm1 .9.8 12.7h9.4l.8-12.7z',
    check:'M4 12.4 5 11.4l4.7 4.7L19 6.8l1 1-10.3 10.3z',
    close:'M5.3 4.6 12 11.3l6.7-6.7.7.7L12.7 12l6.7 6.7-.7.7L12 12.7l-6.7 6.7-.7-.7L11.3 12 4.6 5.3z',
    history:'M12 3a9 9 0 1 1-8.9 10.3h.9A8.1 8.1 0 1 0 12 3.9a8 8 0 0 0-5.8 2.5l2.4 2.4H3.2V3.4l2.3 2.3A8.9 8.9 0 0 1 12 3zm-.5 3.5h.9v5.8l3.6 2.1-.5.8-4-2.3z',
    label:'M3 6h11.3l5.4 6-5.4 6H3zm.9.9v10.2h10l4.6-5.1-4.6-5.1zM7 11.1a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8z',
    help:'M12 2.5a9.5 9.5 0 1 1 0 19 9.5 9.5 0 0 1 0-19zm0 .9a8.6 8.6 0 1 0 0 17.2 8.6 8.6 0 0 0 0-17.2zm0 12.4a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6zm0-9a3.1 3.1 0 0 1 1.9 5.6c-.8.6-1.4 1-1.4 1.9h-1c0-1.3.9-2 1.8-2.6a2.2 2.2 0 1 0-3.4-1.8h-.9A3.1 3.1 0 0 1 12 6.8z',
  },
  graph:{
    // every glyph is built out of nodes and edges — the project's own graph
    play:'M6 4.6a2 2 0 1 1-1.4 3.4L4.5 16a2 2 0 1 1 2.6 2.6l9.3-5a2 2 0 1 1 .4-1.6zM7 8.2v7.4l7-3.7z',
    add:'M12 2.5a2 2 0 0 1 1 3.7V10h3.8a2 2 0 1 1 0 2h-3.8v3.8a2 2 0 1 1-2 0V12H7.2a2 2 0 1 1 0-2H11V6.2a2 2 0 0 1 1-3.7z',
    search:'M10 3a7 7 0 0 1 5.6 11.2l4.1 4.1a1.2 1.2 0 1 1-1.7 1.7l-4.1-4.1A7 7 0 1 1 10 3zm0 2a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0 2.6a2.4 2.4 0 1 1 0 4.8 2.4 2.4 0 0 1 0-4.8z',
    settings:'M12 2.4a2 2 0 0 1 1 3.7v1.3a4.6 4.6 0 0 1 3 1.7l1.2-.7a2 2 0 1 1 1 1.7l-1.2.7a4.6 4.6 0 0 1 0 2.4l1.2.7a2 2 0 1 1-1 1.7l-1.2-.7a4.6 4.6 0 0 1-3 1.7v1.3a2 2 0 1 1-2 0v-1.3a4.6 4.6 0 0 1-3-1.7l-1.2.7a2 2 0 1 1-1-1.7l1.2-.7a4.6 4.6 0 0 1 0-2.4l-1.2-.7a2 2 0 1 1 1-1.7l1.2.7a4.6 4.6 0 0 1 3-1.7V6.1a2 2 0 0 1 1-3.7zm0 6.8a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z',
    chart:'M5 17.4a2 2 0 1 1-1.3 3.5A2 2 0 0 1 5 17.4zm5-6a2 2 0 1 1 0 4 2 2 0 0 1 0-4zm5 3a2 2 0 1 1 0 4 2 2 0 0 1 0-4zm5-10a2 2 0 1 1 0 4 2 2 0 0 1 0-4zM6.4 17.9l2.6-4M11.6 13.2l2 1.6M16.4 13.1l2.4-5.4',
    terminal:'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm3.5 4.4a1.4 1.4 0 1 0 .8 2.5l1.3 1.1-1.3 1.1a1.4 1.4 0 1 0 .9 1.2l2.6-2.3-2.6-2.3a1.4 1.4 0 0 0-1.7-1.3zM13 15h5v1.6h-5z',
    robot:'M12 2a1.6 1.6 0 0 1 .8 3V5h4.4a2.4 2.4 0 0 1 2.4 2.4v9.2a2.4 2.4 0 0 1-2.4 2.4H6.8a2.4 2.4 0 0 1-2.4-2.4V7.4A2.4 2.4 0 0 1 6.8 5h4.4a1.6 1.6 0 0 1 .8-3zM9 9.4a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6zm6 0a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6zm-4.2 1.8h2.4',
    plug:'M9 2.4a1.7 1.7 0 0 1 .9 3.1V8h4.2V5.5a1.7 1.7 0 1 1 1.8 0V8h1.6a1 1 0 0 1 1 1v2.4a6.4 6.4 0 0 1-5.4 6.3v2.4a1.7 1.7 0 1 1-2.2 0v-2.4A6.4 6.4 0 0 1 5.5 11.4V9a1 1 0 0 1 1-1h1.6V5.5A1.7 1.7 0 0 1 9 2.4z',
    doc:'M7 2.5h7l4.5 4.5v12A2 2 0 0 1 16.5 21h-9a2 2 0 0 1-2-2V4.5a2 2 0 0 1 2-2zm2 6a1.4 1.4 0 1 0 0 2.8 1.4 1.4 0 0 0 0-2.8zm5 3a1.4 1.4 0 1 0 0 2.8 1.4 1.4 0 0 0 0-2.8zm-4 0.6 3 1.6',
    folder:'M4 5h5.5l2 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zm4 6a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm7 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm-5.6 1.5h4.2',
    del:'M9.5 2.5h5a1.5 1.5 0 0 1 1.5 1.5v1h4v2H4v-2h4V4a1.5 1.5 0 0 1 1.5-1.5zM5.8 9h12.4l-1 11a2 2 0 0 1-2 1.8H8.8a2 2 0 0 1-2-1.8zm4 3a1.3 1.3 0 1 0 0 2.6 1.3 1.3 0 0 0 0-2.6zm4.4 0a1.3 1.3 0 1 0 0 2.6 1.3 1.3 0 0 0 0-2.6z',
    check:'M6 10.2a2 2 0 1 1-1.6 3.2l3.9 4.2a2 2 0 1 0 2.9.2l7.4-8.2A2 2 0 1 0 17 8.2L10.7 15 8 12.1A2 2 0 0 0 6 10.2z',
    close:'M6 4a2 2 0 1 1-1.4 3.4l4.2 4.2-4.2 4.2A2 2 0 1 0 7.4 19l4.2-4.2 4.2 4.2A2 2 0 1 0 19 16.6l-4.2-4.2 4.2-4.2A2 2 0 1 0 16.6 5L12.4 9.2 8.2 5A2 2 0 0 0 6 4z',
    history:'M12 3a9 9 0 1 1-8.7 11.4 1.5 1.5 0 1 1 2.9-.8A6 6 0 1 0 12 6a6 6 0 0 0-4.1 1.6l1.9 1.9H4V4.2l1.8 1.8A8.9 8.9 0 0 1 12 3zm0 4a1.6 1.6 0 0 1 1 2.9l2.3 1.4a1.6 1.6 0 1 1-.8 1.4L11.2 11A1.6 1.6 0 0 1 12 7z',
    label:'M4 6h9.6a2 2 0 0 1 1.5.7l4.4 5a1.2 1.2 0 0 1 0 1.6l-4.4 5a2 2 0 0 1-1.5.7H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2zm3.5 4.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2z',
    help:'M12 2.5a9.5 9.5 0 1 1 0 19 9.5 9.5 0 0 1 0-19zm0 2a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15zm0 10.4a1.4 1.4 0 1 1 0 2.8 1.4 1.4 0 0 1 0-2.8zm0-8.2a3.3 3.3 0 0 1 1.9 6c-.6.4-.9.7-.9 1.2h-2c0-1.5 1-2.1 1.8-2.7a1.3 1.3 0 1 0-2-1.1h-2A3.3 3.3 0 0 1 12 6.7z',
  },
};
let ICONSET='';   // '' = the base Material set; a world name = its override
/* Falls back per glyph, not per set: a world only draws the icons it has an
   opinion about, and a missing key is never a blank box. */
const ic=n=>{
  const set=ICON_SETS[ICONSET];
  const d=(set&&set[n])||ICONS[n]||ICONS.doc;
  return `<svg class="ic" viewBox="0 0 24 24"><path d="${d}"/></svg>`;
};

/* ── theme → CSS variables (palette mirrors the TUI themes) ── */
function hexRgb(h){const n=parseInt(h.slice(1),16);return `${(n>>16)&255},${(n>>8)&255},${n&255}`;}
function applyTheme(name){
  // a world overrides whatever palette was passed: it is all or nothing
  const w=curWorld();
  if(w)name=w.palette;
  const t=(ST.themes||{})[name];if(!t)return;
  const r=document.documentElement.style;
  const map={'--cyan':t.accent,'--violet':t.accent2,'--ok':t.ok,'--warn':t.warn,
    '--err':t.err,'--bg':t.bg,'--bg2':t.bg2,'--panel':t.panel,'--panel2':t.panel2,
    '--line':t.line,'--txt':t.txt,'--dim':t.dim,'--dim2':t.dim2,'--code':t.code};
  for(const[k,v]of Object.entries(map))if(v)r.setProperty(k,v);
  // HUD hairlines/glows need the channels split for rgba(). The surface
  // channels are here for the same reason plus one more: a skin's `op` token
  // makes panels translucent so the stage shows through, and that needs
  // rgba(var(--panel-rgb),var(--sk-op)) — NOT opacity (which would fade the
  // text with it) and emphatically NOT backdrop-filter.
  for(const[k,v]of[['--cyan-rgb',t.accent],['--violet-rgb',t.accent2],
                   ['--ok-rgb',t.ok],['--warn-rgb',t.warn],
                   ['--panel-rgb',t.panel],['--panel2-rgb',t.panel2],
                   ['--bg2-rgb',t.bg2],['--bg-rgb',t.bg],
                   ['--line-rgb',t.line]])if(v)r.setProperty(k,hexRgb(v));
  // text on gradient: dark themes use their own bg, light themes white.
  // mode is authored per palette — a warm light bg like #fffcf0 and a dark
  // navy both start with 'f'/'0' in a way a luminance sniff gets wrong.
  const light=t.mode==='light';
  r.setProperty('--onacc',light?'#ffffff':t.bg);
  r.setProperty('--grad',`linear-gradient(135deg,${t.accent},${t.accent2})`);
  document.documentElement.classList.toggle('theme-light',light);
  // motion personality: how far things travel and how much they glow, per
  // palette. A single set of curves made Mono feel like Dracula.
  const mp=MOTION_PERSONA[t.motion]||MOTION_PERSONA.smooth;
  r.setProperty('--mo-lift',mp.lift);
  r.setProperty('--mo-glow',light?String(+mp.glow*.55):mp.glow);
  r.setProperty('--mo-beam',mp.beam);
  r.setProperty('--t-spring',mp.spring);
  applySkin(skinFor(name));
  applyWorld(w);
  if(window.INST)INST.setTheme(t);   // canvas can't parse var() — hand it hex
  if(window.STAGE)STAGE.setTheme(t); // ditto: GL wants hex, not a custom prop
}
/* The parts of a world a skin token cannot express: which glyph set the icons
   come from, the persistent overlay, the pointer behaviour and the cursor. */
function applyWorld(w){
  const r=document.documentElement;
  for(const n of Object.keys(ST.worlds||{}))r.classList.toggle('world-'+n,!!w&&ST.world===n);
  r.classList.toggle('in-world',!!w);
  ICONSET=(w&&w.icons)||'';
  MO.hover=(w&&w.hover)||'';
  const ov=$('#overlay');
  if(ov){
    ov.className=w&&w.overlay?('ovl-fx ov-'+w.overlay):'ovl-fx';
    ov.style.display=w&&w.overlay?'':'none';
  }
}
/* lift = hover travel, glow = accent bleed, beam = how fast a running border
   circles, spring = the response curve. Named in themes.py per palette. */
const MOTION_PERSONA={
  crisp: {lift:'-1px',glow:'.14',beam:'2s',  spring:'.18s cubic-bezier(.3,1.3,.6,1)'},
  smooth:{lift:'-2px',glow:'.22',beam:'2.6s',spring:'.26s cubic-bezier(.34,1.56,.64,1)'},
  lush:  {lift:'-3px',glow:'.34',beam:'3.4s',spring:'.34s cubic-bezier(.34,1.62,.58,1)'},
};

/* ── skins ─────────────────────────────────────────────────────────────────
   A palette says what colours; a skin says what the app IS — corner treatment,
   border weight, surface fill, heading type, how cards arrive, how a gauge is
   stroked. See themes.py SKINS for the data and the reasoning.

   Two halves, because neither alone is enough: the numeric/type tokens go out as
   --sk-* custom properties (so any rule can read them), and a `skin-<name>` class
   goes on <html> for the structural work a variable cannot express — corner
   brackets, warning stripes, scanlines, a block caret. */
/* ── worlds ────────────────────────────────────────────────────────────────
   A world is a theme that refuses to be mixed: it owns its palette, skin,
   background scene, icon set, overlay, hover behaviour and cursor, and while
   you wear one the palette and skin pickers are disabled.

   Why the orthogonal model was not enough: a skin that has to look sane under
   32 palettes can commit to nothing, which is how Sakura/Mecha/Glass ended up
   as loud wallpapers on weak chrome ("da buttare"). Classic mode is untouched —
   Slate + Terminal still works exactly as before — this sits above it. */
function curWorld(){return (ST.worlds||{})[ST.world]||null;}
function themeFor(){
  const w=curWorld();
  return w?w.palette:(ST.theme||'default');
}
function skinFor(themeName){
  const w=curWorld();
  if(w)return w.skin;                       // a world locks its own
  // an explicit user choice wins; otherwise wear what the palette asks for
  if(ST.skin&&(ST.skins||{})[ST.skin])return ST.skin;
  const t=(ST.themes||{})[themeName]||{};
  return ((ST.skins||{})[t.skin]?t.skin:'hud');
}
function applySkin(name){
  const sk=(ST.skins||{})[name];if(!sk)return;
  const r=document.documentElement;
  const st=r.style;
  st.setProperty('--sk-r',sk.radius+'px');
  st.setProperty('--sk-bw',sk.border+'px');
  st.setProperty('--sk-font',sk.font);
  st.setProperty('--sk-track',sk.track);
  st.setProperty('--sk-caps',sk.caps);
  st.setProperty('--sk-ease',sk.ease);
  // ── chassis. What makes switching a look read as switching app: surface
  // translucency, the frame the viewport sits in, and the signature treatments.
  //
  // Deliberately NOT here: type scale and row density. Those multipliers were
  // removed — a look may change what the app looks like, never how big it is.
  // The spacing and type scales were tuned once for legibility, and letting a
  // theme stretch them made some themes quietly worse laid out than others.
  // How much of the scene shows through every surface. The look proposes a
  // value; the user's slider, if they have moved it, wins for every look —
  // taste, and monitor, vary more than a designer can guess.
  st.setProperty('--sk-op',ST.surface?(ST.surface/100):(sk.op!=null?sk.op:1));
  for(const n of Object.keys(ST.skins||{}))r.classList.toggle('skin-'+n,n===name);
  for(const c of ['brackets','console','bezel','float','none'])
    r.classList.toggle('ch-'+c,(sk.chassis||'none')===c);
  for(const b of ['flat','notch','rail'])
    r.classList.toggle('tb-'+b,(sk.topbar||'flat')===b);
  SKIN=name;
  MO.skin=name;                      // picks the card-entrance choreography
  MO.arrive_=sk.arrive||'boot';      // …and the whole-page arrival sequence
  if(window.INST)INST.setSkin(sk);
  if(window.STAGE)STAGE.setSkin(name,sk);
}
let SKIN='hud';
function curSkin(){return (ST.skins||{})[SKIN]||{};}

/* ── prompt/confirm helpers ── */
function ask(title,fields,sub){return new Promise(res=>{
  $('#pTitle').textContent=title;$('#pSub').textContent=sub||'';
  $('#pBody').innerHTML=fields.map((f,i)=>f.type==='textarea'
    ?`<div class="fld"><label>${esc(f.label)}</label><textarea id="pf${i}">${esc(f.value||'')}</textarea></div>`
    :f.type==='select'
    ?`<div class="fld"><label>${esc(f.label)}</label><div class="chips" id="pf${i}">${f.options.map((o,j)=>
        `<span class="chip${j===0?' on':''}" data-v="${esc(o[0])}">${esc(o[1])}</span>`).join('')}</div></div>`
    /* multi: the same chips, without the deselect-siblings step. Returns a
       comma-joined string, which is the shape every consumer already wanted
       (agent `tools` frontmatter, for one). */
    :f.type==='multi'
    ?`<div class="fld"><label>${esc(f.label)}</label><div class="chips multi" id="pf${i}">${f.options.map(o=>
        `<span class="chip${(f.value||[]).includes(o[0]||o)?' on':''}" data-v="${esc(o[0]||o)}">${esc(o[1]||o)}</span>`).join('')}</div></div>`
    /* `list` = pick an existing one OR type a new one, in ONE control. A chip
       row cannot express "or something you have not thought of yet", and the
       two-field workaround (a select plus an "only if you picked New" box) is
       a puzzle. <datalist> is the native answer and needs no JS at all. */
    :`<div class="fld"><label>${esc(f.label)}</label><input id="pf${i}" value="${esc(f.value||'')}" placeholder="${esc(f.ph||'')}"${
        f.list?` list="pfl${i}" autocomplete="off"`:''}>${
        f.list?`<datalist id="pfl${i}">${f.list.map(o=>`<option value="${esc(o)}">`).join('')}</datalist>`:''}</div>`).join('');
  document.querySelectorAll('#pBody .chips').forEach(box=>
    box.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
      if(!box.classList.contains('multi'))
        box.querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));
      c.classList.toggle('on',box.classList.contains('multi')?undefined:true);}));
  $('#povl').classList.add('show');
  const done=v=>{$('#povl').classList.remove('show');res(v);};
  $('#pOk').onclick=()=>done(fields.map((f,i)=>{
    const el=$('#pf'+i);
    if(el.classList.contains('multi'))
      return [...el.querySelectorAll('.chip.on')].map(c=>c.dataset.v).join(',');
    return el.classList.contains('chips')?chipVal(el):el.value;}));
  $('#pCancel').onclick=()=>done(null);
  const first=$('#pf0');if(first)first.focus();
});}
function confirmBox(title,sub){return ask(title,[],sub).then(v=>v!==null);}

function fld(id,label){return `<div class="fld"><label>${label}</label><div class="chips" id="${id}"></div></div>`;}

/* ── job runner ────────────────────────────────────────────────────────────
   ONE poll loop, two presentations:

     inline  a banner in the page that started the job, with a travelling
             border while it runs. The rest of the UI stays usable, and the job
             survives navigation because the server's daemon thread does
             (gui_api._JOBS) — walk away and you still get the toast.
     modal   the blocking dialog, for when a job genuinely needs a decision
             from you before it can continue.

   Which one you get is NOT a per-kind allowlist. Every job starts inline and
   ESCALATES to the modal the moment it parks at an approval gate. An allowlist
   would go stale the first time some code path started calling
   diffview.confirm; this can't, because it reacts to what the job actually did.
   The practical win: an AI scaffold spends minutes in the Claude call before it
   has a diff to show, and that whole time the UI used to be hostage to a
   spinner over a dimmed page.

   The __pl* / __j* caches are load-bearing, not micro-optimisation: this polls
   every 600ms, and writing textContent/innerHTML unconditionally destroys and
   recreates nodes on every tick — the original flicker source (see CLAUDE.md). */
const JOBS={};
let __plMsgs='',__plSub='',__plLabel='';      // modal DOM caches

/* modal-first: for callers with nowhere sensible to put a banner */
function runJob(kind,params,onDone){
  return jobStart(kind,params,{onDone,modal:true});
}
/* inline: host is the element (or selector) that owns the banner */
function inlineJob(host,kind,params,o){
  o=o||{};
  const sel=typeof host==='string'?host:'';
  return jobStart(kind,params,{...o,sel,host:sel?$(sel):host});
}
/* Re-resolve by selector every render: a job's onDone often redraws the whole
   page, and a host captured once would be a detached node from then on. */
function jobHost(J){
  if(J.sel){const el=$(J.sel);if(el)J.host=el;}
  return (J.host&&J.host.isConnected)?J.host:null;
}
/* How busy the workspace looks, 0..1, for the background stage.
   Running work dominates — one job already means "something is happening" — and
   today's burn only sets a floor, so an active-but-idle workspace drifts instead
   of crawling. Deliberately NOT driven by burn alone: feeding a throughput
   number straight into a liveness test is the exact one-word bug that made the
   activity equalizer animate forever after a single token had been spent. */
function stageEnergy(jobs,burn){
  if(!window.STAGE)return;
  // /4, not /2: the input used to be claudectl's background jobs, where one was
  // already remarkable. It is now live Claude Code sessions, and two of those
  // is an ordinary Tuesday — at /2 the background would sit pinned at full
  // energy all day and stop meaning anything.
  const j=Math.min(1,(jobs||0)/4);
  const b=Math.min(1,Math.max(0,burn||0));
  STAGE.energy(Math.max(j,b*0.45));
}
/* jobs the UI knows are in flight right now, including plan-execute */
function liveJobs(){
  return Object.keys(JOBS).filter(k=>JOBS[k].status==='running').length
    +(PE&&PE.jid?1:0);
}
/* WHERE you are, as one comparable string. A job outlives navigation on purpose
   — that is the whole point of the inline banner — so its completion handler
   must not repaint the view it belonged to over the view you moved to. The
   NAV_ID guard inside paint() cannot catch this: onDone starts a NEW render, so
   the token matches. Same bug one level up, so the same shape of fix: the guard
   lives in the runner, not in fifteen call sites that each have to remember. */
function viewKey(){return PAGE_+'/'+(PAGE_==='project'?(CUR?CUR.encoded:'')+'/'+TAB:'');}
async function jobStart(kind,params,o){
  o=o||{};
  const r=await post('/api/job',{kind,...params});
  if(!r.ok){toast(r.error||'Could not start','err');return null;}
  const J={jid:r.job,kind,label:o.label||'Working…',status:'running',msgs:[],
    elapsed:0,sub:'',err:'',host:o.host||null,sel:o.sel||'',onDone:o.onDone||null,
    // `redraw` is the guarded half of onDone: it runs ONLY if the user is still
    // looking at the view the job was started from.
    redraw:o.redraw||null,from:viewKey(),
    modal:!!o.modal||!(o.host||o.sel),
    // memory_build reports sub-step progress through its own endpoint
    memPath:kind==='memory_build'?params.path:null};
  JOBS[J.jid]=J;
  if(J.modal)modalOpen(J);else inlineRender(J);
  stageEnergy(liveJobs(),0);
  jobPoll(J);
  return J;
}
/* Take over a job this client is not already tracking.
   A job parked at an approval gate blocks until someone answers it, and the
   client that started it may be a different window — or this one after a
   reload, which drops JOBS entirely. Adopting it re-enters the SAME poll loop,
   so it escalates to the gate modal by the existing path rather than by a
   second implementation of gate rendering. */
function jobOpen(jid,label){
  const J=JOBS[jid]||{jid,kind:'',label:label||'Job',status:'running',msgs:[],
    elapsed:0,sub:'',err:'',host:null,sel:'',onDone:null,memPath:null};
  J.modal=true;
  if(!JOBS[jid]){JOBS[jid]=J;jobPoll(J);}
  else modalOpen(J);
}
async function jobPoll(J){
  if(!JOBS[J.jid])return;                       // finished or superseded
  if(document.hidden||!_VIS){setTimeout(()=>jobPoll(J),800);return;}
  let st=null;
  try{st=await api(`/api/job/${J.jid}`);}catch(e){st=null;}
  if(!JOBS[J.jid])return;
  // a transient backend hiccup must not kill the chain — a dead chain leaves a
  // spinner up forever with no way to learn the job already finished
  if(!st){setTimeout(()=>jobPoll(J),1500);return;}
  J.status=st.status;J.label=st.label||J.label;
  J.msgs=st.messages||[];J.elapsed=st.elapsed||0;
  J.sub=`${J.elapsed}s elapsed`;
  if(J.memPath&&st.status==='running'){
    try{const mp=await api('/api/memory/progress?path='+encodeURIComponent(J.memPath));
      if(mp.progress)J.sub+=` — ${mp.progress}`;}catch(e){}
  }
  if(!J.modal)J.sub+=" — keep working; you'll be notified when it's done";
  if(st.status==='awaiting'&&st.gate){
    if(!J.modal){J.modal=true;inlineClear(J);}   // escalate: a decision blocks
    modalGate(J,st.gate);
    return;                                      // parked until decided
  }
  if(J.modal)modalUpdate(J);else inlineRender(J);
  if(st.status==='running'){
    setTimeout(()=>jobPoll(J),J.elapsed>60?4000:J.elapsed>15?1500:600);return;}
  jobFinish(J,st);
}
/* the guarded refresh: silently skipped when you have navigated on */
function jobRedraw(J,st){
  if(!J.redraw)return;
  if(viewKey()!==J.from)return;
  J.redraw(st);
}
function jobFinish(J,st){
  delete JOBS[J.jid];
  stageEnergy(liveJobs(),0);      // the background settles as the work stops
  if(J.modal)$('#jovl').classList.remove('show');
  if(st.status==='done'){
    inlineClear(J);toast(J.label+' — done','ok');
    if(J.onDone)J.onDone(st);
    jobRedraw(J,st);
  }else if(st.status==='cancelled'){
    inlineClear(J);toast('Cancelled','');
  }else{
    // a job can run for minutes; a 3.5s toast is not enough to report why it
    // died, so an inline failure stays on the page until dismissed
    J.err=st.error||'Failed';
    if(!J.modal&&jobHost(J))inlineRender(J);else toast(J.err,'err');
    if(st.error&&J.onDone)J.onDone(st);
    if(st.error)jobRedraw(J,st);
  }
}
/* ── inline presentation ── */
function inlineRender(J){
  const host=jobHost(J);
  if(!host)return;                      // navigated away; the job runs on
  if(J.err){
    host.__jshown=0;host.style.display='';
    host.innerHTML=`<div class="perun err">${ic('close')}
      <div style="flex:1"><b>${esc(J.label)} failed</b>
        <div class="sub">${esc(J.err)}</div></div>
      <button class="btn sm" data-jd="1">Dismiss</button></div>`;
    host.querySelector('[data-jd]').onclick=()=>{J.err='';inlineClear(J);};
    return;
  }
  if(!host.__jshown){
    host.__jshown=1;host.__jsub='';host.__jmsgs='';host.style.display='';
    host.innerHTML=`<div class="perun beam"><span class="spin"></span>
      <div style="flex:1;min-width:0"><b class="jlbl"></b>
        <div class="sub"></div><div class="msgs"></div></div>
      <button class="btn sm danger" data-jc="1">Cancel</button></div>`;
    host.querySelector('.jlbl').textContent=J.label;
    host.querySelector('[data-jc]').onclick=async ev=>{
      const b=ev.currentTarget;b.disabled=true;b.textContent='Cancelling…';
      await post(`/api/job/${J.jid}/cancel`);};
  }
  const sub=host.querySelector('.sub');
  if(J.sub!==host.__jsub&&sub){host.__jsub=J.sub;sub.textContent=J.sub;}
  const html=(J.msgs||[]).slice(-3).map(m=>
    `<div class="${m.ok?'':'bad'}">${esc(m.text)}</div>`).join('');
  const mb=host.querySelector('.msgs');
  if(html!==host.__jmsgs&&mb){host.__jmsgs=html;mb.innerHTML=html;}
}
function inlineClear(J){
  const host=jobHost(J);if(!host)return;
  host.__jshown=0;host.__jsub='';host.__jmsgs='';
  host.style.display='none';host.innerHTML='';
}
/* ── modal presentation ── */
function modalOpen(J){
  __plMsgs='';__plSub='';__plLabel='';
  $('#jovl').classList.add('show');$('#jGate').style.display='none';
  $('#jCancelRow').style.display='';
  $('#jTitle').innerHTML='<span class="spin"></span> <span id="jLabel">Working…</span>';
  $('#jSub').textContent='';$('#jMsgs').innerHTML='';
  $('#jCancel').disabled=false;$('#jCancel').textContent='Cancel';
  $('#jCancel').onclick=async()=>{
    const btn=$('#jCancel');btn.disabled=true;btn.textContent='Cancelling…';
    await post(`/api/job/${J.jid}/cancel`);
  };
}
function modalUpdate(J){
  if(!$('#jovl').classList.contains('show'))modalOpen(J);
  const lab=$('#jLabel');       // absent while the gate owns #jTitle
  if(J.label!==__plLabel&&lab){__plLabel=J.label;lab.textContent=J.label;}
  const sub=J.sub;
  if(sub!==__plSub){__plSub=sub;$('#jSub').textContent=sub;}
  const msgsHtml=(J.msgs||[]).map(m=>
    `<div class="${m.ok?'':'bad'}">${esc(m.text)}</div>`).join('');
  if(msgsHtml!==__plMsgs){__plMsgs=msgsHtml;$('#jMsgs').innerHTML=msgsHtml;}
}
function modalGate(J,gate){
  modalUpdate(J);
  $('#jGate').style.display='';$('#jCancelRow').style.display='none';
  $('#jTitle').innerHTML=esc(gate.title);
  $('#jDiff').innerHTML=(gate.diff||[]).map(l=>{
    const c=l.startsWith('+++')||l.startsWith('---')||l.startsWith('@@')?'h'
          :l.startsWith('+')?'a':l.startsWith('-')?'d':'';
    return `<div class="${c}">${esc(l)}</div>`;}).join('')||'<div>(no diff)</div>';
  const decide=async apply=>{
    __plMsgs='';__plLabel='';
    await post(`/api/job/${J.jid}/decide`,{apply});
    $('#jGate').style.display='none';$('#jCancelRow').style.display='';
    $('#jTitle').innerHTML='<span class="spin"></span> <span id="jLabel">Working…</span>';
    setTimeout(()=>jobPoll(J),300);
  };
  $('#jApply').onclick=()=>decide(true);
  $('#jReject').onclick=()=>decide(false);
}

/* ── sidebar ── */
/* Twelve equal rows in one flat column read as a wall, and they took ~46% of the
   sidebar's height away from the project list — the thing the sidebar is FOR.
   Grouping is the fix for the reading problem; capping the height (.nav in
   app.css) is the fix for the space problem, and both are needed. */
/* [id, icon, label, blurb, renderer] — FIVE fields, and the last two are why:
   drawPage kept a title map and a renderer map beside this list, and pgHelp kept
   a hand-typed third copy that had already drifted (it listed a dead page and
   omitted five live ones). The sentence describing a page now lives next to that
   page's own nav entry, where the author of a new page is already standing, and
   the help screen is rendered from this array instead of retyped.
   Referencing the pg* functions here is safe: they are hoisted `function`
   declarations, so the initializer runs before their definitions with no TDZ. */
const NAV_GROUPS=[
  ['Workspace',[
    ['usage','chart','Usage','Token spend and rate limits across every account, by day and by project.',()=>pgUsage],
    ['searchp','search','Search','Full-text search over every session transcript on the machine.',()=>pgSearch],
    ['loops','refresh','Loops','Start a /loop in its own session, watch it fire, end it — and the loop.md that says what a bare /loop does.',()=>pgLoops]]],
  ['Library',  [
    ['agents','robot','Agents','Subagent definitions: browse the library, write one by hand or have Claude draft it.',()=>pgAgents],
    ['skills','ai','Skills','SKILL.md skills — bundled templates, your library, and the ones installed in a project.',()=>pgSkills],
    ['hooks','link','Hooks','Claude Code hooks per account: install from a template, enable, disable or remove.',()=>pgHooks],
    ['plugins','folder','Plugins','Marketplaces and installed plugins, with the provenance of everything they shipped.',()=>pgPlugins],
    ['ostyles','palette','Output styles','Output styles Claude Code can wear, and which one is active.',()=>pgOStyles],
    ['mcp','plug','MCP servers','MCP servers: status, detail and the tool documentation they can write into the global CLAUDE.md.',()=>pgMcp],
    ['globalmd','doc','Global CLAUDE.md','The instructions Claude reads in every session on an account, its loop.md, and the conventions worth promoting into it.',()=>pgGlobalMd]]],
  ['System',   [
    ['accounts','group','Accounts','Every Claude login, and the sync that levels them all up to the same provisioning.',()=>pgAccounts],
    ['client','ai','Claude Code','What Claude Code records about itself: versions, disk, background agents, its own settings.',()=>pgClient],
    ['logs','history','Logs','What claudectl itself did and why it failed — its own Claude calls, background jobs, the scheduler and the proxy, newest first.',()=>pgLogs],
    ['settings','settings','Settings','Launch defaults, paths and limits, models, appearance, updates and telemetry.',()=>pgSettings],
    ['helpp','help','Help','This page: every screen in the app and every key in the terminal UI.',()=>pgHelp]]],
];
/* The FLAT list stays the public one: the command palette walks it, and both
   tools/smoke_gui.py and tools/shot_gui.py evaluate `NAV.map(n => n[0])` to get
   the page list rather than hardcoding one. Derived, never maintained twice. */
const NAV=NAV_GROUPS.flatMap(g=>g[1]);
/* Collapsed groups persist in claudectl.json (`nav_collapsed`), not in
   localStorage: every other appearance choice this app makes is a setting, and
   the Qt shell and a browser tab have to agree about the chrome. Stored as
   NAMES rather than indices so reordering or inserting a group never silently
   collapses a different one. */
/* ── sidebar width: drag the grip, and it sticks ──────────────────────────────
   Persisted as a setting, like every other chrome choice, so the Qt shell and a
   browser tab agree. Clamped rather than free: below SIDE_MIN the project paths
   and the nav labels are unreadable, and above SIDE_MAX the sidebar starts
   taking the space the content needs. 0 means "never set" — the CSS fallback
   stays in charge, which is what keeps the default in ONE place. */
const SIDE_MIN=210,SIDE_MAX=520,NAV_MIN=34,PLIST_MIN=120;
/* 0 always means "never dragged, the CSS default is in charge", for both axes.
   Storing the actual default instead would put it in two places and make the
   next change to app.css a silent no-op for everyone who has ever dragged. */
function applySideWidth(px){
  const r=document.documentElement;
  px?r.style.setProperty('--side-w',clampSide(px)+'px'):r.style.removeProperty('--side-w');
}
function applyNavHeight(px){
  const r=document.documentElement;
  if(px){r.style.setProperty('--nav-h',px+'px');r.classList.add('nav-sized');}
  else{r.style.removeProperty('--nav-h');r.classList.remove('nav-sized');}
}
function clampSide(px){return Math.min(SIDE_MAX,Math.max(SIDE_MIN,Math.round(px)));}
function clampNav(px){
  // the nav may grow until the project list hits its own floor — expressed
  // against the live box rather than a guessed constant, so it stays right at
  // any window height and for any number of nav groups the user has open
  const list=$('#plist'),foot=document.querySelector('.side .foot');
  if(!list||!foot)return Math.max(NAV_MIN,Math.round(px));
  const room=foot.getBoundingClientRect().top-list.getBoundingClientRect().top-PLIST_MIN;
  return Math.min(Math.max(room,NAV_MIN),Math.max(NAV_MIN,Math.round(px)));
}
/* ONE binder for both grips. Two copies of this would be two chances for the
   release handler to leak a document-level pointermove listener, which is the
   classic way a drag handle ends up moving things after you let go. */
function bindGrip(id,{measure,clamp,apply,read,save,step}){
  const g=$('#'+id);if(!g||g.__bound)return;g.__bound=1;
  const cls=id+'-drag';
  let v=0;
  const move=ev=>{v=clamp(measure(ev));apply(v);};
  const up=ev=>{
    document.removeEventListener('pointermove',move);
    document.removeEventListener('pointerup',up);
    document.removeEventListener('pointercancel',up);
    document.documentElement.classList.remove(cls);
    if(window.INST)INST.refit();
    if(v)save(v);
  };
  g.addEventListener('pointerdown',ev=>{
    ev.preventDefault();
    document.documentElement.classList.add(cls);
    document.addEventListener('pointermove',move);
    document.addEventListener('pointerup',up);
    document.addEventListener('pointercancel',up);
  });
  g.addEventListener('dblclick',()=>{v=0;apply(0);save(0);
    if(window.INST)INST.refit();});
  // a focusable separator has to be operable from the keyboard
  g.addEventListener('keydown',ev=>{
    const d=step(ev.key);if(!d)return;
    ev.preventDefault();
    v=clamp(read()+d);apply(v);save(v);
    if(window.INST)INST.refit();
  });
}
function bindSideGrips(){
  bindGrip('sgrip',{
    // the sidebar starts at the viewport's left edge, so clientX IS the width
    measure:ev=>ev.clientX, clamp:clampSide, apply:applySideWidth,
    read:()=>ST.side_w||$('.side').offsetWidth,
    save:v=>{ST.side_w=v;post('/api/settings',{side_w:v});},
    step:k=>k==='ArrowLeft'?-16:k==='ArrowRight'?16:0});
  bindGrip('hgrip',{
    // grip-to-footer is exactly the nav's height, so no offset bookkeeping
    measure:ev=>document.querySelector('.side .foot').getBoundingClientRect().top-ev.clientY,
    clamp:clampNav, apply:applyNavHeight,
    read:()=>ST.nav_h||$('#nav').offsetHeight,
    save:v=>{ST.nav_h=v;post('/api/settings',{nav_h:v});},
    step:k=>k==='ArrowUp'?16:k==='ArrowDown'?-16:0});
}

/* In the icon rail there IS no group label to click, so a collapsed group would
   have no way back. The rail therefore ignores the collapsed set entirely; the
   saved setting is untouched and comes back when the window widens again. */
const RAIL=window.matchMedia('(max-width:1099px)');
RAIL.addEventListener('change',()=>{if(ST)drawNav();});
function navSaved(){return new Set(ST.nav_collapsed||[]);}
function navCollapsed(){return RAIL.matches?new Set():navSaved();}
async function toggleNavGroup(grp){
  // the SAVED set, never the rail-filtered view — toggling off a rail would
  // otherwise persist an empty set and silently discard the user's choices
  const set=navSaved();
  set.has(grp)?set.delete(grp):set.add(grp);
  ST.nav_collapsed=[...set];
  drawNav();
  // one write per click; the page never re-reads, so a failed save is the only
  // thing that could desync and it surfaces as the usual toast
  await post('/api/settings',{nav_collapsed:ST.nav_collapsed});
}
/* Nav rows carry no gauge. They used to each own an animated canvas, which put
   ~10 looping surfaces in the chrome to encode numbers about pages you weren't
   looking at — the clearest case of motion that cost frames and said nothing. */
function drawNav(){
  const col=navCollapsed();
  $('#nav').innerHTML=NAV_GROUPS.map(([grp,items])=>{
    const shut=col.has(grp);
    // a collapsed group still shows a dot per page it hides, so the row is not
    // an empty promise — and the current page's dot is lit, which is how you
    // can tell where you are without opening it
    const pips=shut?`<span class="gpip">${items.map(([id])=>
      `<i class="${PAGE_===id?'on':''}"></i>`).join('')}</span>`:'';
    return `<div class="grp${shut?' shut':''}" onclick="toggleNavGroup(${hesc(grp)})"
        role="button" tabindex="0" aria-expanded="${!shut}"
        title="${shut?'Expand':'Collapse'} ${esc(grp)}">
        <svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>
        <span>${esc(grp)}</span>${pips}</div>`
      +(shut?'':items.map(([id,i,l])=>
        `<div class="it${PAGE_===id?' sel':''}" onclick="go(${hesc(id)})" title="${esc(l)}">${ic(i)} <span>${l}</span></div>`
      ).join(''));
  }).join('');
}
/* 14-day activity trace per project. Static inline SVG, never animated — this
   renders once per sidebar row and there can be dozens, so it is deliberately
   the cheapest possible thing: no canvas, no scheduler, no per-frame cost. The
   gradient area fill is what lets a 13px-tall trace read at all. */
let PROJ_SPARK={};
function miniSpark(vals){
  if(!vals||vals.length<2||!vals.some(v=>v>0))return '';
  const w=44,h=13,mx=Math.max(...vals),n=vals.length;
  const pt=(v,i)=>`${(i*(w-1)/(n-1)).toFixed(1)},${(h-1-(v/mx)*(h-2)).toFixed(1)}`;
  const pts=vals.map(pt).join(' ');
  return `<svg class="mspark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">
    <polygon points="0,${h} ${pts} ${w-1},${h}"/><polyline points="${pts}"/></svg>`;
}
/* Sidebar rows are reconciled by key, not rebuilt. The old innerHTML='' + append
   loop threw away every row on any change — losing hover state and making a
   single project's timestamp tick look like the whole list flashing. */
function drawProjects(){
  const q=($('#q').value||'').toLowerCase();
  const box=$('#plist');if(!box)return;
  const list=ST.projects.filter(p=>(SHOW_HIDDEN||!p.hidden)&&(!q
    ||p.name.toLowerCase().includes(q)||p.path.toLowerCase().includes(q)));
  drawHiddenToggle();
  MO.patch(box,list,p=>p.encoded,p=>{
    // Account chips are flex:none, so with three of them they claimed the whole
    // row and the project NAME shrank to a single character ("Claude" rendered
    // as "("). Only the first two are ever named; the rest collapse into one
    // +N chip that still carries them in its tooltip, and .pn keeps a floor
    // width in app.css so no number of accounts can squeeze it out again.
    const extra=p.accounts.slice(1);
    const shown=extra.slice(0,2).map(a=>
      `<span class="tag acct" style="color:${acctColor(a)}">${esc(a)}</span>`);
    if(extra.length>2)shown.push(
      `<span class="tag acct more" title="${esc(extra.slice(2).join(', '))}">+${extra.length-2}</span>`);
    const tags=shown.length?' '+shown.join(' '):'';
    const active=ACTIVE_MEM.has(p.path);
    // a scanning project gets a live pip, not a spinning icon: the ring reads as
    // "this is happening" without dragging the eye across the sidebar
    const amk=(p.auto_memory||active)
      ?`<span class="amk${active?' pip':''}" title="${active?'memory updating now':'auto-memory on'}">${ic('refresh')}</span>`:'';
    const last=p.last_active?` <span style="opacity:.7">· ${esc(p.last_active)} ago</span>`:'';
    const hid=p.hidden?' <span class="tag" title="hidden from the project list">hidden</span>':'';
    return `<div class="nm"><span class="pn" title="${esc(p.name)}">${esc(p.name)}</span>${hid}${tags}${amk}</div>`
      +`<div class="pt"><span class="pp">${esc(p.path)}${last}</span>`
      +`${miniSpark(PROJ_SPARK[p.encoded])}</div>`;
  },p=>'proj lift'+(CUR&&PAGE_==='project'&&CUR.encoded===p.encoded?' sel':''));
  if(!box.__bound){box.__bound=1;
    box.addEventListener('click',ev=>{
      const row=ev.target.closest('.proj');if(!row)return;
      const p=ST.projects.find(x=>x.encoded===row.__mokey);
      if(p)openProject(p);
    });}
}
/* Hiding a project is a VIEW flag (settings), not a file move — the sessions of
   a hidden project stay exactly where Claude Code put them, and the TUI reads
   the same flag. The reveal button only exists while something is hidden. */
let SHOW_HIDDEN=false;
function drawHiddenToggle(){
  const b=$('#bHidden');if(!b)return;
  const n=(ST.projects||[]).filter(p=>p.hidden).length;
  b.style.display=n?'':'none';
  b.innerHTML=ic('eye')+' '+(SHOW_HIDDEN?'Hide':'Show')
    +` ${n} hidden project${n===1?'':'s'}`;
  if(!b.__bound){b.__bound=1;
    b.onclick=()=>{SHOW_HIDDEN=!SHOW_HIDDEN;drawProjects();};}
}
async function toggleHidden(){
  if(!CUR)return;
  const hidden=!CUR.hidden;
  const r=await post('/api/project/hide',{enc:CUR.encoded,hidden});
  if(r.error){toast('Failed: '+r.error,'err');return;}
  CUR.hidden=hidden;
  const p=(ST.projects||[]).find(x=>x.encoded===CUR.encoded);if(p)p.hidden=hidden;
  if(hidden)SHOW_HIDDEN=true;   // don't make the page you're on vanish from the list
  toast(hidden?'Project hidden':'Project restored','ok');
  drawProjects();drawProject();
}

/* ── router ── */
function go(page){PAGE_=page;CUR=page==='project'?CUR:null;
  stopDashboard();   // a detached home view never keeps fetching/writing
  stopLoopPoll();    // …and the loops board is the same promise
  applyTheme(ST.theme);   // drop any unsaved theme preview
  // the stage is global and never restarts across navigation — that is what
  // makes it ONE background rather than seven wallpapers — but each page tilts
  // it (density, camera) and the move itself sends a ripple through it
  if(window.STAGE){STAGE.page(page);STAGE.impulse();}
  render();}
function render(){
  NAV_ID++;
  drawNav();drawProjects();
  $('#tabs').style.display=PAGE_==='project'?'flex':'none';
  $('#pactions').style.display=PAGE_==='project'?'':'none';
  if(PAGE_!=='project')stopMemBadge();   // hide the badge off a project page
  if(PAGE_==='home')drawHome();
  else if(PAGE_==='project')drawProject();
  else drawPage(PAGE_);
  // navigation should read as a move, not a substitution. Fires here rather
  // than inside each renderer so async pages animate their shell immediately
  // instead of after the fetch lands.
  MO.page($('#content'));
}
/* Post-render: adopt any instruments the page emitted, tween any [data-num]
   readouts, stagger the cards in. `mounted` is the single place that knows the
   order these have to happen in — mount before count, or a readout tweens
   against a gauge that has not sized itself yet.

   ~20 renderers write #content, several of them async, so rather than requiring
   each to remember this call an observer on #content's DIRECT children runs it
   (see watchContent). Direct children only: MO.patch appending rows into a list
   host is a subtree change and must not retrigger a whole-page remount. */
function mounted(root){
  const el=root||$('#content');
  // the fade-out at the bottom of a scroll list only makes sense while there IS
  // something below the fold
  el.querySelectorAll('.dlist').forEach(d=>
    d.classList.toggle('full',d.scrollHeight<=d.clientHeight+1));
  tblStack(el);
  INST.mount(el);
  MO.counts(el);
  // the skin's arrival sequence, not a linear delay ramp in DOM order — see
  // SKIN_ARRIVE. Falls back to the old per-card reveal without the vendor bundle.
  MO.arrive(el,'.card,.mo-in');
}
/* ONE filter box for the three lists long enough to need one: the skills
   inventory, the agent library (155 agents in 10 categories) and the project
   agent picker. Filtering is client-side over markup already on the page —
   these lists arrive in a single payload, so a round trip per keystroke would
   be slower and no more correct.

   Rows opt in with `data-f` (the text to match, which is usually more than the
   row displays); a <details> category with nothing left in it hides, and one
   with a hit opens itself, because a match you cannot see is not a match. */
function bindFilter(inputSel,rowSel,countId,extra){
  const inp=$('#'+inputSel.replace(/^#/,''));
  if(!inp)return;
  // `extra` is a second, non-text condition (the skills page's scope chips).
  // It runs in the SAME pass rather than as a second sweep over display:
  // two independent writers of one style property is how a row ends up hidden
  // by the filter that is off.
  const run=window['__f_'+inputSel]=()=>{
    const q=(inp.value||'').toLowerCase().trim();
    let shown=0,total=0;
    document.querySelectorAll('#content '+rowSel).forEach(el=>{
      const inScope=!extra||extra(el);
      if(inScope)total++;
      // title as well as text: a chip shows a name and carries its description
      const hay=(el.dataset.f||(el.title+' '+el.textContent)||'').toLowerCase();
      // something already SELECTED never hides — filtering is a way to find
      // more, not a way to lose track of what you picked
      const hit=inScope&&(!q||hay.includes(q)||el.classList.contains('on'));
      el.style.display=hit?'':'none';
      if(hit)shown++;
    });
    /* A container whose rows are all hidden must go too, or the filter leaves
       a category heading standing over nothing. `.fgrp` is the plain version
       of the same idea as <details> — a group header plus its rows — and it
       gets the identical treatment minus the open/closed state. */
    document.querySelectorAll('#content details, #content .fgrp').forEach(dt=>{
      const rows=[...dt.querySelectorAll(rowSel)];
      if(!rows.length)return;
      const any=rows.some(el=>el.style.display!=='none');
      dt.style.display=any?'':'none';
      if(q&&any&&dt.tagName==='DETAILS')dt.open=true;
    });
    const c=countId&&$('#'+countId);
    if(c)c.textContent=q?`${shown} of ${total}`:'';
  };
  inp.oninput=run;
  run();
}
/* Copy each table's header text onto its cells so the narrow-window stacked
   layout can label them (see the .tbl @media rule). Done in JS because the
   header text lives in <th> and CSS can't reach across to it — and done once at
   mount rather than in 20 renderers' markup. */
function tblStack(root){
  (root||document).querySelectorAll('table.tbl').forEach(t=>{
    if(t.__stk)return;t.__stk=1;
    const hs=[...t.querySelectorAll('tr')].shift();
    if(!hs)return;
    const labels=[...hs.children].map(c=>c.textContent.trim());
    if(!labels.length)return;
    t.querySelectorAll('tr').forEach(tr=>{
      if(tr===hs)return;
      [...tr.children].forEach((td,i)=>{
        if(labels[i]&&!td.dataset.th)td.dataset.th=labels[i];});
    });
  });
}
let __mntQ=null;
function watchContent(){
  const el=$('#content');if(!el||!window.MutationObserver)return;
  new MutationObserver(()=>{
    if(__mntQ)return;                      // coalesce a burst into one pass
    __mntQ=Promise.resolve().then(()=>{__mntQ=null;mounted();});
  }).observe(el,{childList:true});
}

/* ── command palette (Ctrl+K) — navigates the real NAV/TABS route list ── */
let PAL_EL=null;
function palette(){
  if(PAL_EL)return;
  const entries=[['home','Home'],...NAV.map(([id,,l])=>[id,l]),
    ...(CUR?TABS.map(([id,l])=>[id,l]):[])];
  const ov=document.createElement('div');
  ov.className='palette';
  ov.innerHTML=`<div class="pbox"><input id="pInp" placeholder="Jump to…"
    autocomplete="off" spellcheck="false"><div id="pList"></div></div>`;
  ov.addEventListener('mousedown',e=>{if(e.target===ov)closePalette();});
  document.body.appendChild(ov);
  PAL_EL=ov;
  const inp=$('#pInp'),list=$('#pList');
  let rows=[],sel=0;
  const draw=()=>{
    const q=(inp.value||'').toLowerCase();
    rows=entries.filter(([id,l])=>!q||l.toLowerCase().includes(q));
    sel=Math.min(sel,Math.max(0,rows.length-1));
    list.innerHTML=rows.map(([id,l],i)=>
      `<div class="prow${i===sel?' sel':''}" data-id="${id}">${esc(l)}</div>`).join('')
      ||'<div class="pempty">no match</div>';
    const el=list.children[sel];if(el)el.scrollIntoView({block:'nearest'});
  };
  const pick=()=>{
    const r=rows[sel];if(!r)return;
    closePalette();
    const id=r[0];
    if(id==='home')go('home');
    else if(NAV.some(n=>n[0]===id))go(id);
    else{TAB=id;go('project');}              // project tab
  };
  inp.addEventListener('input',()=>{sel=0;draw();});
  inp.addEventListener('keydown',e=>{
    if(e.key==='ArrowDown'){e.preventDefault();sel=Math.min(rows.length-1,sel+1);draw();}
    else if(e.key==='ArrowUp'){e.preventDefault();sel=Math.max(0,sel-1);draw();}
    else if(e.key==='Enter'){e.preventDefault();pick();}
    else if(e.key==='Escape'){e.preventDefault();closePalette();}
  });
  draw();
  inp.focus();
}
function closePalette(){const el=PAL_EL;PAL_EL=null;if(el)el.remove();}
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&(e.key==='k'||e.key==='K')){
    const t=document.activeElement;
    if(t&&(t.tagName==='TEXTAREA'||t.tagName==='INPUT'))return;
    e.preventDefault();                    // stops the browser find bar
    if(PAL_EL)closePalette();else palette();
  }
});

/* ── home: dashboard ── */
function fmtTok(n){
  if(n>=1e6)return (n/1e6).toFixed(1)+'M';
  if(n>=1e3)return (n/1e3).toFixed(1)+'k';
  return String(n);
}
/* ── the dashboard ──
   Instrument row first, because the four questions it answers ("how much plan
   have I burned / how hard am I working / is my tooling up / is anything
   running") are the ones you open this page to ask. Then what to resume and the
   shape of the workspace, then the trend, then the browse list.

   Every gauge here is fed from the same two fetches the page already made; the
   instruments never issue a request of their own. */
function drawHome(){
  $('#ttl').textContent='Dashboard';$('#tpath').textContent='';
  const inst=(kind,key,o)=>INST.html(kind,key,o);
  paintNow(`
  <div class="dash">
    <div class="dband d-b1"><span>Spend</span></div>
    <section class="card icard d-i1 spot lift">
      <div class="ihd">${ic('bolt')}<span>spend today</span><span class="sp"></span>
        <span id="iQuotaN" class="itag"></span></div>
      ${inst('ring','quota',{fmt:'tok',sub:'tokens today',label:'by account'})}
      <div class="ileg" id="iQuotaLeg"></div>
      <div class="ifoot" id="iQuotaFoot">—</div></section>
    <section class="card icard d-i2 spot lift">
      <div class="ihd">${ic('chart')}<span>burn rate</span></div>
      ${inst('dial','burn',{fmt:'tok',unit:'tok/h',label:'rate vs your peak day'})}
      <div class="ifoot" id="iBurnFoot">—</div></section>
    <section class="card icard d-i3 spot lift">
      <div class="ihd">${ic('plug')}<span>tooling</span></div>
      ${inst('ring','mcp',{fmt:'ratio',unit:'/–',sub:'wired up',label:'mcp · hooks · statusline'})}
      <div class="ifoot" id="iMcpFoot">—</div></section>
    <section class="card icard d-i4 spot lift act" onclick="openActivity()"
      title="Open the activity log" role="button" tabindex="0">
      <div class="ihd">${ic('history')}<span>activity</span><span class="sp"></span>
        <span class="itag" id="iJobsN"></span></div>
      ${inst('eq','jobs',{fmt:'int',sub:'live now'})}
      <div class="joblist" id="dashJobs"></div>
      <div class="ifoot" id="iJobsFoot">—</div></section>

    <section class="card d-acct"><div class="lbl">Plan usage by account
      <span id="dashAcctN" style="color:var(--dim2)"></span></div>
      <div class="acct-rail" id="dashAcct"></div></section>
    <section class="card d-chart spot"><div class="lbl">Token trend</div>
      <div class="kpi" id="dashKpi"></div>
      <div class="tchart" id="dashChart">${MO.skel(2,40)}</div></section>

    <div class="dband d-b2"><span>Work</span></div>
    <section class="card d-continue spot lift" id="dashContinue">${continueTileHtml(ST.recent||[])}</section>
    <section class="card d-recent spot"><div class="lbl">Recent sessions</div>
      <div class="fld"><input id="hqSearch" placeholder="Search every session…"></div>
      <div id="hqRes"></div>
      <div class="dlist" id="dashRecent">${MO.skel(4)}</div></section>

    <div class="dband d-b3"><span>Workspace</span></div>
    <section class="card d-projects spot">
      <div class="lbl">Projects <span id="dashProjN" style="color:var(--dim2)"></span></div>
      ${inst('flow','flow',{noread:1,title:'Projects — size by tokens, colour by account, dashed links share an account'})}
      <div class="dlist" id="dashProjects">${MO.skel(4)}</div></section>
  </div>`);
  bindHomeSearch();
  mounted();
  startDashboard();
}
/* ── dashboard tiles: account usage, 7-day sparkline, jobs/MCP/recent ── */
let DASH_TIMER=null,DASH_ABORT=null;
function startDashboard(){
  stopDashboard();
  refreshDashboard();
  // /api/dashboard is server-cached for _DASH_TTL=10s — polling faster only
  // burns round-trips on a payload that provably cannot have changed
  DASH_TIMER=setInterval(refreshDashboard,10000);
}
function stopDashboard(){
  if(DASH_TIMER){clearInterval(DASH_TIMER);DASH_TIMER=null;}
  if(DASH_ABORT){DASH_ABORT.abort();DASH_ABORT=null;}
}
/* QtWebEngine keeps the page 'visible' while minimized — guard on blur too.
   MO.vis mirrors _VIS because motion.js is concatenated ahead of this file and
   cannot reach forward into a `let` that has not initialised yet. */
function setVis(v){
  /* Cancel any pending debounce FIRST. `visibilitychange` calls this directly,
     so a blur that had armed the 150ms timer, followed by the tab becoming
     visible inside that window, left the timer to fire setVis(false) on a
     focused page — animations paused and the GL surface down, with nothing left
     to clear it until the next focus event. */
  clearTimeout(_VIS_T);_VIS_T=null;
  _VIS=v;MO.vis=v;if(v)MO.kick();else MO.stop();
  /* `win-blur` on <html> is what actually stops the PAINTING — see the last
     block of app.css. It is deliberately not STAGE's own `stage-blur`, which
     never appears when the stage is off or its context died, while the CSS
     animations and a world's overlay keep looping regardless. */
  document.documentElement.classList.toggle('win-blur',!v);
  // and take the GL surface down while we are not drawing to it — see STAGE.blur
  if(window.STAGE)STAGE.blur(!v);}
/* A blur/focus pair costs a full background swap plus a dashboard teardown and
   an immediate re-fetch. Qt fires those pairs on interactions that are not
   really a focus change (opening a native menu, a child window taking focus for
   a frame), and the round trip is visible. So going AWAY waits; coming back is
   instant, because a delay there is the thing you would actually notice. */
let _VIS_T=null;
/* The dashboard teardown/re-fetch is HALF the cost the comment above is about,
   and it was left undebounced — stopDashboard() aborts the in-flight request
   and startDashboard() re-fetches immediately, so every spurious Qt pair still
   paid the round trip the debounce existed to remove. Both halves move
   together now. */
function setVisSoon(v){
  clearTimeout(_VIS_T);_VIS_T=null;
  if(v){setVis(true);if(PAGE_==='home')startDashboard();return;}
  _VIS_T=setTimeout(()=>{_VIS_T=null;setVis(false);stopDashboard();},150);
}
document.addEventListener('visibilitychange',()=>{setVis(!document.hidden);
  if(_VIS&&PAGE_==='home')refreshDashboard();});
window.addEventListener('blur',()=>setVisSoon(false));
window.addEventListener('focus',()=>setVisSoon(true));
function setV(el,v){if(!el)return;if(el.__v!==v){el.__v=v;el.innerHTML=v;}}
/* ── daily token chart: stacked columns by account, inline SVG, no deps ── */
let CHART_DAYS=14;
function setChartDays(n){CHART_DAYS=n;refreshDashboard();}
function niceMax(v){                          // round the top reference line up
  if(v<=0)return 0;
  const p=Math.pow(10,Math.floor(Math.log10(v)));
  return Math.ceil(v/p*2)/2*p;
}
function tokenChart(days,accounts){
  const rows=days.slice(-CHART_DAYS);
  const names=accounts.slice(0,6).map(a=>a.account);
  const W=720,H=176,PL=46,PB=20,PT=16;        // viewBox units; CSS scales width
  const max=niceMax(Math.max(...rows.map(r=>r.tokens||0),0));
  const y=v=>max?H-PB-(H-PB-PT)*v/max:H-PB;
  const slot=(W-PL)/Math.max(rows.length,1),bw=Math.min(30,slot-4);
  const step=rows.length<=8?1:rows.length<=16?2:3;
  const peak=rows.reduce((b,r)=>(r.tokens||0)>(b.tokens||0)?r:b,rows[0]||{});
  const bars=rows.map((r,i)=>{
    const x=PL+slot*i+(slot-bw)/2;
    let acc=0,segs='';
    const stack=names.filter(n=>((r.accounts||{})[n]||0)>0);
    names.forEach((n,ai)=>{
      const v=(r.accounts||{})[n]||0;if(!v)return;
      const top=y(acc+v),bot=y(acc),h=Math.max(1,bot-top-2);   // 2px surface gap
      acc+=v;
      // only the topmost segment gets a full pill cap; rounding every seam would
      // read as gaps between segments rather than one column
      const cap=n===stack[stack.length-1]?Math.min(bw/2,h/2):2;
      segs+=`<rect class="seg" x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}"
        height="${h.toFixed(1)}" rx="${cap.toFixed(1)}" fill="${acctColor(n)}"><title>${esc(r.date)} · ${esc(n)}: ${fmtTok(v)} tok</title></rect>`;
    });
    const other=(r.tokens||0)-acc;
    if(other>0){const top=y(acc+other),bot=y(acc);
      segs+=`<rect class="seg" x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}"
        height="${Math.max(1,bot-top-2).toFixed(1)}" rx="${Math.min(bw/2,3).toFixed(1)}" fill="var(--dim2)"><title>${esc(r.date)} · other: ${fmtTok(other)} tok</title></rect>`;}
    // omni tokens cut across accounts, so they can't be another stack segment —
    // hatch the free share up from the baseline over whatever it was spent on
    const om=Math.min(r.omni_tokens||0,r.tokens||0);
    if(om>0){const top=y(om),hh=Math.max(1,y(0)-top);
      const box=`x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${hh.toFixed(1)}" rx="2"`;
      segs+=`<rect class="omnibg" ${box}/><rect class="omni" ${box}><title>${esc(r.date)} · omni (free): ${fmtTok(om)} of ${fmtTok(r.tokens||0)} tok</title></rect>`;}
    const today=i===rows.length-1;
    const tick=(i%step===0||today||i===0)
      ?`<text class="${today?'now':''}" x="${(x+bw/2).toFixed(1)}" y="${H-6}" text-anchor="middle">${today?'today':esc(r.date.slice(5))}</text>`:'';
    const lab=(r===peak&&r.tokens)
      ?`<text class="big" x="${(x+bw/2).toFixed(1)}" y="${(y(r.tokens)-5).toFixed(1)}" text-anchor="middle">${fmtTok(r.tokens)}</text>`:'';
    return segs+tick+lab;
  }).join('');
  const anyOmni=rows.some(r=>(r.omni_tokens||0)>0);
  const legend=names.map(n=>`<span><i class="dot" style="background:${acctColor(n)}"></i>${esc(n)}</span>`).join('')
    +(anyOmni?`<span title="Ran on OmniRoute — free tier, not billed"><i class="dot omnikey"></i>omni · free</span>`:'')
    +`<span class="sp"></span>`
    +[7,14,30].map(n=>`<span class="chip${CHART_DAYS===n?' on':''}" onclick="setChartDays(${n})">${n}d</span>`).join('');
  return `<div class="lg">${legend}</div>
    <svg class="tcsvg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Tokens per day, stacked by account, free-tier omni share hatched">
      <defs><pattern id="omnihatch" width="5" height="5" patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="5"/></pattern></defs>
      <line class="gl" x1="${PL}" y1="${PT}" x2="${W}" y2="${PT}"/>
      <text x="${PL-6}" y="${PT+3}" text-anchor="end">${max?fmtTok(max):''}</text>
      <line class="gl" x1="${PL}" y1="${H-PB}" x2="${W}" y2="${H-PB}"/>
      <text x="${PL-6}" y="${H-PB+3}" text-anchor="end">0</text>
      ${bars}
      ${max?'':`<text class="none" x="${W/2}" y="${(H-PB+PT)/2}" text-anchor="middle">No usage in this range yet</text>`}
    </svg>`;
}
async function refreshDashboard(){
  if(document.hidden||!_VIS)return;       // hidden (incl. Qt minimized via blur)
  if(DASH_ABORT)DASH_ABORT.abort();
  const ac=DASH_ABORT=new AbortController();
  let d=null,plan=null;
  try{[d,plan]=await Promise.all([api('/api/dashboard',{signal:ac.signal}),
                                 api('/api/usage/plan',{signal:ac.signal})]);}
  catch(e){if(e.name==='AbortError')return;d=null;}
  // The poll outlives the page. Same lesson as NAV_ID and paint(), in a timer
  // rather than a renderer: everything below writes into elements that only
  // exist on home, and navigating away while the fetch was in flight left it
  // dereferencing null. It surfaced as an unrelated page getting slower.
  if(!$('#dashProjects'))return;
  if(d){
    const bd=d.breakdown||{days:[],accounts:[],projects:[],totals:{}};
    setV($('#dashChart'),tokenChart(bd.days||[],bd.accounts||[]));
    setV($('#dashKpi'),kpiHtml(d,bd));
    setV($('#dashProjN'),`· ${(bd.projects||[]).length}`);
    // both lists reconcile by key: a session finishing must not re-create the
    // nineteen rows that did not change
    MO.patch($('#dashProjects'),(bd.projects||[]),p=>p.enc,projectRowHtml,'hrow lift');
    if(!$('#dashProjects').__bound){$('#dashProjects').__bound=1;
      $('#dashProjects').addEventListener('click',ev=>{
        const row=ev.target.closest('.hrow');if(row)openProjectByEnc(row.__mokey);});}
    const spark={};
    for(const pr of (bd.projects||[]))if(pr.sparkline)spark[pr.enc]=pr.sparkline;
    if(JSON.stringify(spark)!==JSON.stringify(PROJ_SPARK)){PROJ_SPARK=spark;drawProjects();}
    MO.patch($('#dashRecent'),(d.recent||[]),r=>r.sid,recentRowHtml,'hrow');
    if(!$('#dashRecent').__bound){$('#dashRecent').__bound=1;
      $('#dashRecent').addEventListener('click',ev=>{
        const b=ev.target.closest('button[data-sid]');
        if(b)dashResumeSid(b.dataset.sid);});}
    renderHdrChips(d);
    // the continue tile used to render once from the boot payload and never
    // refresh, so it could disagree with the recent list about the same session.
    // /api/dashboard names the title 'title'; ST.recent (and homeResume) use 'name'.
    ST.recent=(d.recent||[]).map(r=>({project:r.project,path:r.path,encoded:r.encoded,
      sid:r.sid,name:r.title,age:r.age,cfgdir:r.cfgdir}));
    const tune=$('#hometune');
    if(!tune||tune.style.display==='none')   // don't yank the panel out from under an open tune
      setV($('#dashContinue'),continueTileHtml(ST.recent));
    DASH_RECENT=d.recent||[];
  }
  if(plan){
    setV($('#dashAcct'),(plan.accounts||[]).map(acctCard).join('')
      ||'<div style="color:var(--dim2)">no accounts configured</div>');
    // the count that the ring's arcs cannot carry: each of these is its OWN
    // quota, so this rail is where "how close is each one" actually lives
    const na=(plan.accounts||[]).length;
    setV($('#dashAcctN'),na?`· ${na}`:'');
  }
  else if(!d)setV($('#dashAcct'),'<div style="color:var(--dim2)">offline</div>');
  feedDashboard(d,plan);
  mounted();
}
/* ── activity drawer ──────────────────────────────────────────────────────
   The card was a dead end: a live count and a 24h equalizer, and clicking it
   did nothing, while the payload behind it already carried every job with its
   status, timings and last message. Three groups, because those are the three
   states a piece of work can be in from here.

   It renders from DASH_ACT — the payload the 10s poll already fetched — and
   NEVER fetches on its own, so opening it costs a repaint and nothing else.
   The poll keeps running while it is open, so it stays live. */
let DASH_ACT=null;
function openActivity(){
  drawActivity();
  $('#actovl').classList.add('show');
}
function closeActivity(){$('#actovl').classList.remove('show');}
function actAgo(ts){
  if(!ts)return '';
  const s=Math.max(0,Math.round(Date.now()/1000-ts));
  return s<60?s+'s ago':s<3600?Math.round(s/60)+'m ago'
    :s<86400?Math.round(s/3600)+'h ago':Math.round(s/86400)+'d ago';
}
function actDur(s){
  s=Math.max(0,s|0);
  return s<60?s+'s':s<3600?Math.round(s/60)+'m':(s/3600).toFixed(1)+'h';
}
function drawActivity(){
  const el=$('#actBody');if(!el)return;
  const d=DASH_ACT||{};
  const jobs=d.jobs||[],live=d.live||{total:0,by_account:{}},recent=d.recent||[];
  const running=jobs.filter(j=>j.status==='running'||j.status==='awaiting');
  const done=jobs.filter(j=>j.status!=='running'&&j.status!=='awaiting');
  const tone={done:'ok',error:'err',cancelled:'',awaiting:'warn',running:''};
  const jobRow=j=>`<div class="arow3">
    <span class="tag ${tone[j.status]||''}">${esc(j.status)}</span>
    <div><b>${esc(j.kind||'job')}</b>
      ${j.last?`<div class="asub">${esc(j.last)}</div>`:''}
      ${j.error?`<div class="asub err">${esc(j.error)}</div>`:''}</div>
    <span class="aage">${actDur(j.elapsed)}${j.ended?' · '+actAgo(j.ended):''}</span>
    ${j.status==='awaiting'?`<button class="btn sm pri" onclick="closeActivity();jobOpen(${hesc(j.id)})">Open</button>`:''}
  </div>`;
  // live Claude Code sessions are the OTHER kind of ongoing work, and the one
  // that is usually actually happening — claudectl's own jobs are rare
  const liveRows=Object.entries(live.by_account||{}).sort((a,b)=>b[1]-a[1])
    .map(([n,c])=>`<div class="arow3">
      <span class="dot" style="background:${acctColor(n)}"></span>
      <div><b>${esc(n)}</b><div class="asub">${c} live session${c>1?'s':''}</div></div>
      <span class="aage">within ${Math.round((live.window||900)/60)}m</span></div>`).join('');
  // a session whose transcript stopped moving before the live window is one
  // that ENDED — the payload has no separate "finished sessions" feed, and
  // deriving it from mtime is exact rather than a guess
  const now=Date.now()/1000, win=live.window||900;
  const ended=recent.filter(r=>r.mtime&&now-r.mtime>win).slice(0,8);
  const sessRow=r=>`<div class="arow3">
    <span class="dot" style="background:${acctColor(r.account||'default')}"></span>
    <div><b>${esc(r.project)}</b><div class="asub">${esc(r.title||r.sid)} · ${r.msgs} msgs</div></div>
    <span class="aage">${esc(r.age)} ago</span>
    <button class="btn sm" onclick="closeActivity();dashResumeSid(${hesc(r.sid)})">Resume</button></div>`;
  const sect=(title,n,body)=>`<div class="sect"><div class="secth"><h4>${title}</h4>
      <span class="secttag">${n}</span></div>${body}</div>`;
  const nRun=running.length+Object.keys(live.by_account||{}).length;
  el.innerHTML=
    sect('Running now',nRun||'',
      (running.map(jobRow).join('')+liveRows)
      ||'<div class="empty">No live sessions and no running jobs.</div>')
    +sect('Finished',done.length||'',
      done.length?done.map(jobRow).join('')
        :'<div class="empty">No claudectl job has finished this run.</div>')
    +sect('Recently ended sessions',ended.length||'',
      ended.length?ended.map(sessRow).join('')
        :'<div class="empty">Nothing has ended recently.</div>');
}

/* ── the instrument feed ───────────────────────────────────────────────────
   One place where the two dashboard fetches become gauge values, so the mapping
   from "what the API said" to "what the needle shows" is auditable in one read
   instead of scattered across six renderers. Nothing here fetches.

   Every gauge is normalised against the user's OWN history, not an absolute
   scale: a burn dial pinned at 100% because someone else's workspace is busier
   would be worse than no dial. */
function feedDashboard(d,plan){
  const accs=((plan||{}).accounts)||[];
  const wins=accs.flatMap(a=>(a.windows||[]).map(w=>(w.pct||0)/100));
  const peakWin=wins.length?Math.max(...wins):0;
  const hot=accs.find(a=>(a.windows||[]).some(w=>(w.pct||0)>=80));

  /* ── Spend today ──────────────────────────────────────────────────────────
     The ring plots TOKENS, split one arc per account, because tokens are the
     only cross-account aggregate that is actually additive. Each account's
     quota `pct` is a share of ITS OWN window, so summing or averaging those
     five numbers produces something that looks like a total and means nothing.
     The quota question is answered next to it as a COUNT of accounts with
     headroom, plus the one that will run out first.

     The ring's fill is still driven by the worst window — that is the thing
     with a real ceiling — while the arcs inside it carry the split. */
  const spendBy=Object.entries((d&&d.today&&d.today.by_account)||{})
    .filter(([,t])=>t>0).sort((a,b)=>b[1]-a[1]);
  const todayTok=spendBy.reduce((n,[,t])=>n+t,0)||((d&&d.today&&d.today.tokens)||0);
  INST.set('quota',{v:peakWin||(todayTok?0.06:0),
    tone:peakWin>=.8?'err':peakWin>=.6?'warn':null,
    segments:spendBy.map(([n,t])=>({v:t,color:resolveColor(acctColor(n))}))});
  setRead('quota',todayTok);
  // a legend, because an arc with no name is a colour
  setV($('#iQuotaLeg'),spendBy.slice(0,5).map(([n,t])=>
    `<span><i style="background:${acctColor(n)}"></i>${esc(n)} ${fmtTok(t)}</span>`).join(''));
  const roomy=accs.filter(a=>!(a.windows||[]).some(w=>(w.pct||0)>=50)).length;
  setV($('#iQuotaFoot'),accs.length
    ?(hot?`<b>${esc(hot.email||hot.account)}</b> runs out first — ${Math.round(peakWin*100)}% of its window`
        :`${roomy}/${accs.length} account${accs.length>1?'s':''} under 50%`)
    :(todayTok?'no plan accounts configured':'nothing spent today'));
  setV($('#iQuotaN'),(d&&d.today&&d.today.cost)?'$'+(d.today.cost).toFixed(2):'');

  if(!d)return;
  const bd=d.breakdown||{};
  const days=(bd.days||[]).slice(-30);
  const today=(d.today||{}).tokens||0;
  // hours elapsed today, floored at 1 so the first minute after midnight does
  // not report an hourly rate extrapolated from 40 seconds of work
  const hrs=Math.max(1,(Date.now()-new Date().setHours(0,0,0,0))/3.6e6);
  const burn=Math.round(today/hrs);
  const peakDay=Math.max(...days.map(r=>r.tokens||0),1);
  const peakHr=peakDay/12||1;                 // a heavy day ≈ 12 working hours
  INST.set('burn',{v:Math.min(1,burn/peakHr)});
  setRead('burn',burn);
  /* A rate wants a sentence that ends somewhere. Three additions, all from
     numbers already in the payload: where today lands if it keeps up, how that
     compares to the last seven days, and how much of it was free-tier. */
  const proj=Math.round(burn*24);
  const wk=(d.week||[]).filter(r=>r.tokens>0);
  const wkAvg=wk.length?wk.reduce((n,r)=>n+r.tokens,0)/wk.length:0;
  const ratio=wkAvg?today/wkAvg:0;
  const omniT=(d.today||{}).omni_tokens||0;
  // .ifoot is one clipped line — the long form lives in the tooltip
  setV($('#iBurnFoot'),
    `<span title="${esc(`${fmtTok(today)} so far today, on track for ${fmtTok(proj)} by midnight`
      + (ratio ? `. That is ${ratio.toFixed(1)}x your 7-day average.` : '')
      + (omniT ? ` ${fmtTok(omniT)} of it ran on free-tier models.` : ''))}">`
    +`<b>${fmtTok(proj)}</b> by midnight`
    +(ratio?` · <b>${ratio.toFixed(1)}×</b> 7-day avg`:'')
    +(omniT?` · ${fmtTok(omniT)} free`:'')+'</span>');

  /* ── Tooling → is the workspace actually wired up ──────────────────────────
     MCP reachability alone answered a narrow question. The card now counts
     every wiring claudectl can verify from files: MCP servers up, the failover
     proxy, and per account whether hooks and a statusline are installed AND
     whether the statusline will actually be drawn — an installed statusline
     under the classic renderer is invisible, which from the settings file looks
     exactly like one that works. */
  const mcp=d.mcp||[],up=mcp.filter(m=>m.running).length;
  const fo=!!((d.failover||{}).running);
  const wir=d.wiring||{accounts:[],ok:0,total:0};
  /* The ring scores WIRING, not MCP reachability. An MCP server that is not
     currently connected is usually not a fault — most start on demand, and
     "1/10 up" is an ordinary healthy state — so counting them would pin this
     card at red forever and it would stop meaning anything. That is the same
     mistake the activity gauge already made once by measuring claudectl's
     idleness instead of the workspace's work. MCP stays in the footer as
     information; the ring counts things that are actually misconfigured. */
  const nodes=wir.total+1,liveN=wir.ok+(fo?1:0);   // +1: the failover proxy
  INST.set('mcp',{v:nodes?liveN/nodes:0,
    tone:liveN===nodes?'ok':liveN?'warn':'err'});
  // the ring counts the reachable servers and the unit carries the total, so
  // the readout says "1 /2" without needing a formatter that knows both
  setRead('mcp',liveN);
  setUnit('mcp','/'+nodes);
  // Name what is WRONG — a count of healthy things tells you nothing to do.
  // Kept short because .ifoot is one clipped line: "statusline hidden" is the
  // actionable half, and the reason belongs in a tooltip.
  const broke=[];
  (wir.accounts||[]).forEach(a=>{
    if(a.statusline_hidden)broke.push([`${a.account}: statusline hidden`,
      'installed but the classic renderer never draws it — set tui: fullscreen']);
    else if(!a.statusline)broke.push([`${a.account}: no statusline`,'']);
    if(!a.hooks)broke.push([`${a.account}: no hooks`,'']);
  });
  if(!fo)broke.push(['failover off','no model fallback proxy running']);
  setV($('#iMcpFoot'),broke.length
    ?`<span style="color:var(--warn)" title="${esc(broke.map(b=>b[0]+(b[1]?' — '+b[1]:'')).join('\n'))}">`
      +`${esc(broke[0][0])}</span>`
      +(broke.length>1?` <span style="color:var(--dim2)">+${broke.length-1} more</span>`:'')
    :`mcp <b>${up}/${mcp.length}</b> up · ${wir.total} account${wir.total===1?'':'s'} wired`);

  /* ── Activity ──────────────────────────────────────────────────────────
     Reads LIVE CLAUDE CODE SESSIONS across every account, not claudectl's own
     background jobs. It used to read the latter, which are almost never
     running, so the card sat at "0 RUNNING" and flat while the same dashboard
     reported hundreds of millions of tokens that day — it was reporting the
     tool's idleness, not the workspace's.

     Sessions are the right feed for a *liveness* gauge because they are
     genuinely intermittent: non-zero while you work, zero when you stop. That
     is the same property that made `beats` the right input and throughput the
     wrong one — throughput stays non-zero all day after one token is spent, so
     the equalizer never stopped moving. Tokens live on the burn dial next door.

     Bars are the last 24 HOURS. They used to be `days.slice(-24)` — the last 24
     *days* out of a 14-day window, i.e. a sparse trend chart in a card called
     Activity. */
  const live=d.live||{total:0,by_account:{}};
  const nlive=live.total||0;
  const allJobs=d.jobs||[];
  const runJobs=allJobs.filter(j=>j.status==='running'||j.status==='awaiting');
  const jobs=runJobs.length;
  DASH_ACT=d;                      // the drawer renders from the poll's payload
  // …and re-renders on each poll while it is open, so it stays live without
  // owning a timer of its own
  if($('#actovl')&&$('#actovl').classList.contains('show'))drawActivity();
  INST.set('jobs',{v:Math.min(1,nlive/3),beats:nlive,series:d.hours||[]});
  // Feed the background: live sessions dominate, claudectl's own jobs also
  // count (a memory build IS the workspace working), burn is a floor.
  stageEnergy(Math.max(nlive,jobs),burn/peakHr);
  setRead('jobs',nlive);
  setV($('#dashJobs'),jobsHtml(d));
  const sess=(d.today||{}).sessions||0;
  // name the accounts — "for all accounts" is only meaningful if you can see
  // WHICH one is busy when you have three
  const byAcct=Object.entries(live.by_account||{})
    .sort((a,b)=>b[1]-a[1])
    .map(([n,c])=>`${esc(n)}${c>1?` <b>${c}</b>`:''}`).join(' · ');
  // an awaiting job is PARKED at an approval gate — the single most important
  // thing on this page, because nothing moves until you answer it
  const gated=allJobs.filter(j=>j.status==='awaiting').length;
  setV($('#iJobsN'),gated?`${gated} needs you`:(allJobs.length>jobs
    ?`${allJobs.length-jobs} done`:''));
  $('#iJobsN').className='itag'+(gated?' hot':'');
  setV($('#iJobsFoot'),gated
    ?`<b style="color:var(--warn)">${gated} waiting for approval</b> — click to open`
    :nlive
      ?`<b>${nlive}</b> live${byAcct?' · '+byAcct:''} · ${sess} today`
      :(jobs?`<b>${jobs}</b> claudectl job${jobs>1?'s':''} · ${sess} today`
            :`idle · <b>${sess}</b> session${sess===1?'':'s'} today`));

  // flow map: one node per project, dashed links where two share a non-default
  // account. Positions are solved from the index, so the same set always lands
  // the same way and the map is stable to look at across refreshes.
  const projects=(bd.projects||[]).slice(0,24);
  const now=Date.now()/1000;
  const pmax=Math.max(...projects.map(p=>p.tokens||0),1);
  const links=[];
  for(let i=0;i<projects.length;i++)for(let j=i+1;j<projects.length;j++){
    const a=projects[i].accounts||[],b=projects[j].accounts||[];
    const shared=a.filter(x=>x!=='default'&&b.includes(x)).length;
    if(shared)links.push([i,j,shared]);
  }
  INST.set('flow',{
    items:projects.map(p=>({v:(p.tokens||0)/pmax,
      heat:Math.max(.2,1-(now-(p.mtime||now))/(3600*24*7)),
      col:resolveColor(acctColor((p.accounts||[]).find(x=>x!=='default')||'default')),
      label:p.name||p.enc||''})),
    links:links.sort((a,b)=>b[2]-a[2]).slice(0,48)});
}
/* The readout inside an instrument — tweened, never assigned. Deliberately the
   imperative counterpart to the declarative [data-num] sweep: a gauge's value
   arrives from a feed, not from markup, so re-rendering the page must not reset
   the number to whatever the template happened to say. */
function setRead(key,v){
  const el=document.querySelector(`.iwrap[data-k="${key}"] .iread b`);
  if(!el)return;
  MO.count(el,v,MO_FMT[el.dataset.fmt]||MO_FMT.int);
}
function setUnit(key,txt){
  const el=document.querySelector(`.iwrap[data-k="${key}"] .iread i`);
  if(el)el.textContent=txt;
}
function projectRowHtml(p){
  return `<div class="info"><b>${esc(p.name)}</b>
      ${(p.accounts||[]).map(a=>`<span class="dot" title="${esc(a)}" style="width:7px;height:7px;background:${acctColor(a)}"></span>`).join('')}
      ${p.omni?OMNI_TAG:''}</div>
    <span class="num">${fmtTok(p.tokens||0)} · $${(p.cost||0).toFixed(2)}</span>
    <span class="num">${esc(p.age||'')}</span>`;
}
function recentRowHtml(r){
  return `<div class="info"><b>${esc(r.project)}</b>
      <span style="color:var(--dim)">${esc(r.title)} · ${r.msgs} msgs</span></div>
    ${r.account&&r.account!=='default'?`<span class="tag acct" style="color:${acctColor(r.account)}">${esc(r.account)}</span>`:''}
    ${r.omni?OMNI_TAG:''}
    <span class="num">${esc(r.age||'')}</span>
    <button class="btn sm" data-sid="${esc(r.sid)}">Resume</button>`;
}
const OMNI_TAG='<span class="tag acct" style="color:var(--violet)" title="Ran on OmniRoute (free-tier model)">omni</span>';
let DASH_RECENT=[];
/* keyed by session id, not row index: the recent list reorders between polls, so
   an index captured at render time could resume a different session than the one
   under the cursor */
function dashResumeSid(sid){
  const r=DASH_RECENT.find(x=>x.sid===sid);if(!r)return;
  const [model,effort]=defaultModelEffort();
  doQuickLaunch({path:r.path,enc:r.encoded,choice:'resume:'+r.sid,cfgdir:r.cfgdir},model,effort);
}
/* data-num + data-fmt instead of a baked string: MO.counts() picks these up and
   tweens them, so a poll that moves the 7-day total is visible as movement */
function kpiHtml(d,bd){
  const t=d.today||{},tot=bd.totals||{};
  const wk=(bd.days||[]).slice(-7);
  const wtok=wk.reduce((a,r)=>a+(r.tokens||0),0),wcost=wk.reduce((a,r)=>a+(r.cost||0),0);
  const stat=(v,f,l,title)=>`<div class="k" title="${esc(title||l)}">`
    +`<div class="kv2" data-num="${v}" data-fmt="${f}">–</div>`
    +`<div class="kl">${esc(l)}</div></div>`;
  const age=d.generated_at?Math.max(0,Math.round(Date.now()/1000-d.generated_at)):null;
  return stat(t.tokens||0,'tok','today tok')
    +stat(wtok,'tok','7d tok')
    +stat(wcost,'usd','7d est. cost','API-rate estimate, cache-aware; OmniRoute models count as free')
    +stat(t.sessions||0,'int','sessions today')
    +stat(tot.omni_tokens||0,'tok','omni tok',`free-tier tokens over ${d.days||30}d — about $${(tot.omni_saved||0).toFixed(2)} of Opus-rate work`)
    +`<span class="sp"></span>`
    +`<span class="fresh">${age==null?'':'updated '+age+'s ago'}</span>`;
}
/* running jobs, each with a live pip. This is the "is anything happening"
   answer in words; the equalizer above it is the same answer at a glance. */
function jobsHtml(d){
  const jobs=d.jobs||[];
  if(!jobs.length)return '';
  return jobs.slice(0,4).map(j=>`<div class="jrow"><span class="pulse"></span>`
    +`<span class="jk">${esc(j.kind)}</span>`
    +`<span class="js">${esc(j.status)}</span>`
    +`<span class="je">${j.elapsed}s</span></div>`).join('')
    +(jobs.length>4?`<div class="jrow more">+${jobs.length-4} more</div>`:'');
}
function continueTileHtml(rec){
  if(!rec.length)return `<b>Welcome</b><p style="color:var(--dim);margin-top:8px">
    Open a project on the left to get started.</p>`;
  const r=rec[0];
  return `<div class="lbl">Continue where you left off</div>
    <div class="hn">${esc(r.project)}</div>
    <div class="hs">${esc(r.name)}</div>
    <div style="color:var(--dim);font-size:12px;margin-top:2px">${r.age?esc(r.age)+' ago':''}</div>
    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn pri sm" onclick="homeResume(0)">${ic('play')} Resume</button>
      <button class="btn sm" onclick="toggleHomeTune()" title="Adjust power before resuming">${ic('settings')}</button>
    </div>
    <div class="rowtune" id="hometune" style="display:none;margin-top:12px">
      <input type="range" class="rtfrontier" id="hqFrontier" min="0" step="1">
      <div class="frontends"><span>Cheap &amp; fast</span><span>Max power</span></div>
      <div class="frontread" id="hqFrontRead"></div>
      <button class="btn sm pri" onclick="homeResumeTuned()" style="margin-top:8px">Resume with these settings →</button>
    </div>`;
}
function homeResume(i){
  const r=ST.recent[i];
  const [model,effort]=defaultModelEffort();
  doQuickLaunch({path:r.path,enc:r.encoded,choice:'resume:'+r.sid,cfgdir:r.cfgdir},model,effort);
}
function toggleHomeTune(){
  const el=$('#hometune');if(!el)return;
  const show=el.style.display==='none';
  el.style.display=show?'':'none';
  if(!show)return;
  const sl=$('#hqFrontier'),rows=ST.options.frontier||[];
  sl.max=Math.max(rows.length-1,0);
  const [dm,de]=defaultModelEffort();
  const fi=rows.findIndex(row=>row[0]===dm&&row[1]===de);
  sl.value=fi>=0?fi:Math.min(2,rows.length-1);
  sl.oninput=updateHomeTuneReadout;
  updateHomeTuneReadout();
}
function updateHomeTuneReadout(){
  const sl=$('#hqFrontier'),rows=ST.options.frontier||[];
  const [_mid,eff,lbl,cost,swe,note]=rows[+sl.value]||rows[rows.length-1]||['','','','','',''];
  $('#hqFrontRead').innerHTML=`<b>${esc(lbl)} · ${esc(eff)}</b>`
    +`<div class="fsub">${esc(swe)} SWE · ${esc(cost)}</div>`
    +`<div class="fsub" style="font-family:inherit">${esc(note)}</div>`;
}
function homeResumeTuned(){
  const r=ST.recent[0],sl=$('#hqFrontier'),rows=ST.options.frontier||[];
  const row=rows[+sl.value]||rows[rows.length-1]||['',''];
  doQuickLaunch({path:r.path,enc:r.encoded,choice:'resume:'+r.sid,cfgdir:r.cfgdir},row[0],row[1]);
  toggleHomeTune();
}
/* one account card in the home rail — a degraded account is NAMED, never '—' */
function acctCard(a){
  const wins=a.windows||[],ok=a.status==='ok';
  const bad=a.status==='expired'||a.status==='no_creds';
  const cls='acct-card'+(bad?' bad':'')+(!ok&&wins.length?' stale':'');
  const meter=(w,sub)=>`<div class="arow${sub?' sub':''}">
    <span class="al" title="${esc(w.label)}">${esc(w.label)}</span>
    <span class="bar${w.pct>=80?' hot':''}"><i style="width:${Math.min(100,w.pct)}%"></i></span>
    <span class="ap"${w.pct>=80?' style="color:var(--err)"':''}>${Math.round(w.pct)}%</span>
    ${w.resets?`<span class="rs">resets ${esc(w.resets)}</span>`:''}</div>`;
  const known=['session','weekly'];
  const bars=known.filter(k=>wins.some(w=>w.label===k)).map(k=>meter(wins.find(w=>w.label===k))).join('')
    +wins.filter(w=>!known.includes(w.label)).map(w=>meter(w,true)).join('');
  const skel=`<div class="arow"><span class="al">session</span><span class="bar shimmer"></span><span class="ap"></span></div>
    <div class="arow"><span class="al">weekly</span><span class="bar shimmer"></span><span class="ap"></span></div>`;
  let note='';
  if(bad)note=`<div class="st err">${esc(a.status_text||'not logged in')}
    <span class="hlink" onclick="acctReconnect(${hesc(a.account)},${hesc(a.dir||'')})">Reconnect ›</span></div>`;
  else if(a.status==='rate_limited')note=`<div class="st warn">rate-limited${a.retry_in?' · retry in '+a.retry_in+'s':''}</div>`;
  else if(a.status==='error')note=`<div class="st warn">${esc(a.status_text||'usage unavailable')}</div>`;
  else if(a.stale_secs!=null&&a.stale_secs>600)note=`<div class="st dim">as of ${Math.round(a.stale_secs/60)}m ago</div>`;
  return `<div class="${cls}">
    <div class="ah"><span class="dot" style="background:${acctColor(a.account)}"></span>
      <b title="${esc(a.email||a.account)}">${esc(a.email||a.account)}</b>
      ${a.plan||a.tier?`<span class="plan">${esc(a.plan||a.tier)}</span>`:''}</div>
    ${bars||(a.status==='pending'?skel:'')}
    ${a.spend?`<div class="st dim">credits ${esc(a.spend.currency)} ${a.spend.used.toFixed(2)} · ${Math.round(a.spend.pct)}%</div>`:''}
    ${note}</div>`;
}
async function acctReconnect(name,dir){
  const r=await post('/api/accounts/terminal',{name:name,dir:dir});
  toast(r.ok?'Terminal opened — run /login there':'Failed: '+(r.error||''),r.ok?'ok':'err');
}
/* Canvas gradients cannot parse var(), and acctColor() hands back var(--ok) /
   var(--dim) for the default and overflow accounts — so anything feeding a
   canvas resolves the computed value first. */
function cssVar(n){return getComputedStyle(document.documentElement).getPropertyValue(n).trim();}
function resolveColor(c){
  const m=/^var\((--[\w-]+)\)$/.exec(c||'');
  return m?(cssVar(m[1])||'#7dcfff'):c;
}
/* The dashboard "activity constellation" that used to live here — a second,
   independent rAF chain drawing glow blobs on a golden-angle spiral — is now
   the "flow" instrument (instruments.js). Same deterministic layout, rendered
   as ring-outline nodes with dashed links so the structure is legible, and on
   the app's ONE shared scheduler instead of a loop of its own. */

function openProjectByEnc(enc){
  const p=(ST.projects||[]).find(x=>x.encoded===enc);
  if(p)openProject(p);else toast('Project not found','err');
}
/* ── home search: shares the SIDX cache with pgSearch() below ── */
let PENDING_SEARCH_Q='';
function bindHomeSearch(){
  const inp=$('#hqSearch');if(!inp)return;
  let t=null;
  inp.oninput=()=>{clearTimeout(t);t=setTimeout(drawHomeSearchResults,120);};
}
async function drawHomeSearchResults(){
  const q=($('#hqSearch').value||'').toLowerCase().trim();
  const res=$('#hqRes'),list=$('#dashRecent');if(!res)return;
  if(list)list.style.display=q?'none':'';       // search replaces the recent list
  if(!q){res.innerHTML='';return;}
  if(!SIDX){const d=await api('/api/search-index');SIDX=d.rows||[];}
  const m=SIDX.filter(r=>q.split(/\s+/).every(w=>r.haystack.includes(w)));
  window._hmatch=m;
  res.innerHTML=m.slice(0,5).map((r,i)=>`
    <div class="s" onclick="homeSearchResume(${i})">
      <div class="info">${esc(r.display)}</div>
      <span style="color:var(--dim);font-size:11px">${esc(r.project)} · ${esc(r.age)} ago</span>
    </div>`).join('')
    +(m.length?`<div class="hlink" style="margin-top:6px" onclick="goToFullSearch()">See all ${m.length} results ›</div>`
              :`<div style="color:var(--dim);font-size:12px;padding:6px 0">No matches</div>`);
}
function homeSearchResume(i){
  const r=window._hmatch[i];
  const [model,effort]=defaultModelEffort();
  doQuickLaunch({path:r.path,enc:r.enc,choice:'resume:'+r.sid,cfgdir:r.cfgdir},model,effort);
}
function goToFullSearch(){PENDING_SEARCH_Q=($('#hqSearch').value||'');go('searchp');}

/* ── project view + tabs ── */
/* [id, label, blurb] — the blurb rides along for the same reason the nav's does:
   the help page renders from this array instead of a retyped copy of it. */
const TABS=[
  ['sessions','Sessions','Every session in this project, across accounts — open, rename, archive, export.'],
  ['memory','Memory','Everything claudectl knows about this project and what knowing it costs — the graph, the rules, lessons, spend, and what the last cycle did.'],
  ['claudemd','CLAUDE.md','The instruction file block by block, what each block costs, the memory map, and every version claudectl replaced.'],
  ['review','Review','Run a code review over the working tree, staged changes or a branch.'],
  ['audit','Audit','What one turn costs across every surface at once — this project, your account, hooks and MCP — before you spend it.'],
  ['pusage','Usage','This project\'s token spend over time.'],
  ['planexec','Plan → Execute','Have one model write a plan, approve or edit it, then have another execute it.'],
  ['worktrees','Repos','Git repos, submodules and linked worktrees under this project.'],
  ['tools','Tools','Architecture, the interactive graph, Claude Code\'s own record of the project, and the loop file.']];
/* The project a project-scoped setting targets from a GLOBAL page.

   `go()` clears CUR on purpose — the header leaves project mode — so a "this
   project" card on the Skills or Output styles page was structurally
   unreachable: navigating to the page was itself what emptied it. That is why
   the per-project skills section read as pointless; it could never have shown
   anything. The last project you opened is the honest answer, and every surface
   that uses it says which project that is. */
let LASTPROJ=null;
function projCtx(){return CUR||LASTPROJ;}
async function openProject(p){CUR=p;LASTPROJ=p;PAGE_='project';TAB='sessions';REVIEW=null;
  render();
  // kick off the same background memory update the TUI does on open, then
  // watch the scan-lock so the badge shows live progress
  post('/api/memory/autoscan',C()).then(r=>{if(r.running)startMemBadge();else pollMemOnce();});
}

/* ── memory-updating badge (polls the scan-lock like the TUI) ── */
let _memTimer=null,_memSeen=false,_memGrace=0;
function setMemBadge(txt){const b=$('#membadge');
  if(txt==null){b.style.display='none';b.innerHTML='';}
  else{b.style.display='';b.innerHTML=`<span class="pulse"></span> memory updating${txt?' '+esc(txt):''}… <span style="color:var(--dim2)">safe to launch</span>`;}}
function stopMemBadge(){if(_memTimer){clearTimeout(_memTimer);_memTimer=null;}
  _memSeen=false;_memGrace=0;setMemBadge(null);}
async function pollMemOnce(){   // no worker spawned — show badge only if one is already live
  if(!CUR)return;
  try{const r=await api('/api/memory/progress?'+qs({path:CUR.path}));
    if(r.progress!=null)startMemBadge();}catch(e){}
}
function startMemBadge(){
  if(_memTimer)return;
  _memSeen=false;_memGrace=8;        // tolerate ~16s while the detached worker starts up
  setMemBadge('');                   // show immediately so the user sees activity
  const tick=async()=>{
    if(!CUR||PAGE_!=='project'){stopMemBadge();return;}
    let prog=null,last=null;
    try{const r=await api('/api/memory/progress?'+qs({path:CUR.path}));
      prog=r.progress;last=r.last;}catch(e){}
    if(prog!=null){_memSeen=true;setMemBadge(prog);_memTimer=setTimeout(tick,2000);}
    else if(!_memSeen&&_memGrace-->0){_memTimer=setTimeout(tick,2000);}  // worker not up yet
    else{ // finished (or never started) — refresh memory view if it's showing
      const wasRunning=_memSeen;stopMemBadge();
      if(wasRunning){
        /* The lock vanishing is NOT success: a crashed cycle clears it in its
           `finally` exactly like a completed one, so this toasted "Memory
           updated" over a failure. The server records how the run ended. */
        if(last&&last.ok===false)toast('Memory update failed: '+(last.error||'unknown'),'err');
        else if(last&&last.ok){
          const b=[];if(last.extracted)b.push(last.extracted+' module(s)');
          if(last.lessons)b.push(last.lessons+' lesson(s)');
          if(last.pending)b.push(last.pending+' still queued');
          toast(b.length?'Memory updated — '+b.join(', '):'Memory already current','ok');
        }else toast('Memory updated','ok');
        if(PAGE_==='project'&&TAB==='memory')drawMemory();}
    }
  };
  tick();
}
function drawProject(){
  NAV_ID++;
  $('#ttl').textContent=CUR.name;$('#tpath').textContent=CUR.path;
  $('#bHide').textContent=CUR.hidden?'Unhide':'Hide';
  $('#bHide').title=CUR.hidden?'Show this project in the list again'
                              :'Hide this project from the project list (nothing is deleted)';
  $('#tabs').innerHTML=TABS.map(([id,l])=>
    `<div class="tab${TAB===id?' sel':''}" onclick="TAB=${hesc(id)};drawProject()">${l}</div>`).join('')
    +`<div class="tab" onclick="window.open('/graph?${qs({path:CUR.path,enc:CUR.encoded,k:CK})}','_blank')">Graph ${ic('ext')}</div>`;
  ({sessions:drawSessions,memory:drawMemory,claudemd:drawClaudeMd,review:drawReview,
    audit:drawAudit,pusage:drawProjUsage,planexec:drawPlanExec,
    worktrees:drawWorktrees,tools:drawTools}[TAB])();
}

/* review tab — confidence-scored review of the working diff */
let REVIEW=null;
const SEVCLR={critical:'var(--sev-critical)',high:'var(--sev-high)',medium:'var(--sev-medium)',low:'var(--dim)'};
function drawReview(){
  const r=REVIEW;
  const fnd=(r&&r.findings)||[];
  const sev=['critical','high','medium','low'].map(k=>
    fnd.filter(f=>(f.severity||'').toLowerCase()===k).length);
  let out;
  if(!r){
    out=`<div style="color:var(--dim)">Review your uncommitted changes against this project's CLAUDE.md rules and learned lessons. Runs one Claude call; only findings at or above the confidence threshold are shown.</div>`;
  }else if(r.empty){
    out=`<div class="empty">No changes to review — the working diff is empty. Make edits (or stage them) first.</div>`;
  }else if(!(r.findings||[]).length){
    const filt=r.raw_count?` <span class="tag">${r.raw_count} lower-confidence note(s) below ${r.min}% filtered</span>`:'';
    out=`<div class="empty" style="color:var(--ok)">✓ No issues found above ${r.min}% confidence.${filt}</div>`;
  }else{
    out=`<div class="lbl" style="margin:0 0 10px">Found ${r.findings.length} issue(s)</div>`
      +r.findings.map(f=>{
      const c=SEVCLR[(f.severity||'').toLowerCase()]||'var(--dim)';
      return `<div class="card" style="border-left:3px solid ${c};margin-bottom:10px">
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
          <b style="color:${c};text-transform:uppercase;font-size:12px">${esc(f.severity||'?')}</b>
          <span class="tag">${esc(f.category||'')}</span>
          <span class="tag">${esc(String(f.confidence))}%</span>
          <code style="color:var(--cyan)">${esc(f.file||'?')}:${esc(String(f.line||'?'))}</code>
        </div>
        <div style="margin:6px 0 2px;font-weight:600">${esc(f.summary||'')}</div>
        <div style="color:var(--dim);font-size:13px">${esc(f.detail||'')}</div></div>`;
    }).join('');
  }
  paintNow(`<div class="card"><h3>Code review <span class="sp"></span>
      <button class="btn sm pri" onclick="runReview(false)">${ic('search')} Review working changes</button>
      <button class="btn sm" onclick="runReview(true)">Staged only</button></h3>
      <p style="color:var(--dim);font-size:12px;margin:0 0 10px">Inspired by the Claude Code code-review plugin — confidence-scored, high-threshold, CLAUDE.md-aware.</p>
      ${out}</div>`);
}
function runReview(staged){
  inlineJob('#jban','review',{...C(),staged},{label:'Reviewing changes',
    onDone:st=>{REVIEW=st.result||{findings:[]};},redraw:()=>drawReview()});
}

function projectSetup(){
  inlineJob('#jban','project_setup',Object.assign({},C(),{path:CUR.path}),
    {label:'Setting up '+CUR.name,onDone:st=>{
      const r=(st&&st.result)||{};
      toast(`Set up: CLAUDE.md + ${r.entities||0} memory entities`,'ok');
      if(CUR)CUR.set_up=true;},
     redraw:()=>drawSessions()});
}

/* sessions tab */
async function drawSessions(archived){
  const nav=paintNow(LOADING);
  /* No cfgdir on either branch: both endpoints walk every account server-side.
     Sending `CUR.primary_cfgdir` was the archived-view bug — it is the FIRST
     account that has this project (so `default` whenever `default` has it), and
     anything archived under another account was simply not in the response.
     The TUI's archived tab has always merged them. */
  const d=archived?await api('/api/session/archived?enc='+encodeURIComponent(CUR.encoded))
                  :await api('/api/sessions?enc='+encodeURIComponent(CUR.encoded));
  SESS=d.sessions||[];
  const tagsD=await api('/api/session/tags?'+qs({enc:CUR.encoded,cfgdir:CUR.primary_cfgdir}));
  const tags=tagsD.tags||{};
  /* The TUI's `!` badge condition, verbatim: no CLAUDE.md AND no memory graph.
     Both halves of the action existed in the GUI (scaffold, then build memory)
     and were never chained into one, which is what the ACTIONS row said. */
  const setup=(!archived&&CUR&&CUR.set_up===false)?`<div class="card"
      style="border-left:3px solid var(--warn);margin-bottom:12px">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <b>${ic('bolt')} This project isn't set up yet</b>
        <span style="flex:1;color:var(--dim);font-size:13px">Scaffold its CLAUDE.md and build the memory graph in one go — the same thing <code>!</code> does in the terminal UI.</span>
        <button class="btn pri sm" onclick="projectSetup()">Set up now</button></div></div>`:'';
  const hdr=setup+`<div style="display:flex;gap:8px;margin-bottom:12px;align-items:center">
    <span class="lbl" style="margin:0">${archived?'Archived':'Sessions'} (${SESS.length})</span>
    <span style="flex:1"></span>
    <button class="btn sm" onclick="drawSessions(${archived?'false':'true'})">
      ${archived?'← Active sessions':ic('archive')+' Archived'}</button></div>`;
  if(!SESS.length){
    paint(nav,hdr+`<div class="empty">${archived?'No archived sessions.':'No sessions yet — start one with New session.'}</div>`);
    return;}
  if(!paint(nav,hdr+'<div class="slist">'+SESS.map((s,i)=>{
    const tg=(tags[s.sid]||[]).map(t=>`<span class="tag ok">${esc(t)}</span>`).join(' ');
    return `<div class="sess">
      <span class="dot" style="background:${acctColor(s.account)}"
            title="${esc(s.account||'')}"></span>
      <div class="info">
        <div class="t" id="st${i}" title="${esc(s.title||s.preview||s.sid)}">${esc(s.title||s.preview||s.sid.slice(0,8))} ${tg}</div>
        <div class="meta"><span>${esc(s.age)} ago</span><span>${s.count} msgs</span>
          ${s.tokens?`<span>${esc(s.tokens)} tok</span>`:''}
          ${s.account&&s.account!=='default'?`<span class="tag acct" style="color:${acctColor(s.account)}">${esc(s.account)}</span>`:''}
          ${s.omni?`<span class="tag acct" style="color:var(--violet)" title="Executed via OmniRoute (free-tier model)">omni</span>`:''}</div>
      <!-- inside .info, and absolutely positioned over its right end: the strip
           was opacity:0 but still held its full width at all times, so adding a
           tenth action took a quarter of the title away from every row, read or
           not. .info spans exactly the gap between the account dot and Resume,
           which makes it the one correct positioning context. -->
      <div class="acts">${archived?`
        <button class="btn sm danger" onclick="deleteS(${i},true)" title="Delete">${ic('del')}</button>`:`
        <button class="btn sm" onclick="toggleTune(${i})" title="Adjust power before resuming">${ic('settings')}</button>
        <button class="btn sm" onclick="viewS(${i})" title="Transcript">${ic('doc')}</button>
        <button class="btn sm" onclick="exportS(${i})" title="Export markdown">${ic('download')}</button>
        <button class="btn sm" onclick="filesS(${i})" title="Changed files">${ic('folder')}</button>
        <button class="btn sm" onclick="ckptS(${i})" title="File checkpoints">${ic('history')}</button>
        <button class="btn sm" onclick="tagS(${i})" title="Tags">${ic('label')}</button>
        <button class="btn sm" onclick="renameS(${i})">Rename</button>
        <button class="btn sm" onclick="archiveS(${i})">Archive</button>
        <button class="btn sm" onclick="forkS(${i})">Fork</button>
        <button class="btn sm" onclick="handoffS(${i})"
          title="Start a new chat — in this or another account — seeded with this session's transcript as context">${ic('inject')} Hand off</button>`}
      </div>
      </div>
      <button class="btn sm pri" onclick="${archived?`restoreS(${i})`:`resumeS(${i})`}">${archived?'Restore':'Resume'}</button>
      </div>${archived?'':`
      <div class="rowtune" id="rowtune-${i}" style="display:none">
        <input type="range" class="rtfrontier" min="0" step="1">
        <div class="frontends"><span>Cheap &amp; fast</span><span>Max power</span></div>
        <div class="frontread rtread"></div>
        <button class="btn sm pri" onclick="resumeTuned(${i})" style="margin-top:8px">Resume with these settings →</button>
      </div>`}`;}).join('')+'</div>'))return;
  // a worklog line asked for this session by id; SESS only exists now
  if(PENDING_SID){const i=SESS.findIndex(s=>s.sid===PENDING_SID);
    PENDING_SID=null;if(i>=0)viewS(i);}
}
// one-click resume: launches immediately with the recommended/last-used
// model+effort — no dialog. The settings ⚙ icon expands this row in place
// (no overlay, no dimming) to tune power before launching, per progressive
// disclosure: the common case costs zero clicks, the override is one click
// away and never blocks the list.
function defaultModelEffort(){
  const o=ST.options,d=ST.defaults;
  if(d.model||d.effort)return [d.model||'',d.effort||''];
  const rec=(o.presets||[])[0];
  return rec?[rec[2].model||'',rec[2].effort||'']:['',''];
}
async function doQuickLaunch(cfg,model,effort){
  const d=ST.defaults;
  const opts={effort,model,perm:d.perm||'',max_thinking:d.max_thinking||'',
    subagent_model:d.subagent_model||'',name:'',worktree:'',cfgdir:cfg.cfgdir||''};
  const r=await post('/api/launch',{path:cfg.path,enc:cfg.enc,choice:cfg.choice,opts});
  toast(r.ok?'Launched in a new terminal window':'Launch failed: '+(r.error||'unknown'),
    r.ok?'ok':'err');
  // the launch moment: the session opens in a separate console window, so
  // without this the GUI gives no sign anything happened beyond a toast. Each
  // skin marks it its own way, once (MO.burst never loops).
  if(r.ok)MO.launched($('#content'));
  return r.ok;
}
let TUNE_OPEN=-1;
function toggleTune(i){
  const wasOpen=TUNE_OPEN===i;
  if(TUNE_OPEN>=0){const prev=$('#rowtune-'+TUNE_OPEN);if(prev)prev.style.display='none';}
  TUNE_OPEN=wasOpen?-1:i;
  if(TUNE_OPEN<0)return;
  const el=$('#rowtune-'+i);if(!el)return;
  el.style.display='';
  const sl=el.querySelector('.rtfrontier');
  const rows=ST.options.frontier||[];
  sl.max=Math.max(rows.length-1,0);
  const [dm,de]=defaultModelEffort();
  const fi=rows.findIndex(r=>r[0]===dm&&r[1]===de);
  sl.value=fi>=0?fi:Math.min(2,rows.length-1);
  sl.oninput=()=>updateRowTuneReadout(i);
  updateRowTuneReadout(i);
}
function updateRowTuneReadout(i){
  const el=$('#rowtune-'+i);if(!el)return;
  const sl=el.querySelector('.rtfrontier');
  const rows=ST.options.frontier||[];
  const [_mid,eff,lbl,cost,swe,note]=rows[+sl.value]||rows[rows.length-1]||['','','','','',''];
  el.querySelector('.rtread').innerHTML=`<b>${esc(lbl)} · ${esc(eff)}</b>`
    +`<div class="fsub">${esc(swe)} SWE · ${esc(cost)}</div>`
    +`<div class="fsub" style="font-family:inherit">${esc(note)}</div>`;
}
function resumeTuned(i){
  const s=SESS[i];
  const el=$('#rowtune-'+i);const sl=el.querySelector('.rtfrontier');
  const rows=ST.options.frontier||[];
  const row=rows[+sl.value]||rows[rows.length-1]||['',''];
  doQuickLaunch({path:CUR.path,enc:CUR.encoded,choice:'resume:'+s.sid,cfgdir:s.cfgdir},row[0],row[1]);
  toggleTune(i);
}
function resumeS(i){
  const s=SESS[i];
  const [model,effort]=defaultModelEffort();
  doQuickLaunch({path:CUR.path,enc:CUR.encoded,choice:'resume:'+s.sid,cfgdir:s.cfgdir},model,effort);
}
function forkS(i){const s=SESS[i];
  askLaunch({title:'Fork — '+(s.title||s.sid.slice(0,8)),sub:CUR.name,isNew:false,
    path:CUR.path,enc:CUR.encoded,choice:'fork:'+s.sid,cfgdir:s.cfgdir});}
async function renameS(i){
  const s=SESS[i];
  const v=await ask('Rename session',[{label:'Name',value:s.title||''}]);
  if(v===null)return;
  const r=await post('/api/rename',{enc:CUR.encoded,cfgdir:s.cfgdir||CUR.primary_cfgdir,
    sid:s.sid,name:v[0].trim()});
  toast(r.ok?'Renamed':'Rename failed',r.ok?'ok':'err');drawSessions();
}
async function archiveS(i){const s=SESS[i];
  const r=await post('/api/session/archive',{enc:CUR.encoded,
    cfgdir:s.cfgdir||CUR.primary_cfgdir,sid:s.sid});
  toast(r.ok?'Archived':'Failed: '+(r.errors||[]).join(', '),r.ok?'ok':'err');drawSessions();}
async function restoreS(i){const s=SESS[i];
  const r=await post('/api/session/restore',{enc:CUR.encoded,
    cfgdir:s.cfgdir||CUR.primary_cfgdir,sid:s.sid});
  toast(r.ok?'Restored':'Failed',r.ok?'ok':'err');drawSessions(true);}
async function deleteS(i,arch){const s=SESS[i];
  if(!await confirmBox('Delete session permanently?','This cannot be undone.'))return;
  const r=await post('/api/session/delete',{enc:CUR.encoded,
    cfgdir:s.cfgdir||CUR.primary_cfgdir,sid:s.sid,archived:!!arch});
  toast(r.ok?'Deleted':'Failed',r.ok?'ok':'err');drawSessions(arch);}
async function exportS(i){const s=SESS[i];
  const r=await post('/api/session/export',{enc:CUR.encoded,
    cfgdir:s.cfgdir||CUR.primary_cfgdir,sid:s.sid,path:CUR.path});
  toast(r.message||(r.ok?'Exported':'Failed'),r.ok?'ok':'err');}
async function viewS(i){const s=SESS[i];
  $('#dTitle').textContent=s.title||s.sid.slice(0,8);
  $('#dBody').innerHTML='<div class="empty"><span class="spin"></span></div>';
  $('#drawer').classList.add('show');
  const [t,m]=await Promise.all([
    api('/api/transcript?'+qs({enc:CUR.encoded,cfgdir:s.cfgdir||CUR.primary_cfgdir,sid:s.sid})),
    api('/api/session/meta?'+qs({enc:CUR.encoded,cfgdir:s.cfgdir||CUR.primary_cfgdir,sid:s.sid}))]);
  TSC={sid:s.sid,cfgdir:s.cfgdir||CUR.primary_cfgdir,next:t.next_offset,more:t.more};
  $('#dBody').innerHTML=
    `<div class="card"><h3>Session info</h3><div style="font:12px Consolas,monospace;white-space:pre-wrap">${esc((m.lines||[]).join('\n'))}</div></div>`
    +`<div id="dMsgs">${msgsHtml(t.messages)}</div>`+moreBtn();
}
/* A transcript here reaches 2,787 messages over 100 MB — the drawer pages. */
let TSC=null;
function msgsHtml(ms){return (ms||[]).map(x=>`<div class="msg ${x.role==='user'?'user':''}">
      <div class="who">${x.role==='user'?'User':'Assistant'}</div>
      <div class="body">${esc(x.text)}</div></div>`).join('');}
function moreBtn(){return TSC&&TSC.more
  ?'<div id="dMore" style="text-align:center;padding:12px"><button class="btn" onclick="moreTranscript()">Load more</button></div>':'';}
async function moreTranscript(){
  if(!TSC||!TSC.more)return;
  const b=$('#dMore');if(b)b.innerHTML='<span class="spin"></span>';
  const t=await api('/api/transcript?'+qs({enc:CUR.encoded,cfgdir:TSC.cfgdir,
    sid:TSC.sid,offset:TSC.next}));
  TSC.next=t.next_offset;TSC.more=t.more;
  const host=$('#dMsgs');if(host)host.insertAdjacentHTML('beforeend',msgsHtml(t.messages));
  const m=$('#dMore');if(m)m.outerHTML=moreBtn();
}
async function filesS(i){const s=SESS[i];
  const d=await api('/api/session/changed-files?'+qs({enc:CUR.encoded,
    cfgdir:s.cfgdir||CUR.primary_cfgdir,sid:s.sid}));
  $('#dTitle').textContent='Changed files — '+(s.title||s.sid.slice(0,8));
  $('#dBody').innerHTML=(d.files&&d.files.length)
    ?'<div class="card">'+d.files.map(f=>`<div style="font:12px Consolas,monospace;padding:2px 0">${esc(Array.isArray(f)?f.join('  '):f)}</div>`).join('')+'</div>'
    :'<div class="empty">No file changes recorded.</div>';
  $('#drawer').classList.add('show');
}
async function tagS(i){const s=SESS[i];
  const cur=await api('/api/session/tags?'+qs({enc:CUR.encoded,cfgdir:CUR.primary_cfgdir}));
  const now=(cur.tags||{})[s.sid]||[];
  const v=await ask('Tags',[{label:'Comma-separated tags',value:now.join(', ')}]);
  if(v===null)return;
  const tags=v[0].split(',').map(t=>t.trim()).filter(Boolean);
  await post('/api/session/tags',{enc:CUR.encoded,cfgdir:CUR.primary_cfgdir,sid:s.sid,tags});
  toast('Tags saved','ok');drawSessions();
}

/* The two flags the GUI used to only PRINT (`${st.hook_on?'on':'off'}`) — a
   read-only toggle is the class of gap no route-coverage gate can see, because
   there was no route at all. One writer per flag, in memhub. */
async function memToggle(patch){
  const r=await post('/api/memory/toggles',Object.assign({},C(),{path:CUR.path},patch));
  toast(r.ok?'Saved':'Failed','ok');drawMemory();}

/* memory tab.

   Memory is not "CLAUDE.md plus a few counters" — claudectl builds twelve
   artifacts per project, and until now the tab could name three of them. The
   two questions this tab answers are "what does claudectl know" and "what does
   knowing it cost me", and the cost half is what was missing: the digest is
   read on EVERY turn while several KB of rules cost nothing until Claude opens
   a matching path, and nothing said so. Every number below already existed in
   a payload this renderer was already fetching. */

/* how far an artifact reaches into a session — the vocabulary is the whole
   point of the table, because 'always' is the only one you pay for on every
   single turn and everything else is conditional or free. */
const REACH={
  always:['every session','tag warn','Loaded at the start of every session and read on every turn. This is the only kind you pay for unconditionally.'],
  lazy:  ['when Claude opens a matching file','tag',"Costs nothing until Claude reads a file the rule's paths match — then only that one rule loads."],
  prompt:['every prompt', 'tag warn','Added to each message you send, inside the recall budget. The only cost that multiplies by how much you talk.'],
  start: ['session start','tag',     'Injected once, when a session starts.'],
  source:['on request',   'tag ok',  'Never loaded on its own. It is what the always-on surfaces are built FROM, and you can query it directly with `claudectl recall`.'],
  never: ['not loaded',   '',        'claudectl reads it to do its job. Claude never sees it, and it costs you nothing.']};
function rch(k){const r=REACH[k]||REACH.never;
  return r[1]?`<span class="${r[1]}" title="${esc(r[2])}">${r[0]}</span>`
             :`<span style="color:var(--dim2)" title="${esc(r[2])}">${r[0]}</span>`;}
/* One row = name, a sentence saying what it does FOR YOU, its cost, an action.
   The sentence is inline and never a tooltip — a tooltip is for a glossary, not
   for the thing that makes the row comprehensible at all. The file path IS a
   tooltip, because nobody asks it first. */
/* "Is this stale?" is the second question, right after "who wrote it?", and no
   row could answer it: the graph carries one build time while the worklog, the
   rules and the two logs are each written by a different code path on its own
   schedule. `when` is the artifact's own mtime, in epoch seconds — sent by
   /api/memory/state only for the artifacts that ARE one file, so a row that
   cannot honestly be dated shows nothing rather than a plausible lie. */
function ago(sec){
  if(!sec)return '';
  const s=Math.max(0,Date.now()/1000-sec);
  for(const [u,n] of [['d',86400],['h',3600],['m',60]])
    if(s>=n)return Math.floor(s/n)+u+' ago';
  return 'just now';
}
function invRow(name,does,where,size,action,when){
  return `<tr><td title="${esc(where)}"><b>${name}</b>
      <div style="color:var(--dim2);font-size:11px">${does}</div>
      ${when?`<div style="color:var(--dim2);font-size:11px" title="${esc(new Date(when*1000).toLocaleString())}">updated ${ago(when)}</div>`:''}</td>
    <td class="num" style="white-space:nowrap">${size}</td>
    <td style="white-space:nowrap">${action||''}</td></tr>`;}
function invTable(rows){
  return `<table class="tbl" style="margin:2px 0 4px">${rows}</table>`;}
/* The load rule is stated ONCE per group instead of as a badge repeated on
   every row, which is what turned a 12-row table into a wall. */
function bucket(title,reach,note){
  return `<div class="lbl" style="margin-top:16px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap">
    <span>${title}</span>${rch(reach)}
    ${note?`<span style="color:var(--dim2);font-weight:400;text-transform:none;letter-spacing:0">${note}</span>`:''}</div>`;}
//: what a session's context window holds, for the "…of what Claude can hold" line
const CTX_WINDOW=200000;
/* The auto-memory cadence, read back the way a person would say it. It is the
   user's own setting, and it is the answer to "when do the queued modules get
   done" — a cycle is capped on purpose, so nothing comes sooner than this. */
function every(sec){
  const s=+sec||3600;
  if(s<3600)return Math.round(s/60)+' min';
  const h=s/3600;
  return (h%1?h.toFixed(1):h)+(h===1?' hour':' hours');}
/* a name and a hit count says a fact matters without saying what it IS */
async function entDetail(name){
  const d=await api('/api/memory/entity?'+qs({...C(),name}));
  const b=$('#dBody');if(!b)return;
  $('#dTitle').textContent=name;
  b.innerHTML=!d.found?'<div class="empty">Not in the graph any more.</div>':`
    <div class="kv">
      <span class="k">What it is</span><span>${esc(d.type||'')}${d.kind?' · '+esc(d.kind):''}${d.valid?'':' <span class="tag warn">superseded</span>'}${d.status?` <span class="tag">${esc(d.status)}</span>`:''}</span>
      <span class="k">Where</span><span>${esc(d.unit||'')}</span>
      <span class="k" title="How many prompts recall has injected this into. It is a term in the ranking, and in what eviction drops first.">Recalled into</span><span>${d.hits} prompt(s)</span>
    </div>
    <p style="margin:10px 0">${esc(d.summary||'')}</p>
    ${d.unit_summary?`<div class="lbl">The module it belongs to</div>
      <p style="color:var(--dim);margin:2px 0 10px">${esc(d.unit_summary)}</p>`:''}
    ${(d.source_files||[]).length?`<div class="lbl">Read from</div>
      <div class="chips">${d.source_files.map(f=>`<span class="chip">${esc(f)}</span>`).join('')}</div>`:''}
    ${(d.relations||[]).length?`<div class="lbl" style="margin-top:10px">Connected to</div>
      ${d.relations.map(r=>`<div class="agrow"><span style="flex:1">${r.dir==='out'?'':'← '}${esc(r.rel)} <b>${esc(r.other)}</b></span>
        <button class="btn sm" onclick="entDetail(${hesc(r.other)})">open</button></div>`).join('')}`:''}
    ${(d.sessions||[]).length?`<div class="lbl" style="margin-top:10px">Learned in</div>
      <div class="chips">${d.sessions.map(s=>`<span class="chip">${esc(String(s).slice(0,8))}</span>`).join('')}</div>`
      :`<p style="color:var(--dim2);font-size:12px;margin-top:10px">Recall counts hits per fact, not per session, so there is no per-session breakdown for a code fact — only lessons carry the session they came from.</p>`}`;
  $('#drawer').classList.add('show');
}
/* what actually raises each freshness check, as a button. workspace._FIXES is
   phrased in TUI keystrokes ("press c", "m → b"), which is the right answer
   there and no answer at all here. */
const WSFIX={claude_md:['Scaffold','cmScaffold()'],
  claude_md_fresh:['Rebuild memory','buildMemory()'],
  repo:['Rebuild memory','buildMemory()'],
  sessions:['Rebuild memory','buildMemory()'],
  manifest:['Rebuild memory','buildMemory()'],
  mcp_docs:['MCP servers',"go('mcp')"],
  /* No button can fix this one: it says the prose and the graph disagree, and
     which side is wrong is a judgement. So it opens the file rather than
     pretending to repair it — rebuilding memory is one tab away from there. */
  claude_md_claims:['CLAUDE.md',"TAB='claudemd';drawProject()"],
  conflicts:['AI analyze',"inlineJob('#jban','ai_scaffold',C(),{label:'AI-analyzing project',redraw:()=>drawMemory()})"]};

async function drawMemory(){
  const nav=paintNow(LOADING);
  const c=C();
  // Three fast calls, and NOT the slow one. /api/workspace-status hashes files
  // and walks every transcript folder; awaiting it here made the whole tab wait
  // seconds on a card that is below the fold. It fills #wsBox after the paint,
  // the same shape drawClaudeMd uses for #spBox.
  const [st,les,wl]=await Promise.all([
    api('/api/memory/state?'+qs(c)),api('/api/lessons?'+qs(c)),
    api('/api/worklog?'+qs(c))]);
  if(nav!==NAV_ID)return;   // navigated away mid-fetch — don't clobber the new page
  const est=st.est||{},rules=est.rules||[],
        ruleTok=rules.reduce((a,r)=>a+(r.tokens||0),0),
        ttl=les.ttl||30,counter=les.counter||0;
  const lesRows=(les.lessons||[]).map(l=>{
    const left=ttl-(counter-(l.last_used||0));
    return `<tr><td>${l.status==='pending'?'…':l.status==='pinned'?ic('pin'):ic('check')} ${esc(l.status)}
      ${l.kind?`<span class="tag">${esc(l.kind)}</span>`:''}</td>
    <td><b>${esc(l.name)}</b><div style="color:var(--dim);font-size:12px">${esc(l.summary)}</div></td>
    <td class="num">${(l.confidence||0).toFixed(1)}</td>
    <td class="num">${l.status==='pinned'?'<span class="tag ok">kept</span>'
      :`<span class="${left<=5?'tag warn':'tag'}" title="Dropped when it has gone ${ttl} sessions unused.">${Math.max(0,left)}</span>`}</td>
    <td style="white-space:nowrap">
      <button class="btn sm" onclick="lessonAct(${hesc(l.id)},'approve')" title="Approve">${ic('check')}</button>
      <button class="btn sm" onclick="lessonAct(${hesc(l.id)},'pin')" title="Pin">${ic('pin')}</button>
      <button class="btn sm danger" onclick="lessonAct(${hesc(l.id)},'evict')" title="Evict">${ic('close')}</button></td></tr>`;}).join('');
  // coverage against something REAL: how many of the graph's own modules got a
  // rule file. The old denominator read `ws.modules`, a key no endpoint has
  // ever returned, so it fell back to a constant 8 for the feature's whole life.
  const cover=st.n_modules?Math.min(1,rules.length/st.n_modules):0;
  // A raw token total is a number nobody can calibrate. As a share of what a
  // session can hold it is both honest and small, which is the de-alarming fact.
  const pctWin=(((est.digest_tokens||0)/CTX_WINDOW)*100).toFixed(1)+'%';
  const al=st.auto_last||{},wlIns=wl.installed,
        wlMissing=wlIns&&typeof wlIns==='object'
          ?Object.keys(wlIns).filter(k=>!wlIns[k]):[];
  // The bookkeeping bucket: never sent to Claude, so it collapses behind one
  // summary line. Built as a list so the count in that line cannot drift from
  // the rows it is counting.
  const W=st.written||{};
  const stored=[
    invRow('Semantic graph','Everything claudectl has learned about the code. Nothing reads it directly — every surface above is written from it.',
      '.claudectl/memory/graph.json',`${st.n_entities||0}+${st.n_lessons||0}`,
      `<button class="btn sm" onclick="go('graph')">${ic('share')} Graph</button>`,W.graph),
    invRow('Reinforcement log','Which facts recall actually injected. Folded into the graph on the next build; it is what makes useful facts survive.',
      '.claudectl/memory/hits.log',
      st.hits_pending?`<span class="tag">${st.hits_pending} to fold in</span>`:'folded in','',W.hits),
    invRow('Edit log','Files Claude changed, so the next cycle knows what to re-read instead of rescanning everything.',
      '.claudectl/memory/dirty.log',
      st.dirty?`<span class="tag warn">${st.dirty} queued</span>`:'0',
      st.dirty_hook?'':`<span class="hlink" onclick="go('hooks')">install hook</span>`,W.dirty),
    invRow('Workspace manifest','What memory was built from — commit, file hashes, session count — so staleness is a fact rather than a guess.',
      '.claudectl/workspace-manifest.json',
      '<span id="wsScore" style="color:var(--dim2)">checking…</span>',
      `<span class="hlink" onclick="showDetail('wsBox')">show ↓</span>`,W.manifest),
    // "12 kept" was the CAP read as a count — it said twelve whether there
    // were twelve or none. The honest pair is the limit and the last write.
    invRow('Version snapshots','A copy of everything claudectl was about to overwrite. Nothing it shrinks is gone until it falls off this list.',
      '.claudectl/snapshots/','up to 12 each',
      `<span class="hlink" onclick="TAB='claudemd';drawProject()">History</span>`,W.snapshots)];
  paint(nav,`
    <div class="card"><h3>What claudectl knows <span class="sp"></span>
      <button class="btn sm" onclick="askMem()">${ic('chat')} Ask</button>
      <button class="btn sm" onclick="recallPrev()">${ic('eye')} Recall preview</button>
      <button class="btn sm pri" onclick="buildMemory()">${ic('bolt')} Build with Claude</button></h3>
      <p style="color:var(--dim);font-size:13px;margin:0 0 10px">claudectl reads this project — its code, its git history and your past sessions — and
        builds a knowledge graph of it. The graph itself is never sent to Claude. What a session
        actually reads is written <i>from</i> it, and the next card is that list.</p>
      <div class="memhd">
        ${INST.html('ring','memory',{fmt:'pct',sub:'covered',label:'modules with a rule'})}
        <div class="kv" style="flex:1">
        <span class="k">Entities</span><span>${st.n_entities||0} across ${st.n_modules||0} module(s)</span>
        <span class="k">Relations</span><span>${st.n_relations||0}${st.n_module_edges?` · ${st.n_module_edges} module link(s)`:''}</span>
        <span class="k">Lessons</span><span>${st.n_lessons||0} (${st.n_pending||0} pending review)</span>
        <span class="k">Unscanned sessions</span><span>${st.n_unscanned||0} of ${st.session_counter||0} seen</span>
        <span class="k">Built</span><span>${esc(st.generated_at||'never')}</span>
      </div></div>
      ${(st.top||[]).length?`<div class="lbl" style="margin-top:10px" title="How often recall has injected each fact. It is a term in the ranking and in what eviction drops first.">Facts Claude reaches for most</div>
        <div class="chips">${st.top.map(t=>`<span class="chip" style="cursor:pointer" onclick="entDetail(${hesc(t.name)})" title="Recalled into ${t.hits} prompt(s)${t.module?' · '+esc(t.module):''} — click for what it means">${esc(t.name)} <i>${t.hits}</i></span>`).join('')}</div>`:''}
      ${(st.evicted_names||[]).length?`<div class="lbl" style="margin-top:10px" title="When the graph passes its cap, the least-used unpinned facts go. Pinned ones never do.">Dropped to stay under the cap (${st.evicted||0})</div>
        <div class="chips">${st.evicted_names.slice(0,12).map(n=>`<span class="chip">${esc(n)}</span>`).join('')}</div>`:''}
      <div class="lbl" style="margin-top:16px">Keeping it current</div>
      <p style="color:var(--dim);font-size:13px;margin:0 0 10px">Keeping memory current costs Claude calls. One pass runs when you open claudectl and
        one every ${every(st.auto_interval)} after that — never sooner, however much is left to do.
        Each pass re-reads at most a few changed modules, so a big backlog is spread across
        passes instead of spent at once.</p>
      <label class="autoline" style="margin-top:0" title="Refresh this project's memory in the background — on launch and then on the interval, from whichever interface is running — whenever its files change, without needing this tab open.">
        <input type="checkbox" id="autoMem" ${st.auto_on?'checked':''} onchange="toggleAutoMem(this.checked)">
        <span>${ic('refresh')} Keep this project's memory updated automatically</span>
        <span class="tag" title="Change it on the Claude Code page, under auto-memory.">every ${every(st.auto_interval)}</span>
        ${st.auto_updated?`<span class="tag">last run ${esc(String(st.auto_updated).slice(0,16))}</span>`:''}</label>
      <div style="margin-top:12px">${INST.html('spark','memcost',{fmt:'usd',label:'what each cycle cost',noread:true})}</div>
      <div class="kv" style="margin-top:10px">
        <span class="k">Last cycle</span><span>${al.extracted??st.last_extracted??0} module(s)${al.lessons?` · ${al.lessons} lesson(s)`:''}${al.scanned?` · ${al.scanned} session(s) scanned`:''}${st.last_cost_usd?` · $${(+st.last_cost_usd).toFixed(3)}`:''}</span>
        <span class="k" title="Every Claude call memory has ever paid for on this project. Asking memory is a query, not memory being built, and is not counted.">Spent in total</span><span>$${(+(st.cost_usd_total||0)).toFixed(3)}</span>
        ${st.last_failed?`<span class="k">Failed</span><span class="warn">${st.last_failed} module(s) could not be extracted${st.last_error?` — ${esc(String(st.last_error).slice(0,160))}`:''}. Retried on the next pass, in ${every(st.auto_interval)} — not sooner, so a failing account is not called in a loop.</span>`:''}
        ${st.last_skipped?`<span class="k">Still queued</span><span>${st.last_skipped} module(s) — the next pass takes some, in ${every(st.auto_interval)}</span>`:''}
        ${(!st.last_failed&&!st.last_skipped&&st.pending_units)?`<span class="k">Still queued</span><span>${st.pending_units} module(s) — the next pass takes some, in ${every(st.auto_interval)}</span>`:''}
        ${st.dirty?`<span class="k">Edited since</span><span>${st.dirty} file(s) waiting for the next cycle</span>`:''}
      </div>
      ${st.dirty_hook?'':`<div style="margin-top:8px"><span class="tag warn">stale-on-edit hook not installed</span>
        <span style="color:var(--dim);font-size:12px">— memory will not notice edits until something else triggers a cycle.</span>
        <span class="hlink" onclick="go('hooks')">Install it</span></div>`}
      <div id="memProg"></div>
    </div>
    <div class="card"><h3>What a session costs <span class="sp"></span>
      <button class="btn sm" onclick="inlineJob('#jban','rules_sync',C(),{label:'Rebuilding rules',redraw:()=>drawMemory()})" title="Rewrite the path-scoped rule files from the current graph. Free — no Claude call.">${ic('refresh')} Rebuild rules</button></h3>
      <p style="color:var(--dim);font-size:13px;margin:0 0 8px">You write the prose in CLAUDE.md. claudectl writes everything below it, and every
        one of these can be opened, rebuilt or switched off. What separates them is <b>when</b>
        each one reaches a session — which is what the four groups say.</p>
      <p style="color:var(--dim);font-size:13px;margin:0 0 4px">
        <b>~${est.digest_tokens||0} tok</b> of it is in every session — <b>${pctWin}</b> of what Claude can hold ·
        ${est.hook_budget!=null?`up to <b>${est.hook_budget} tok</b> more per prompt`:'no per-prompt recall'} ·
        <b>~${ruleTok} tok</b> across ${rules.length} rule file(s), read only when Claude opens a matching path</p>
      <div id="memInv">
      ${bucket('Always on','always',`~${est.digest_tokens||0} tok · ${pctWin} of the context window${
        st.generated_at?` · rebuilt ${esc(String(st.generated_at).slice(0,16))}`:''}`)}
      ${invTable(
        invRow('CLAUDE.md digest','What this project is, its modules, and the lessons worth carrying into every session.',
          'the CLAUDECTL:MEMORY block in CLAUDE.md',`~${est.digest_tokens||0} tok`,
          `<span class="hlink" onclick="TAB='claudemd';drawProject()">CLAUDE.md</span>`)
       +invRow('AUTOGEN / SESSIONS','Which repos this is, its recent commits, and what past sessions were about.',
          'blocks in CLAUDE.md','see blocks',
          `<span class="hlink" onclick="TAB='claudemd';drawProject()">CLAUDE.md</span>`)
       +invRow('Cross-project conventions','Rules that recur across your repos, promoted once so every project on this account gets them.',
          'a block in your user CLAUDE.md','account-wide',
          `<span class="hlink" onclick="go('globalmd')">Global</span>`)
       +invRow('Recent work','A one-line summary of what each session changed, put back when the next one starts.',
          '.claudectl/memory/worklog.json',
          `${(wl.entries||[]).length} entr${(wl.entries||[]).length===1?'y':'ies'}`,
          `<span class="hlink" onclick="showDetail('memWork')">show ↓</span>`,W.worklog))}
      ${bucket('Every prompt','prompt','the only one that grows with how much you talk')}
      ${invTable(invRow('Recall','The slice of the graph matching what you just typed, added to the message before it is sent.',
        'the UserPromptSubmit recall hook',
        st.budget?`up to ${st.budget} tok`:'<span class="tag">off</span>',''))}
      <label class="autoline" style="margin-top:0" title="UserPromptSubmit recall: inject the task-relevant slice of this project's memory before every prompt. The hook itself is installed into every account.">
        <input type="checkbox" id="memHook" ${st.hook_on?'checked':''} onchange="memToggle({hook:this.checked})">
        <span>${ic('bolt')} Per-prompt recall for this project</span></label>
      <div class="fld" style="margin-top:8px;max-width:320px"><label>Recall budget (tokens per prompt)</label>
        <input type="number" id="memBudget" min="0" max="20000" step="50"
          value="${st.budget==null?600:st.budget}" onchange="memToggle({budget:+this.value})">
        <div style="color:var(--dim2);font-size:12px;margin-top:4px">0 turns injection off. Try it with Recall preview.</div></div>
      ${bucket('When relevant','lazy',`${rules.length} rule file(s) · ~${ruleTok} tok, none of it loaded until it matches${
        W.rules?` · rewritten ${ago(W.rules)}`:''}`)}
      <details><summary style="cursor:pointer;padding:6px 0">
        <b>Path-scoped rules</b> — one file per module. Claude loads the one whose
        <code>paths:</code> cover the file it just opened, and no others ·
        ${rules.length} file(s), ~${ruleTok} tok</summary>
        <table class="tbl" style="margin:4px 0 8px">
          <tr><th>module</th><th>loads for</th><th>tok</th></tr>
          ${rules.map(r=>`<tr><td>${esc(r.unit||r.file||'')}</td>
            <td><code style="color:var(--cyan)">${esc(r.glob||'?')}</code></td>
            <td class="num">${r.tokens||0}</td></tr>`).join('')
            ||'<tr><td colspan="3" style="color:var(--dim)">None written yet.</td></tr>'}
        </table>
        <label class="autoline" style="margin:0" title="Write .claude/rules/claudectl-mem-*.md so Claude Code loads the memory for the paths it is actually working in.">
          <input type="checkbox" id="memRules" ${st.rules_on?'checked':''} onchange="memToggle({rules:this.checked})">
          <span>${ic('folder')} Keep the rule files in sync</span></label></details>
      ${invTable(invRow('Lessons','Things this project learned the hard way — a bug and its fix, a decision, a preference you corrected. Recall injects one when it applies.',
        'entities inside the graph',`${st.n_lessons||0}`,
        `<span class="hlink" onclick="showDetail('memLessons')">show ↓</span>`,W.graph))}
      ${bucket('Stored, not loaded','source',`${stored.length} records · 0 tokens — read only when you or Claude ask`)}
      <details id="memKeep"><summary style="cursor:pointer;padding:6px 0;color:var(--dim);font-size:13px">
        What claudectl keeps for itself, so it knows what to rebuild and can undo it</summary>
      ${invTable(stored.join(''))}
      </details></div></div>
    <div class="card"><h3>What it learned <span class="sp"></span>
      <button class="btn sm" onclick="inlineJob('#jban','lessons_scan',C(),{label:'Learning from sessions',redraw:()=>drawMemory()})">${ic('school')} Learn from sessions${st.n_unscanned?` <span class="tag warn">${st.n_unscanned} new</span>`:''}</button>
      <button class="btn sm" onclick="lessonAct('','approve_all')">${ic('check')} Approve all pending</button></h3>
      <p style="color:var(--dim);font-size:13px;margin:0 0 10px">What this project learned the hard way, read back out of your past sessions — a bug and its
        fix, a decision and why, something you corrected. Approved ones are injected when relevant;
        one nobody uses for ${ttl} sessions is dropped, unless you pin it.</p>
      <div id="memLessons">${lesRows?`<table class="tbl"><tr><th>status</th><th>lesson</th><th>conf</th>
        <th title="Sessions of disuse left before it is dropped.">left</th><th></th></tr>${lesRows}</table>`
        :'<div class="empty">No lessons yet.</div>'}</div>
      <div class="lbl" style="margin-top:16px">Recent work</div>
      <p style="color:var(--dim);font-size:12px;margin:0 0 8px">A token-free log of what each session changed, injected into the next session on start (claude-mem style).</p>
      <label class="autoline" style="margin-top:0" title="On session end, record a one-line summary + files touched; inject the last few on the next SessionStart.">
        <input type="checkbox" id="wlOn" ${wl.on?'checked':''} onchange="toggleWorklog(this.checked)">
        <span>${ic('school')} Track recent work for this project</span></label>
      ${wlMissing.length?`<div style="margin-top:8px"><span class="tag warn">hook missing on ${esc(wlMissing.join(', '))}</span>
        <span style="color:var(--dim);font-size:12px">— sessions under that account record nothing.</span>
        <span class="hlink" onclick="go('hooks')">Fix</span></div>`:''}
      <div id="memWork">${(wl.entries||[]).length?`<table class="tbl" style="margin-top:10px"><tr><th>when</th><th>summary</th><th>files</th></tr>`
        +wl.entries.map(e=>`<tr${e.session_id?` style="cursor:pointer" onclick="openWorkSession(${hesc(e.session_id)})" title="Open this session"`:''}>
          <td style="white-space:nowrap;color:var(--dim)">${esc(e.ended_at||'')}</td>
          <td>${esc(e.summary||'')}</td>
          <td style="color:var(--dim);font-size:12px">${esc((e.files||[]).join(', '))}</td></tr>`).join('')
        +`</table>`:'<div class="empty">No sessions recorded yet.</div>'}</div></div>
    <div class="card"><h3>Freshness &amp; history <span class="sp"></span><span id="wsHead"></span></h3>
      <p style="color:var(--dim);font-size:13px;margin:0 0 10px">How much of what claudectl generated still matches the code as it is now. Each row is worth
        the points beside it; clearing one raises the score.</p>
      <div id="wsBox"><span class="spin"></span></div>
      <div class="lbl" style="margin-top:12px">History</div>
      <p style="color:var(--dim);font-size:12px;margin:0 0 6px">Every graph claudectl replaced. Eviction and rebuild both snapshot what they were about to overwrite, and restoring is itself snapshotted.</p>
      <div id="histOut"><span class="spin"></span></div></div>`);
  INST.set('memory',{v:cover,tone:cover>=.6?'ok':cover>=.25?null:'warn'});
  setRead('memory',cover*100);
  const hist=(st.cost_history||[]);
  if(hist.length>1)INST.set('memcost',{series:hist});
  drawHistory(['memory_graph']);
  fillWorkspace();
}
/* The freshness checks, fetched AFTER the page is on screen. Hashing files and
   walking every transcript folder is the slowest thing this tab asks for, and
   it answers a question nobody has while the page is still blank. Writes its
   own node, never #content — and re-queries it, because the fetch outlives a
   navigation away. */
async function fillWorkspace(){
  let ws;
  try{ws=await api('/api/workspace-status?'+qs(C()));}
  catch(e){const b=$('#wsBox');if(b)b.innerHTML='<div class="empty">Could not read workspace status.</div>';return;}
  const b=$('#wsBox');if(!b)return;
  b.innerHTML=(ws.checks||[]).map(k=>{
    const f=WSFIX[k.name]||null,na=!k.applicable;
    return `<div class="hrow">
      <span style="min-width:150px">${esc(k.name.replace(/_/g,' '))}</span>
      <span style="min-width:74px">${na?'<span style="color:var(--dim2)">n/a</span>'
        :k.state==='fresh'?'<span class="tag ok">fresh</span>'
        :k.state==='invalid'?'<span class="tag warn">invalid</span>':'<span class="tag warn">stale</span>'}</span>
      <span class="info" style="flex:1;color:var(--dim)">${esc(k.detail||'')}</span>
      <span style="color:var(--dim2);font-size:11px;min-width:44px" title="Share of the freshness score this check carries.">${na?'—':'+'+(k.weight||0)}</span>
      ${(na||k.state==='fresh'||!f)?'':`<button class="btn sm" onclick="${f[1]}">${esc(f[0])}</button>`}
    </div>`;}).join('')||'<div class="empty">No checks ran.</div>';
  const h=$('#wsHead');
  if(h)h.innerHTML=`<span style="color:var(--dim2);font-size:12px">score ${ws.score??'?'}</span>
    ${ws.safe?'<span class="tag ok">safe to launch</span>':'<span class="tag warn">attention</span>'}`;
  const s=$('#wsScore');if(s)s.textContent=`score ${ws.score??'?'}`;
}
/* One bucket row states what an artifact COSTS; the table stating what is in it
   is a card further down. The row links there instead of repeating the numbers
   in both places, where they can disagree. */
function showDetail(id){
  const el=$('#'+id);if(!el)return;
  const d=el.closest('details');if(d)d.open=true;
  el.scrollIntoView({behavior:'smooth',block:'center'});
}
/* a worklog line names its session; opening it was one field away the whole
   time. `viewS` indexes into SESS, which drawSessions builds — so hand the sid
   to that renderer rather than trying to resolve it here. */
let PENDING_SID=null;
function openWorkSession(sid){PENDING_SID=sid;TAB='sessions';drawProject();}
async function toggleWorklog(on){
  await post('/api/worklog',{enc:CUR.encoded,on});
  toast(on?'Recent-work tracking on':'Recent-work tracking off','ok');
}
/* live progress of a running memory scan (fg job or bg worker), and — once the
   lock clears — how the last one ENDED. The poller only ever read `progress`,
   so a cycle that crashed cleared the lock exactly like one that succeeded and
   the tab said nothing either way. `last` has been in this payload all along.
   Cached in __plMemProg: an unconditional innerHTML every 600ms rebuilds nodes
   and reflows for a string that almost never changes. */
let __plMemProg=null;
async function pollMemProg(){
  const el=$('#memProg');
  if(el&&PAGE_==='project'&&TAB==='memory'){
    try{
      const d=await api('/api/memory/progress?'+qs({path:CUR.path}));
      const L=d.last||null;
      const html=(d.progress!==null&&d.progress!==undefined)
        ?`<div style="display:flex;gap:8px;align-items:center;margin-top:10px;color:var(--warn)">
           <span class="spin"></span> Memory scan running… ${esc(d.progress||'')}</div>`
        :(L&&L.ok===false)
        ?`<div style="margin-top:10px"><span class="tag warn">last run failed</span>
           <span style="color:var(--dim);font-size:12px">${esc(L.error||'')}</span></div>`
        :(L&&L.ok)
        ?`<div style="margin-top:10px;color:var(--dim);font-size:12px">Last run: ${L.extracted||0} module(s), ${L.lessons||0} lesson(s)${L.pending?`, ${L.pending} still queued`:''}.</div>`
        :'';
      if(html!==__plMemProg){__plMemProg=html;el.innerHTML=html;}
    }catch(e){}
  }
}
async function lessonAct(id,action){
  await post('/api/lessons',{...C(),id,action});drawMemory();}
async function toggleAutoMem(on){
  CUR.auto_memory=on;
  const p=(ST.projects||[]).find(x=>x.encoded===CUR.encoded);if(p)p.auto_memory=on;
  await post('/api/memory/auto',{enc:CUR.encoded,auto:on});
  drawProjects();
  toast(on?'Auto-memory on — updates in the background when files change'
          :'Auto-memory off','ok');
}
async function buildMemory(){
  // if a background worker is already refreshing this project, just show the
  // badge instead of starting a second, colliding run
  try{const r=await api('/api/memory/progress?'+qs({path:CUR.path}));
    if(r.progress!=null){startMemBadge();toast('Memory update already running','ok');return;}}catch(e){}
  inlineJob('#jban','memory_build',C(),{label:'Building memory',redraw:()=>drawMemory()});
}
async function askMem(){
  const v=await ask('Ask project memory',[{label:'Question'}]);
  if(v===null||!v[0].trim())return;
  inlineJob('#jban','memory_ask',{...C(),question:v[0]},{label:'Asking memory',onDone:st=>{
    $('#dTitle').textContent='Memory answer';
    $('#dBody').innerHTML=`<div class="msg"><div class="who">Answer</div>
      <div class="body">${esc(st.result||'')}</div></div>`;
    $('#drawer').classList.add('show');}});
}
async function recallPrev(){
  const v=await ask('Recall preview',[{label:'Simulated prompt',ph:'e.g. fix the launch bug'}]);
  if(v===null)return;
  const d=await api('/api/recall-preview?'+qs({...C(),q:v[0]}));
  const b=$('#dBody');if(!b)return;
  $('#dTitle').textContent=`Recall preview (${d.tokens||0} tok)`;
  // the items ARE the answer to "why did that text appear" — they were computed
  // and dropped at the endpoint. Previewing does not reinforce (log=False), so
  // opening this repeatedly no longer reshapes what recall will pick next time.
  b.innerHTML=d.empty?'<div class="empty">Nothing would be injected.</div>'
    :`${(d.items||[]).length?`<div class="lbl">Injected (${d.items.length})</div>
       <div class="chips">${d.items.map(n=>`<span class="chip">${esc(n)}</span>`).join('')}</div>`:''}
     <div class="msg"><div class="body">${esc(d.context)}</div></div>`;
  $('#drawer').classList.add('show');
}

/* CLAUDE.md tab — the file, block by block.

   CLAUDE.md is five things stacked in one file: your prose, KEEP-fenced
   regions, and three blocks claudectl rewrites (AUTOGEN, SESSIONS, MEMORY).
   Shown as one 46vh scroll of raw text, there was no way to tell which part
   cost what, which button regenerated which part, or that "Prune" only ever
   touches the SESSIONS block. `ctxaudit.split_blocks` already did the split for
   the token audit; the endpoint just stopped throwing the result away. */
const CMFIX={
  manual:['Compress',"inlineJob('#jban','ai_compress',C(),{label:'Compressing CLAUDE.md',redraw:()=>drawClaudeMd()})",
          'Rewrite your prose shorter with Claude. KEEP-fenced text is never sent.'],
  keep:['Protect more','cmProtect()',
        'Fence a section so AI compression can never reword, shorten or drop it.'],
  autogen:['Rebuild','cmScaffold()','Re-derive the repo and commit block.'],
  sessions:['Prune','cmPrune()','Drop session entries past the cap. Transcripts are untouched.'],
  memory:['Rebuild','buildMemory()','Re-extract the project memory this digest is built from.'],
  agents:['Agents',"go('agents')",'Written from the agents installed here — manage them on the Agents page.'],
  loop:['Loops',"go('loops')",'Written by the background loop — start, watch or end one on the Loops page.']};
/* WHAT each block is, in one line, and WHO writes it. Two questions, and the
   row answered neither: "maintained by claudectl" says who and not what, and
   was the same six words on three different blocks. A subtitle, never a
   tooltip — the row has to be readable without hovering it. */
const CMWHAT={
  manual:['What you wrote — the instructions you want in every session here.','you'],
  keep:['Sections you fenced off. AI compression is not even shown this text.','you'],
  autogen:['Which repos this is, and what has been committed lately.','claudectl'],
  sessions:['What your past sessions in this project were about.','claudectl'],
  memory:['The project memory digest — modules, dependencies, hard-won lessons.','claudectl'],
  agents:['The subagents installed here and when to delegate to each. Claude Code only picks an agent it has been told about.','claudectl'],
  loop:['What a background /loop has done lately, rewritten on every run.','claudectl']};
async function drawClaudeMd(){
  const nav=paintNow('<div class="empty"><span class="spin"></span></div>');
  const c=C();
  const [md,mm]=await Promise.all([api('/api/claude-md?'+qs(c)),
                                   api('/api/memory-map?'+qs(c))]);
  const blocks=md.blocks||[];
  if(!paint(nav,`
    <div class="card"><h3>CLAUDE.md — block by block <span class="sp"></span>
      <button class="btn sm" onclick="cmScaffold()">${ic('doc')} Scaffold</button>
      <button class="btn sm" onclick="inlineJob('#jban','ai_scaffold',C(),{label:'AI-analyzing project',redraw:()=>drawClaudeMd()})">${ic('ai')} AI analyze</button>
      <button class="btn sm" onclick="post('/api/open-editor',{file:CUR.path+'\\\\CLAUDE.md'})">${ic('edit')} Edit</button></h3>
      <p class="secthint">Claude reads this whole file on <b>every turn</b> of every session in this project — it is the one place an instruction is guaranteed to be seen, and the one place a wasted paragraph is paid for over and over.</p>
      <p class="secthint">It is not one document but several stacked in one file: your own prose, the regions you have fenced off, and the blocks claudectl regenerates. Each row says what it is, <b>who writes it</b>, what it costs, and the one button that rebuilds it.</p>
      ${md.exists?`
      <div id="cmBlocks">${blocks.map(b=>{
        const f=CMFIX[b.key]||null,w=CMWHAT[b.key]||['',''];
        const who=w[1]==='you'
          ?'<span class="tag ok" title="claudectl never rewrites this unprompted.">you write it</span>'
          :'<span class="tag" title="Rewritten by claudectl. Hand edits here are replaced on the next pass.">claudectl writes it</span>';
        return `
        <div class="agrow">
          <div class="agmain">
            <b>${b.present?ic('check'):'<span style="color:var(--dim2)">—</span>'} ${esc(b.label)}</b>
            ${who}${b.present?'':'<span class="tag" style="color:var(--dim2)">not present</span>'}
            ${b.key==='sessions'&&b.entries?`<span class="tag">${b.entries} entr${b.entries===1?'y':'ies'}</span>`:''}
            <div class="agdesc">${esc(w[0])}</div></div>
          <span class="num" style="min-width:70px;color:var(--dim2)">~${b.tokens||0} tok</span>
          ${f?`<button class="btn sm" title="${esc(f[2])}" onclick="${f[1]}">${esc(f[0])}</button>`:''}
        </div>${b.text?`<details style="margin:0 0 6px 12px"><summary style="cursor:pointer;color:var(--dim2);font-size:12px">show</summary>
          <div class="diff">${esc(b.text).split('\n').map(l=>`<div>${l}</div>`).join('')}</div></details>`:''}`;}).join('')}</div>
      <p style="color:var(--dim);font-size:12px;margin:10px 0 0">
        <b>~${md.tokens||0} tok</b> of this file is read on every turn of every session in this project.
        <code>loop.md</code> — what a bare <code>/loop</code> runs here — is edited on the
        <span class="hlink" onclick="go('loops')">Loops</span> page, beside the loops themselves.</p>
      <details style="margin-top:8px"><summary style="cursor:pointer;color:var(--dim2);font-size:12px">The whole file</summary>
        <div style="font:12px Consolas,monospace;white-space:pre-wrap;max-height:46vh;overflow-y:auto;background:var(--code);border-radius:8px;padding:12px;margin-top:6px">${esc(md.text)}</div></details>`
        :'<div class="empty">No CLAUDE.md yet — scaffold one.</div>'}</div>
    <div class="card"><h3>What else reaches this project <span class="sp"></span>
      <span style="color:var(--dim2);font-size:12px">the other files a session here loads</span></h3>
      <div class="lbl">Memory files map</div>
      ${(mm.files||[]).map(f=>`<div style="display:flex;gap:8px;padding:3px 0;align-items:center">
        <span style="width:18px">${f.exists?ic('check'):'—'}</span>
        <span style="flex:1">${esc(f.label)}</span>
        <span style="color:var(--dim2);font-size:11px">${esc(f.path)}</span>
        ${f.exists?`<button class="btn sm" onclick="post('/api/open-editor',{file:${hesc(f.path)}})">open</button>`:''}
      </div>${(f.imports||[]).map(im=>`<div class="agrow" style="margin-left:26px">
        <span style="flex:1;color:var(--dim);font-size:12px">@import ${esc(im.ref)}</span>
        ${im.exists?ic('check'):'<span class="tag warn" title="This @import points at a file that does not exist — Claude silently loads nothing for it.">missing</span>'}
      </div>`).join('')}`).join('')}
      <div class="lbl" style="margin-top:16px">System prompt</div>
      <div id="spBox"><span class="spin"></span></div></div>
    <div class="card"><h3>${ic('refresh')} History <span class="sp"></span>
      <span style="color:var(--dim2);font-size:12px">every version claudectl replaced</span></h3>
      <p style="color:var(--dim);font-size:13px;margin:0 0 8px">Compress and prune both snapshot what they were about to
        overwrite. Nothing claudectl shrinks is gone until it falls off the end of this list.
        Restoring is itself snapshotted, so you can walk back out again.</p>
      <div id="histOut"><span class="spin"></span></div></div>`))return;
  drawHistory(['claude_md','system_prompt']);
  const sp=await api('/api/system-prompt?'+qs(c));
  const b=$('#spBox');if(!b)return;   // navigated away during the second fetch
  b.innerHTML=`<div class="fld"><textarea id="spText">${esc(sp.text)}</textarea></div>
    <div class="mrow"><button class="btn pri sm" onclick="spSave()">Save</button></div>`;
}
async function cmScaffold(){
  await post('/api/claude-md/scaffold',C());toast('Scaffolded','ok');drawClaudeMd();}
/* Prune drops session entries past the cap. The TUI has always confirmed; the
   GUI destroyed silently. Same operation, so same contract — and the preview
   names what goes, because a token delta is not something you can check. */
async function cmPrune(redraw){
  const p=await api('/api/ctxaudit/prune-preview?'+qs(C()));
  if(!p.ok){toast(p.error||'Nothing to prune','err');return;}
  if(!p.changed){toast('Already pruned — nothing would change','ok');return;}
  const drop=(p.dropped||[]);
  const body=`~${p.old_tokens} → ~${p.new_tokens} tok every turn.`
    +(drop.length?`\n\nDrops ${drop.length} session entr${drop.length===1?'y':'ies'}:\n· `
      +drop.slice(0,12).join('\n· ')+(drop.length>12?`\n· …and ${drop.length-12} more`:'')
      +'\n\nThe transcripts themselves are untouched — only their lines in CLAUDE.md.'
     :'\n\nNo session entries are dropped.');
  if(!await confirmBox('Prune CLAUDE.md?',body))return;
  const r=await post('/api/ctxaudit/prune',C());
  if(!r.ok){toast(r.error||'Prune failed','err');return;}
  toast(`Pruned: ~${r.old_tokens} → ~${r.new_tokens} tok · undo from History`,'ok');
  (redraw||drawClaudeMd)();}
async function spSave(){
  await post('/api/system-prompt',{...C(),text:$('#spText').value});
  toast('System prompt saved','ok');}

/* audit tab — one question: what does a single turn cost me, across EVERY
   surface. That includes the ones no project tab should own: the global
   CLAUDE.md of each account, the system-prompt file, SessionStart hook
   injections and MCP tool schemas. The per-artifact detail that used to live
   here now sits on the tab that owns the artifact — CLAUDE.md blocks on the
   CLAUDE.md tab, rule globs on the Memory tab — so each row here links there
   instead of restating it. */
async function drawAudit(){
  const nav=paintNow('<div class="empty"><span class="spin"></span></div>');
  const c=C();
  const [d,deny]=await Promise.all([api('/api/ctxaudit?'+qs(c)),api('/api/deny?'+qs(c))]);
  const rows=(d.items||[]).map(it=>{
    const owner=it.label.startsWith('CLAUDE.md')?'claudemd'
               :it.label.startsWith('rule ')?'memory':'';
    return `
    <tr><td>${esc(it.label)} ${it.lazy?'<span class="tag" title="Loaded only when Claude opens a matching path.">lazy</span>':''}
      ${owner?`<span class="hlink" style="font-size:11px" onclick="TAB=${hesc(owner)};drawProject()">detail →</span>`:''}</td>
    <td class="num">${it.tokens==null?'<span title="Not knowable before the session starts.">~?</span>':it.tokens}</td>
    <td style="color:var(--warn);font-size:12px">${esc((it.warnings||[]).join(' · '))}</td>
    <td style="white-space:nowrap">${it.path?`<button class="btn sm" onclick="post('/api/open-editor',{file:${hesc(it.path)}})">open</button>`:''}</td></tr>`;}).join('');
  if(!paint(nav,`
    <div class="card"><h3>Context weight — ~${d.total||0} tok loaded every turn
      <span class="sp"></span>
      <button class="btn sm" onclick="cmPrune(drawAudit)">${ic('cut')} Prune sessions</button>
      <button class="btn sm" onclick="post('/api/ctxaudit/compact',{path:CUR.path}).then(()=>{toast('Compact section added','ok');drawAudit()})">${ic('add')} Compact instructions</button></h3>
      <p style="color:var(--dim);font-size:12px;margin:0 0 8px">Every surface a session loads, project-scoped and account-scoped together. <b>lazy</b> rows cost nothing until Claude opens a matching path, so they are not in the total.</p>
      <table class="tbl"><tr><th>item</th><th>tokens</th><th>warnings</th><th></th></tr>${rows}</table></div>
    <div class="card"><h3>Deny rules (token-heavy paths) <span class="sp"></span>
      <button class="btn sm pri" onclick="post('/api/deny/apply',{path:CUR.path}).then(r=>{toast(r.added+' added, '+r.existed+' existed','ok');drawAudit()})">Apply all</button></h3>
      ${(deny.patterns||[]).map(p=>`<div style="display:flex;gap:10px;padding:2px 0">
        <code style="color:var(--cyan)">${esc(p.pattern)}</code>
        <span style="color:var(--dim);font-size:12px">${esc(p.why)}</span></div>`).join('')
      ||'<div class="empty">Nothing heavy found.</div>'}</div>`))return;
}
async function cmProtect(){
  const v=await ask('Protect a section of CLAUDE.md',
    [{label:'Text from the section to keep',ph:'e.g. Deployment checklist'}],
    'The heading it sits under, and everything down to the next heading, is '
    +'fenced. AI compression never sees fenced text, so it cannot reword, '
    +'shorten or drop it.');
  if(v===null||!v[0].trim())return;
  const r=await post('/api/ctxaudit/protect',{path:CUR.path,text:v[0].trim()});
  toast(r.ok?'Section protected':(r.error||'Failed'),r.ok?'ok':'err');
  if(r.ok)drawClaudeMd();     // the fence is a CLAUDE.md block, shown there
}
/* One renderer, three consumers: the Memory tab owns memory_graph, the
   CLAUDE.md tab owns claude_md + system_prompt. `only` filters by key so
   history sits beside the artifact it is history OF, instead of in a third tab
   that has to explain which of the three it is showing. */
/* Two ways a version can be summarised, and the graph needs the second one.
   `+27808 -27783` is what a re-serialised 300 KB JSON reports whether one fact
   changed or every one did — a true number that answers nothing. For the graph
   the summary is its SHAPE, which is what you would restore for. */
const HIST_SHOWN=4;
function histSum(k,v,now){
  if(v.shape){const s=v.shape,d=(a,b)=>{const n=(b||0)-(a||0);
      return n?`<span style="color:var(--${n>0?'ok':'err'})">${n>0?'+':''}${n}</span>`:'';};
    return `${s.entities} facts · ${s.relations} links · ${s.lessons} lessons`
      +(now?` <span style="color:var(--dim2)">(now ${now.entities}${d(s.entities,now.entities)}`
        +`, ${now.lessons}${d(s.lessons,now.lessons)} lessons)</span>`:'');}
  return `<span style="color:var(--ok)">+${v.added}</span> <span style="color:var(--err)">-${v.removed}</span>`;
}
function histRows(k,vs){
  return vs.map(v=>`<div class="agrow">
      <span style="min-width:70px;color:var(--dim)">${esc(v.age)}</span>
      <span style="flex:1;font-size:12px">${histSum(k.key,v,k.now)}</span>
      <button class="btn sm" onclick="histDiff(${hesc(k.key)},${v.ts})">what changed</button>
      <button class="btn sm pri" onclick="histRestore(${hesc(k.key)},${v.ts},${hesc(k.title)},${hesc(v.age)})">restore</button>
      </div>`).join('');
}
async function drawHistory(only){
  const d=await api('/api/history?'+qs(C()));const b=$('#histOut');if(!b)return;
  // 12 versions per key is the right thing to KEEP and the wrong thing to
  // show — a wall of near-identical rows buries the recent one you want.
  const rows=(d.keys||[]).filter(k=>(!only||only.includes(k.key))&&(k.versions||[]).length).map(k=>{
    const vs=k.versions,head=vs.slice(0,HIST_SHOWN),rest=vs.slice(HIST_SHOWN);
    return `<div class="lbl" style="margin-top:8px">${esc(k.title)}</div>`
      +histRows(k,head)
      +(rest.length?`<details><summary style="cursor:pointer;color:var(--dim2);font-size:12px;padding:4px 0">${rest.length} older version(s)</summary>${histRows(k,rest)}</details>`:'');
  }).join('');
  b.innerHTML=rows||'<div class="empty">No version has been replaced yet — nothing to roll back to.</div>';
}
async function histDiff(key,ts){
  const d=await api('/api/history/diff?'+qs({...C(),key,ts}));
  $('#dTitle').textContent=(d.title||key)+' — what this restore would bring back';
  $('#dBody').innerHTML=`<div class="diff">${(d.diff||[]).map(l=>
    `<div class="${l.startsWith('+')&&!l.startsWith('+++')?'a':
       (l.startsWith('-')&&!l.startsWith('---')?'d':
       (l.startsWith('@@')?'h':''))}">${esc(l)}</div>`).join('')
    ||'<div class="empty">Identical to what is on disk now.</div>'}</div>`;
  $('#drawer').classList.add('show');
}
async function histRestore(key,ts,title,age){
  if(!await confirmBox(`Restore ${title}?`,
    `Puts back the version from ${age}. What is on disk now is snapshotted first, `
    +`so this is reversible too.`))return;
  const r=await post('/api/history/restore',{...C(),key,ts});
  toast(r.ok?(r.message||'Restored'):(r.error||'Restore failed'),r.ok?'ok':'err');
  if(r.ok)drawProject();     // history now lives on two tabs — redraw whichever
}

/* project usage tab */
async function drawProjUsage(){
  const nav=paintNow('<div class="empty"><span class="spin"></span> Crunching…</div>');
  const d=await api('/api/usage/project?'+qs(C()));
  const ss=d.sessions||[],spend=ss.reduce((a,r)=>a+(r.cost||0),0);
  const rows=(d.sessions||[]).map(r=>`
    <tr><td>${esc(r.age)}</td><td>${esc(r.name)} ${r.account&&r.account!=='default'?`<span class="tag">${esc(r.account)}</span>`:''}</td>
    <td class="num">${r.msgs}</td><td class="num">${r.usage.in}</td>
    <td class="num">${r.usage.out}</td><td class="num">${r.exact?'':'~'}$${r.cost.toFixed(2)}</td></tr>`).join('');
  paint(nav,`<div class="card"><h3>Per-session usage</h3>
    <table class="tbl"><tr><th>age</th><th>session</th><th>msgs</th><th>in</th><th>out</th><th>est.$</th></tr>${rows}</table></div>`);
}

/* tools tab */
function drawTools(){
  paintNow(`
    <div class="card"><h3>${ic('check')} Project health</h3>
      <p style="color:var(--dim);font-size:13px;margin-bottom:10px">The checks the TUI's workspace screen runs — they had no GUI trace at all.</p>
      <div id="hOut"><span class="spin"></span></div></div>
    <div class="card"><h3>${ic('ai')} What to work on <span class="sp"></span>
      <button class="btn sm" title="One Claude call over the memory graph, recorded lessons and recent diff — returns bugs, vulnerabilities, slow paths and functions worth building. Findings are stored, so this list stays instant afterwards."
        onclick="inlineJob('#jban','work_scan',C(),{label:'Scanning for work',onDone:st=>toast(((st.result||{}).items||[]).length+' finding(s)','ok'),redraw:()=>drawBrief()})">${ic('ai')} Find work</button></h3>
      <p style="color:var(--dim);font-size:12px;margin:2px 0 8px">Free signals — stale context, TODO markers, deferred shortcuts, untested modules — are always listed. Press <b>Find work</b> to add what a model can see that a grep cannot.</p>
      <div id="bOut"><span class="spin"></span></div></div>
    <div class="card"><h3>${ic('map')} Architecture <span class="sp"></span>
      <button class="btn sm" onclick="archRefresh()">${ic('refresh')} Rebuild</button>
      <button class="btn sm" onclick="window.open('/graph?${qs({path:CUR.path,enc:CUR.encoded,k:CK})}','_blank')">Graph ${ic('ext')}</button></h3>
      <p style="color:var(--dim);font-size:13px;margin-bottom:10px">What the terminal UI's architecture screen shows. The endpoint behind it existed and had no consumer at all.</p>
      <div id="archOut"><span class="spin"></span></div></div>
    <div class="card"><h3>${ic('ai')} Claude Code's own record</h3>
      <p style="color:var(--dim);font-size:13px;margin-bottom:10px">What Claude Code itself has stored about this project in <code>.claude.json</code> — not claudectl's numbers.</p>
      <div id="ccProj"><span class="spin"></span></div></div>
    <div class="card"><h3>${ic('robot')} Project agents</h3><div id="agSel"></div></div>
    <div class="card"><h3>${ic('terminal')} Extra PATH entries</h3>
      <p style="color:var(--dim);font-size:13px;margin-bottom:10px">Directories prepended to PATH for every launch of this project.</p>
      <div id="xpaths"></div>
      <div class="mrow"><button class="btn sm" onclick="dirAdd('xpaths')">${ic('add')} Add entry</button>
        <button class="btn pri sm" onclick="savePaths()">Save</button></div></div>
    <div class="card"><h3>${ic('newfolder')} Add directories</h3>
      <p style="color:var(--dim);font-size:13px;margin-bottom:10px">Extra working directories passed as <code>--add-dir</code> on every launch.</p>
      <div id="xdirs"></div>
      <div class="mrow"><button class="btn sm" onclick="dirAdd('xdirs')">${ic('add')} Add directory</button>
        <button class="btn pri sm" onclick="saveDirs()">Save</button></div></div>`);
  drawAgentPicker();
  drawArch(false);
  /* Read the keys Claude Code actually writes — camelCase, and every number is
     about its LAST session, not a lifetime total. Naming them otherwise is the
     mistake CLAUDE.md records for `context_used_pct`: a field nobody sends. */
  api('/api/client/project?'+qs(Object.assign({path:CUR.path},C()))).then(d=>{
    const b=$('#ccProj');if(!b)return;
    const st=d.state||{};
    const mins=v=>v?Math.round(v/60000)+' min':null;
    const rows=[
      ['Last session',st.lastSessionId?String(st.lastSessionId).slice(0,8):null],
      ['Last cost',st.lastCost!=null?'$'+(+st.lastCost).toFixed(2):null],
      ['Last duration',mins(st.lastDuration)],
      ['Lines added / removed',(st.lastLinesAdded!=null||st.lastLinesRemoved!=null)
        ?`+${st.lastLinesAdded||0} / -${st.lastLinesRemoved||0}`:null],
      ['Tokens in / out',(st.lastTotalInputTokens!=null||st.lastTotalOutputTokens!=null)
        ?`${st.lastTotalInputTokens||0} / ${st.lastTotalOutputTokens||0}`:null],
      ['Allowed tools',(st.allowedTools||[]).join(', ')],
      ['MCP servers enabled',(st.enabledMcpjsonServers||[]).join(', ')],
      ['MCP servers disabled',(st.disabledMcpjsonServers||[]).join(', ')],
      ['Trust dialog accepted',st.hasTrustDialogAccepted==null?null:(st.hasTrustDialogAccepted?'yes':'no')],
    ].filter(([,v])=>v!==null&&v!==undefined&&v!=='');
    b.innerHTML=(d.known&&rows.length)?`<div class="kv">${rows.map(([k,v])=>
      `<span class="k">${esc(k)}</span><span>${esc(String(v))}</span>`).join('')}</div>`
      :'<div class="empty">Claude Code has recorded nothing for this project yet.</div>';});
  api('/api/extra-paths?'+qs(C())).then(d=>dirRows('xpaths',d.paths||[]));
  api('/api/add-dirs?'+qs(C())).then(d=>dirRows('xdirs',d.dirs||[]));
  api('/api/health?'+qs(C())).then(d=>{const h=$('#hOut');if(!h)return;
    const iss=(d.issues||[]),bash=(d.bash||[]);
    h.innerHTML=(iss.length
      ?iss.map(i=>`<div class="lrow"><span class="tag ${i.severity==='warn'?'warn':''}">${esc(i.severity)}</span>
          <div><b>${esc(i.message)}</b><div style="color:var(--dim);font-size:12px">${esc(i.hint||'')}</div></div></div>`).join('')
      :'<div class="empty">No issues found.</div>')
      // one chip per command with its count, instead of a dot-separated run of
      // monospace text that reads as a single unbreakable line
      +(bash.length?`<div class="sect">
        <div class="secth"><h4>Most-run commands</h4>
          <button class="btn sm" onclick="allowlistApply()">Add to permissions</button></div>
        <div class="secthint">Allowlist candidates, taken from what this project actually ran.</div>
        <div class="cmdchips">${bash.map(b=>
          `<span class="cmdchip"><b>${esc(b.command)}</b><i>${+b.count}</i></span>`).join('')}</div>
        </div>`:'');});
  drawBrief();
}
/* tags that mean "something is wrong" get the warn colour; the rest are
   neutral, so a list of ideas does not read as a list of alarms */
const BRIEF_ALERT={vuln:1,bug:1,fix:1,health:1,stale:1};
/* Only a scan finding is dismissible. A local item (a stale check, a TODO, an
   untested module) disappears when you FIX it — offering to hide it instead
   would let the list quietly lie about the state of the project. */
const BRIEF_SCAN={vuln:1,bug:1,perf:1,idea:1};
async function drawBrief(){
  const d=await api('/api/brief?'+qs(C()));const b=$('#bOut');if(!b)return;
  const sug=(d.suggestions||[]);
  b.innerHTML=(sug.length
    ?sug.map(s=>`<div class="lrow">
        <span class="tag${BRIEF_ALERT[s.tag]?' warn':''}">${esc(s.tag)}</span>
        <div class="clamp3">${esc(s.text)}</div>
        ${BRIEF_SCAN[s.tag]?`<button class="btn sm" title="Stop showing this"
          onclick="briefDismiss(${hesc(s.text)})">${ic('close')}</button>`:''}</div>`).join('')
    :'<div class="empty">Nothing to suggest yet — press Find work, or build memory first.</div>')
    +(d.scan_at?`<div style="color:var(--dim2);font-size:11px;margin-top:6px">last scan ${esc(d.scan_at)}</div>`:'')
    +sinceHtml(d);
}
async function briefDismiss(text){
  await post('/api/brief/dismiss',{...C(),text});drawBrief();
}
function archRefresh(){drawArch(true);}
async function drawArch(force){
  const el=$('#archOut');if(!el)return;
  el.innerHTML='<span class="spin"></span>';
  const d=await api('/api/graph-lite?'+qs(Object.assign({},C(),{path:CUR.path},
    force?{refresh:'1'}:{})));
  if(!$('#archOut'))return;
  /* connections._language_breakdown returns sorted `counts.items()` — a LIST of
     [name, count] pairs, not an object. Object.entries over an array yields
     index/value, which rendered as "Python,171 0". */
  const raw=d.languages||[];
  const langs=Array.isArray(raw)?raw:Object.entries(raw);
  const nums=[['Files',d.files],['Dirs',d.dirs],['Repos',d.repos],['Dependencies',d.deps]];
  const tops=(d.top_repos||[]);
  $('#archOut').innerHTML=`<div class="kv">
      ${nums.map(([k,v])=>`<span class="k">${k}</span><span>${(+v)||0}</span>`).join('')}
      <span class="k">Languages</span><span>${langs.length
        ?esc(langs.slice(0,6).map(([name,count])=>`${name} ${count}`).join('   ')):'?'}</span></div>
    ${d.truncated?'<div class="tag warn" style="margin-top:8px">large project — capped for display (the whole tree is still cached)</div>':''}
    ${tops.length?`<table class="tbl" style="margin-top:10px"><tr><th>top project</th><th class="num">files</th><th class="num">deps</th></tr>
      ${tops.map(r=>`<tr><td>${esc(r.label)}</td><td class="num">${r.files}</td><td class="num">${r.deps}</td></tr>`).join('')}</table>`:''}`;
}

/* "Since your last session" was a single pre-wrapped blob of every commit in
   every sub-repo — on a workspace of 8 repos it was most of the page and told
   you nothing at a glance. One collapsible row per repo, summarised by counts,
   and only the repo you open costs you any reading. */
function sinceHtml(d){
  const s=d.since||{};
  const repos=s.repos||[];
  if(!repos.length){
    const note=s.note||((d.since_last||[]).join('\n'));
    return note?`<div class="sect"><div class="secth"><h4>Since your last session</h4></div>
      <div class="empty">${esc(note)}</div></div>`:'';
  }
  const tot=repos.reduce((n,r)=>n+r.commits.length,0);
  const dirty=repos.reduce((n,r)=>n+(r.dirty?1:0),0);
  return `<div class="sect"><div class="secth"><h4>Since your last session</h4>
      <span class="secttag">${tot} commit${tot===1?'':'s'} · ${repos.length} repo${repos.length===1?'':'s'}${dirty?` · ${dirty} dirty`:''}</span>
      <button class="btn sm icon" title="Re-read git in every repo" onclick="briefReload()">${ic('refresh')}</button></div>
    ${repos.map(r=>`<details class="rgrp"${repos.length===1?' open':''}>
      <summary><span class="rn">${esc(r.label||'this repo')}</span>
        <span class="rc">${r.commits.length} commit${r.commits.length===1?'':'s'}</span>
        ${r.dirty?`<span class="tag warn">${r.dirty} uncommitted</span>`:''}</summary>
      ${r.commits.length?`<div class="clog">${r.commits.map(c=>{
        const sp=c.indexOf(' ');
        return `<div><code>${esc(sp>0?c.slice(0,sp):c)}</code>${esc(sp>0?c.slice(sp):'')}</div>`;
      }).join('')}</div>`:'<div class="empty">No commits — working tree changes only.</div>'}
    </details>`).join('')}</div>`;
}
/* The cached read is what makes reopening the tab instant; this is the way to
   ask for a fresh one, so the cache is never something you cannot get past. */
async function briefReload(){
  const b=$('#bOut');if(b)b.innerHTML='<span class="spin"></span>';
  const d=await api('/api/brief?'+qs(C())+'&refresh=1');
  if(!$('#bOut'))return;                     // navigated away mid-fetch
  const sug=(d.suggestions||[]);
  $('#bOut').innerHTML=(sug.length
    ?sug.map(s=>`<div class="lrow"><span class="tag">${esc(s.tag)}</span><div class="clamp3">${esc(s.text)}</div></div>`).join('')
    :'<div class="empty">Nothing to suggest yet.</div>')+sinceHtml(d);
}
async function allowlistApply(){
  const r=await post('/api/health/allowlist',C());
  toast(r.ok?`Added ${r.added} rule(s)`:(r.error||'Failed'),r.ok?'ok':'err');
  drawTools();
}
/* one input row per directory */
function _dirRow(v){return `<div class="lrow"><input value="${esc(v)}" placeholder="C:\\path\\to\\dir">
  <button class="btn sm danger" title="Remove" onclick="this.parentElement.remove()">${ic('del')}</button></div>`;}
function dirRows(id,items){$('#'+id).innerHTML=(items.length?items:['']).map(_dirRow).join('');}
function dirAdd(id){$('#'+id).insertAdjacentHTML('beforeend',_dirRow(''));
  const inp=$('#'+id).lastElementChild.querySelector('input');if(inp)inp.focus();}
function dirVals(id){return [...document.querySelectorAll('#'+id+' input')].map(i=>i.value);}
async function savePaths(){
  await post('/api/extra-paths',{...C(),paths:dirVals('xpaths')});
  toast('Extra PATH saved','ok');}
async function saveDirs(){
  await post('/api/add-dirs',{...C(),dirs:dirVals('xdirs')});
  toast('Add directories saved','ok');}
/* ── Hand off ──
   Was a card in the Tools tab that asked you to pick a source session out of a
   dropdown, then an account. It is a session-row action now, beside Fork: the
   row IS the source, so the first step disappears. Nothing is lost with it —
   `context_inject.find_sessions_across_accounts(path)` and
   `gui.list_sessions(enc)` return the same per-project, cross-account set, which
   is what that dropdown was listing.

   The docs and the site already call this "context hand-off", so the button is
   named after the thing rather than after a new word. */
async function handoffS(i){
  const s=SESS[i];
  const v=await ask('Hand off — '+(s.title||s.sid.slice(0,8)),
    [{label:'Start the new chat under account',type:'select',
      options:ST.accounts.map(a=>[a.dir,a.name])}],
    "A new chat opens in this project, seeded with this session's transcript as "
    +'context. The transcript is written to .claudectl/injected-context.md and '
    +'the new session is told to read it first.');
  if(v===null)return;
  /* cfgdir + enc, not a folder path: the server derives the source folder
     through the same validated `_folder()` every other endpoint uses. It used to
     take an absolute `folder` off the wire and join it. */
  const r=await post('/api/inject/launch',{path:CUR.path,enc:CUR.encoded,
    cfgdir:s.cfgdir,sid:s.sid,account:s.account,target_cfgdir:v[0]});
  toast(r.ok?'New chat launched with this session as context':'Failed: '+(r.error||''),
    r.ok?'ok':'err');
}
/* ── Plan → Execute — own project tab ──
   Two-model workflow: a strong model plans once (headless, no tools), you
   approve it, then a cheaper — or free, via OmniRoute — model executes it
   in a real interactive `claude` session launched with cwd=project, which
   is what makes it auto-discover this project's selected agents/skills
   (Claude Code reads .claude/agents & .claude/skills from cwd; both are
   already synced there when you select them elsewhere in the GUI) and this
   project's own system prompt/add-dirs — nothing about the session itself
   is different, only which model and account it runs against. */
/* ── plan-exec: non-blocking background runner ──
   runJob() opens the full-screen #jovl modal and ties progress to its DOM, so
   the whole UI is blocked until the job ends (and the animated modal over the
   dimmed page was the flicker source). Plan jobs don't need any of that: the
   daemon-thread job already survives navigation (gui_api _JOBS), and none of
   the plan_* kinds use an approval gate. So we just poll module state, render
   an inline banner into the plan-exec page while it's mounted, and toast on
   completion no matter which page the user wandered to. */
let PE={jid:null,kind:'',label:'',status:'',msgs:[],elapsed:0,plan:null,lastError:''};
let __peShown=false,__peSub='',__peMsgs='';
let peLastChange=0,peLastSig='';    // stall watchdog: last poll where anything changed
function peBusy(){return !!PE.jid&&PE.status==='running';}
function peStalled(){return peBusy()&&peLastChange&&(Date.now()-peLastChange)>120000;}
async function peJobStart(kind,params,label){
  if(peBusy()){toast('A plan job is already running — cancel it first','err');return;}
  const r=await post('/api/job',{kind,...params});
  if(!r.ok){toast(r.error||'Could not start','err');return;}
  PE={jid:r.job,kind,label,status:'running',msgs:[],elapsed:0,plan:PE.plan,lastError:''};
  __peShown=false;   // force a fresh banner build
  peLastChange=0;peLastSig='';      // fresh baseline — don't inherit the last job's stall
  peRenderStatus();
  pePoll();
}
async function pePoll(){
  const jid=PE.jid;if(!jid)return;
  if(document.hidden||!_VIS)return;        // finally-block re-arms the chain
  let terminal=false;
  try{
    let st=null;
    try{ st=await api(`/api/job/${jid}`); }
    catch(e){ st=null; }            // transient network/backend hiccup — retry next tick
    if(PE.jid!==jid)return;          // superseded by a newer job, or cleared
    if(!st){ peRenderStatus(); return; }  // fetch failed: keep the banner, reschedule below
    PE.status=st.status;PE.label=st.label||PE.label;
    PE.msgs=st.messages||[];PE.elapsed=st.elapsed||0;
    // stall watchdog: status/label/messages changing = progress (elapsed alone
    // ticks every second and is NOT progress)
    const sig=`${st.status}|${st.label}|${(st.messages||[]).slice(-3)
      .map(m=>(m.ok?'1':'0')+m.text).join('\u0001')}`;
    if(sig!==peLastSig){peLastSig=sig;peLastChange=Date.now();}
    if(st.status==='running'){peRenderStatus();return;}
    terminal=true;
    if(st.status==='done')peJobDone(st.result||{});
    else{
      if(st.status==='cancelled')toast('Plan job cancelled','');
      else{PE.lastError=st.error||'Plan job failed';toast(PE.lastError,'err');}
      // A failed/cancelled LAUNCH (e.g. a stale/unavailable OmniRoute model)
      // shouldn't force replanning -- the plan is already safely on disk
      // (write_plan_file runs before the exec-model check) and still held in
      // PE.plan since peExecute() no longer discards it up front. Re-open the
      // editor so the user can just retry, e.g. after switching Execute
      // via/model, instead of regenerating the plan from scratch.
      if(PE.kind==='plan_launch'&&PE.plan&&PAGE_==='project'&&TAB==='planexec')peShowPlan(PE.plan);
    }
    peRenderStatus();   // clears the inline banner
  }catch(e){
    // never let a render error kill the poll chain either
  }finally{
    // Reschedule on BOTH the success and the error path — a dead chain is the
    // bug. Stop only on a terminal status or when a newer job supersedes us.
    // a plan job can run for minutes; polling it at 700ms the whole way is
    // pure waste once it's clearly long-running
    if(!terminal&&PE.jid===jid)
      setTimeout(pePoll,PE.elapsed>60?4000:PE.elapsed>15?1500:700);
  }
}
function peJobDone(result){
  const onPage=PAGE_==='project'&&TAB==='planexec';
  if(PE.kind==='plan_make'||PE.kind==='plan_replan'){
    if(!result.plan){toast('Plan came back empty','err');return;}
    PE.plan=result.plan;
    toast('Plan ready — review & execute'+(onPage?'':' (open the Plan → Execute tab)'),'ok');
    if(onPage)peShowPlan(result.plan);
  }else if(PE.kind==='plan_launch'){
    PE.plan=null;   // launch actually succeeded -- now safe to consume
    toast(`Execute session launched — ${esc(result.model||'')} via ${result.via==='omniroute'?'OmniRoute':'Anthropic'}`,'ok');
  }
}
async function peCancel(){
  if(!PE.jid)return;
  await post(`/api/job/${PE.jid}/cancel`);
  toast('Cancelling…','');
}
/* the plan-exec gauge: alive while a plan job runs, flat when idle. `beats`
   carries the step count once a plan exists, so a long execute reads as "there
   is a real amount of work here" rather than just "busy". */
function peFeed(){
  const steps=((PE.plan||{}).steps)||[];
  const run=peBusy();
  INST.set('planexec',{v:run?1:0,beats:run?Math.min(6,steps.length||1):0,
    series:steps.map((x,i)=>i+1)});
  setRead('planexec',steps.length);
}
function peDismissError(){PE.status='';PE.lastError='';peRenderStatus();}
/* Renders the inline running-banner. No-op when off the plan-exec page (the
   poll chain keeps ticking regardless). Caches sub-text/messages so a poll
   tick only rewrites what changed — never recreates the animated spinner. */
function peRenderStatus(){
  peFeed();
  const el=$('#peStatus');if(!el)return;
  if(peBusy()){
    if(!__peShown){
      __peShown=true;__peSub='';__peMsgs='';el.style.display='';
      el.innerHTML=`<div class="perun beam"><span class="spin"></span>
        <div style="flex:1;min-width:0"><b id="peLbl"></b>
          <div class="sub" id="peSub"></div><div class="msgs" id="peMsgs"></div></div>
        <button class="btn sm danger" onclick="peCancel()">Cancel</button></div>`;
      $('#peLbl').textContent=PE.label||'Working…';
    }
    // stall watchdog: 2min with no status/label/message change while running —
    // hint that upstream is wedged and surface the Cancel path
    const sub=peStalled()
      ? `${PE.elapsed||0}s elapsed — still running, NO progress for 2m (upstream may be stuck). Click Cancel to stop.`
      : `${PE.elapsed||0}s elapsed — keep using claudectl; you'll be notified when it's done`;
    if(sub!==__peSub){__peSub=sub;const s=$('#peSub');if(s)s.textContent=sub;}
    const msgs=(PE.msgs||[]).slice(-3).map(m=>`<div class="${m.ok?'':'bad'}">${esc(m.text)}</div>`).join('');
    if(msgs!==__peMsgs){__peMsgs=msgs;const m=$('#peMsgs');if(m)m.innerHTML=msgs;}
  }else if(PE.status==='error'&&PE.lastError){
    // a plan job can run for minutes; a 3.5s toast is not enough to report why
    // it died, so the failure stays on the page until dismissed
    __peShown=false;el.style.display='';
    el.innerHTML=`<div class="perun err">${ic('close')}
      <div style="flex:1"><b>Plan job failed</b>
        <div class="sub">${esc(PE.lastError)}</div></div>
      <button class="btn sm" onclick="peDismissError()">Dismiss</button></div>`;
  }else if(__peShown){
    __peShown=false;el.style.display='none';el.innerHTML='';
  }
}
function drawPlanExec(){
  const o=ST.options;
  paintNow(`
    <div id="peStatus" style="display:none;margin-bottom:14px"></div>
    <div class="card"><h3>${ic('map')} Plan → Execute <span class="sp"></span>
      ${INST.html('eq','planexec',{fmt:'int',sub:'steps'})}</h3>
      <p style="color:var(--dim);font-size:13px;margin-bottom:10px">
        Cuts cost on multi-step work: a strong model writes a numbered plan (no file writes, no tool
        calls — it can't go off script), you review it, then execution runs as a normal full
        interactive session — same agents, skills, system prompt, and add-dirs this project already
        has — just possibly on a cheaper or completely free model.</p>
      <div class="fld"><label>Task</label><textarea id="peTask" placeholder="Describe what to build or fix…"></textarea></div>
      <div class="grid2">${fld('pePlan','Plan model')}${fld('peEff','Plan effort')}</div>
      <div class="fld"><label>Execute via</label><div class="chips" id="peVia">
        <span class="chip on" data-v="anthropic">Anthropic</span>
        <span class="chip" data-v="omniroute">OmniRoute (free)</span></div></div>
      <div id="peExecWrap"></div>
      <div class="fld"><label>Model council</label><div class="chips" id="peCouncil">
        <span class="chip" data-v="1">Optimize plan with council</span></div>
        <div style="color:var(--dim);font-size:12px;margin-top:2px">Runs the draft past extra models for critique before you review it — costs more tokens.</div></div>
      ${ST.accounts.length>1?`<div class="fld"><label>Account (plan &amp; execute)</label><div class="chips" id="peAcct"></div></div>`:''}
      <div class="mrow"><button class="btn pri" onclick="peRun()">${ic('play')} Write the plan…</button>
        <button class="btn sm" onclick="peUseExisting()" title="Skip planning — paste a plan you already have and go straight to review/execute">${ic('doc')} Already have a plan?</button>
        <span id="peLastPlanHint"></span></div></div>
    <div class="card" id="peEditCard" style="display:none"><h3>${ic('edit')} Edit plan <span class="sp"></span>
      <label style="font-size:13px;font-weight:400;display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="pePerStep"> Per-step approval</label>
      <span style="flex:1"></span>
      <button class="btn sm" onclick="peReplan()">${ic('refresh')} Re-plan</button></h3>
      <div class="fld"><label>Plan text — edit then approve</label>
        <textarea id="pePlanEdit" style="min-height:240px;font-family:var(--mono);font-size:12px"></textarea></div>
      <div class="mrow">
        <button class="btn" id="peDiscardBtn">Discard</button>
        <button class="btn pri" id="peApproveBtn">${ic('play')} Approve &amp; execute</button>
      </div></div>
    <div class="card"><h3>How it works</h3>
      <ol style="color:var(--dim);font-size:13px;padding-left:18px;display:flex;flex-direction:column;gap:5px;margin:0">
        <li>The plan model reasons about the task and writes numbered steps — headless, read-only, no file writes.</li>
        <li>You review the plan before anything executes and can reject it with nothing launched.</li>
        <li>Approved plan is saved to <code>.claudectl/plan-latest.md</code> in this project.</li>
        <li>A real interactive <code>claude</code> session opens in a new console, pointed at the plan file — with this project's usual agents, skills, system prompt, and add-dirs already in place.</li>
        <li>Executing via OmniRoute: it auto-starts in the background if it isn't already running (no terminal to babysit), and on <i>Auto</i> it picks the best free model per request, falling back automatically if one is rate-limited or exhausted.</li>
        <li>Model council (optional): the draft plan is critiqued by a small set of other models, then merged into one improved plan before you see it for approval.</li>
      </ol></div>`);
  chipsFill($('#pePlan'),o.models,o.model_labels,ST.plan_model||'');
  chipsFill($('#peEff'),o.efforts,null,'xhigh');
  $('#peVia').querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
    $('#peVia').querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));
    c.classList.add('on');peViaChange();});
  $('#peCouncil').querySelectorAll('.chip').forEach(c=>c.onclick=()=>c.classList.toggle('on'));
  if(ST.accounts.length>1) chipsFill($('#peAcct'),ST.accounts.map(a=>a.dir),ST.accounts.map(a=>a.name),ST.active_cfgdir);
  peViaChange();
  // Re-navigating to this tab rebuilds the whole card and would otherwise
  // silently reset via/model/account back to defaults, discarding a choice
  // already captured for the plan currently in flight or awaiting approval.
  const savedCfg=window._peExecCfg;
  if(savedCfg&&savedCfg.via){
    chipSet($('#peVia'),savedCfg.via);
    peViaChange();
    if(savedCfg.via!=='omniroute')chipSet($('#peExec'),savedCfg.execModel||'');
  }
  if(savedCfg&&ST.accounts.length>1)chipSet($('#peAcct'),savedCfg.account||'');
  // Restore any in-flight or just-finished plan work when returning to the tab:
  // a running job re-shows its inline banner, a finished-but-unreviewed plan
  // re-opens the editor. The background poll chain kept running the whole time.
  __peShown=false;
  peRenderStatus();
  if(!peBusy()&&PE.plan)peShowPlan(PE.plan);
  peLoadLastPlanHint();
}
let __peLastPlan=null;
async function peLoadLastPlanHint(){
  const hint=$('#peLastPlanHint');if(!hint)return;
  const r=await api('/api/plan/last?'+qs({path:CUR.path}));
  __peLastPlan=r&&r.exists?r:null;
  hint.innerHTML=__peLastPlan
    ?`<button class="btn sm" onclick="peResumeLastPlan()">${ic('history')} Resume last plan — "${esc((__peLastPlan.task||'(untitled)').slice(0,50))}"</button>`
    :'';
}
function peResumeLastPlan(){
  if(!__peLastPlan)return;
  $('#peTask').value=__peLastPlan.task||'';
  window._peTask=__peLastPlan.task||'';
  window._peExecCfg={via:chipVal($('#peVia')),execModel:$('#peExec')?chipVal($('#peExec')):'',
                     account:$('#peAcct')?chipVal($('#peAcct')):''};
  PE.plan=__peLastPlan.plan;
  peShowPlan(__peLastPlan.plan);
}
function peUseExisting(){
  const task=($('#peTask').value||'').trim();
  if(!task){toast('Give the task a short title first','err');return;}
  window._peTask=task;
  window._peExecCfg={via:chipVal($('#peVia')),execModel:$('#peExec')?chipVal($('#peExec')):'',
                     account:$('#peAcct')?chipVal($('#peAcct')):''};
  PE.plan=null;   // nothing to restore until they submit — peExecute() sets it then
  peShowPlan('');
}
function peViaChange(){
  const via=chipVal($('#peVia'));
  const wrap=$('#peExecWrap');
  if(via==='omniroute'){
    wrap.innerHTML=`<div style="color:var(--dim);font-size:12px;margin:2px 0 8px">
      Best free model auto-selected by OmniRoute, with automatic fallback. Endpoint configured under
      <span style="color:var(--cyan);cursor:pointer" onclick="go('settings')">Settings</span>.</div>`;
  } else {
    wrap.innerHTML=fld('peExec','Execute model');
    chipsFill($('#peExec'),ST.options.models,ST.options.model_labels,ST.exec_model||'');
  }
}
async function peRun(){
  const task=($('#peTask').value||'').trim();
  if(!task){toast('Describe the task first','err');return;}
  const council=!!$('#peCouncil').querySelector('.chip.on');
  const via=chipVal($('#peVia'));
  const model=chipVal($('#pePlan'));
  const execEl=$('#peExec'),acctEl=$('#peAcct');
  const account=acctEl?chipVal(acctEl):'';
  // account rides along on plan_make too — the plan call itself must run
  // under the chosen account, not just the later execute launch, else a
  // second account's plan-model access/limits are never actually used.
  const body={...C(),task,model,effort:chipVal($('#peEff')),council,via,account};
  window._peTask=task;
  // Capture the execute-side config NOW while the chips are on-screen — the
  // plan may finish while the user is on another page, so peShowPlan can't
  // rely on the DOM still holding these values.
  window._peExecCfg={via,execModel:execEl?chipVal(execEl):'',account};
  peJobStart('plan_make',body,`Writing plan (${model})${council?' + council':''}`);
}
function peShowPlan(plan){
  const ed=$('#pePlanEdit'),card=$('#peEditCard');
  if(!ed||!card)return;   // not on the page — PE.plan holds it until drawPlanExec renders
  ed.value=plan;
  card.style.display='';
  $('#peDiscardBtn').onclick=()=>{card.style.display='none';PE.plan=null;};
  $('#peApproveBtn').onclick=()=>peExecute();
  card.scrollIntoView({behavior:'smooth',block:'start'});
}
async function peReplan(){
  const task=window._peTask||($('#peTask').value||'').trim();
  if(!task){toast('No task to re-plan','err');return;}
  const curPlan=$('#pePlanEdit').value;
  const feedback=prompt('What to change about the plan? (feedback)');
  if(!feedback)return;
  const via=chipVal($('#peVia'));
  const acctEl=$('#peAcct');
  const body={...C(),task,feedback,plan_text:curPlan,
    model:chipVal($('#pePlan')),effort:chipVal($('#peEff')),council:false,via,
    account:acctEl?chipVal(acctEl):''};
  $('#peEditCard').style.display='none';
  peJobStart('plan_replan',body,'Re-planning with feedback');
}
async function peExecute(){
  const task=window._peTask||($('#peTask').value||'').trim();
  const plan=$('#pePlanEdit').value;
  const perStep=!!$('#pePerStep').checked;
  // Re-read the chips live rather than replaying the snapshot peRun() took
  // when the plan was submitted — the review card stays on screen with the
  // via/model/account chips still editable, so a change made after reading
  // the plan (e.g. picking a different account before executing) must win.
  const viaEl=$('#peVia'),execEl=$('#peExec'),acctEl=$('#peAcct');
  const cfg=viaEl?{via:chipVal(viaEl),execModel:execEl?chipVal(execEl):'',
                   account:acctEl?chipVal(acctEl):''}:(window._peExecCfg||{});
  window._peExecCfg=cfg;
  $('#peEditCard').style.display='none';
  // keep the (possibly hand-edited) plan in PE until the launch actually
  // succeeds — a failed launch (stale/unavailable OmniRoute model, etc.)
  // re-opens the editor with it instead of forcing a full replan
  PE.plan=plan;
  peJobStart('plan_launch',{...C(),task,plan_text:plan,per_step:perStep,
    via:cfg.via,model:cfg.execModel,account:cfg.account},
    'Launching execute session'+(perStep?' (per-step)':''));
}
/* mirrors the TUI's category-grouped library multi-select */
async function drawAgentPicker(){
  const [lib,cur]=await Promise.all([
    api('/api/agents/library?'+qs({path:CUR.path})),
    api('/api/agents/session?'+qs(C()))]);
  const on=new Set(cur.refs||[]);
  window._agLimit=cur.limit||10;
  const used=cur.usage||{};
  /* Claude Code's own `agentLastUsed`, which is the only honest answer to "is
     this doing anything?". An agent installed and never delegated to is the one
     to remove — the picker used to give no way to tell those apart. */
  const chip=(ref,label,desc)=>{
    const u=used[label]||used[ref.split('/').pop()];
    return `<span class="chip${on.has(ref)?' on':''}${u?' used':''}" data-ref="${esc(ref)}"
      data-f="${esc(ref+' '+(desc||''))}"
      title="${esc((desc||'')+(u?'  ·  last used '+u+' ago':'  ·  never used'))}"
      onclick="this.classList.toggle('on');agCount()">${esc(label)}${u?' <i>'+esc(u)+'</i>':''}</span>`;};
  const sug=(cur.suggested||[]).map(s=>chip(s.ref,'★ '+s.ref.split('/').pop(),s.reason)).join('');
  const cats=(lib.categories||[]).map(c=>{
    const nSel=c.agents.filter(a=>on.has(c.category+'/'+a.name)).length;
    return `<details style="margin-bottom:6px">
      <summary style="cursor:pointer;font-weight:600">${esc(c.category)}
        <span style="color:var(--dim)">(${c.agents.length})</span>
        <span class="agcnt" data-cat="${esc(c.category)}" style="color:var(--ok)">${nSel?nSel+' selected':''}</span></summary>
      <div class="chips" style="padding:6px 0 2px 16px">
        ${c.agents.map(a=>chip(c.category+'/'+a.name,a.name,a.desc)).join('')}</div></details>`;}).join('');
  $('#agSel').innerHTML=(sug?`<div class="lbl">Suggested for this project</div>
      <div class="chips" style="margin-bottom:10px">${sug}</div>`:'')
    // the same box as the Agents page: ten collapsed categories over 150-odd
    // agents is not something you scroll, it is something you search
    +`<div class="fld"><input id="agPickQ" placeholder="Filter agents…" spellcheck="false">
        <div style="color:var(--dim2);font-size:12px;margin-top:4px" id="agPickCount"></div></div>`
    +(cats||'<div style="color:var(--dim)">Agent library is empty.</div>')
    +`<div class="mrow" style="align-items:center">
      <span id="agTot" style="flex:1;color:var(--dim);font-size:12px"></span>
      <button class="btn sm" onclick="agClear()">Clear all</button>
      <button class="btn sm pri" onclick="applyAgents()">Apply to project</button></div>
     <div style="border-top:1px solid var(--line);margin-top:8px;padding-top:8px">
      <div class="lbl">Getting them actually used</div>
      <p style="color:var(--dim);font-size:12px;margin:2px 0 6px">Copying agent files in only makes them <i>available</i>. Claude Code picks a subagent by matching your task against its <b>description</b> — nothing reads the body — so a set nothing ever mentions is a set that never fires. Three levers, strongest first: a sharp description, the table below (kept in the project's CLAUDE.md, the one file read on every turn), and the optional prompt hook that names a match deterministically.</p>
      <div class="mrow" style="margin:0 0 8px">
        <button class="btn sm" onclick="go('agents')" title="Sharpening is a machine-wide action now — it lives on the Agents page and rewrites every copy of an agent at once">${ic('ai')} Sharpen descriptions…</button>
        <button class="btn sm" onclick="go('hooks')" title="Install the suggest-subagent hook: a keyword match on your prompt, no model call">${ic('link')} Prompt-match hook</button>
      </div>
      ${(cur.routing||[]).length
        ?`<div class="clog">${(cur.routing||[]).map(r=>
            `<div><b>${esc(r.name)}</b> — ${esc(r.trigger||'')}</div>`).join('')}</div>`
        :'<div class="empty">Nothing installed yet, so no table is written.</div>'}
     </div>`;
  bindFilter('agPickQ','#agSel details .chip','agPickCount');
  agCount();
}
function agRefs(){return [...new Set([...document.querySelectorAll('#agSel .chip.on')]
  .map(c=>c.dataset.ref))];}
function agCount(){
  const refs=agRefs(),over=refs.length>window._agLimit;
  const el=$('#agTot');
  if(el){el.textContent=refs.length+' agent(s) selected'
    +(over?` — over ${window._agLimit}, may slow Claude startup`:'');
    el.style.color=over?'var(--warn)':'var(--dim)';}
  document.querySelectorAll('#agSel .agcnt').forEach(s=>{
    const n=refs.filter(r=>r.startsWith(s.dataset.cat+'/')).length;
    s.textContent=n?n+' selected':'';});
}
function agClear(){
  document.querySelectorAll('#agSel .chip.on').forEach(c=>c.classList.remove('on'));
  agCount();
}
/* Machine-wide by default. The same agent lives in a dozen projects and in
   every account's user directory; sharpening it a dozen times was a dozen
   model calls and a dozen chances to get a dozen different answers. */
function agSharpen(scope,redraw){
  inlineJob('#jban','agent_desc_ai',{...C(),scope:scope||'all'},
    {label:scope==='project'?'Sharpening agent descriptions'
                            :'Sharpening every agent description',
     onDone:st=>{const r=st.result||{},u=r.updated||[];
       toast(u.length?`Rewrote ${u.length} description(s) in ${r.locations} location(s)`
                     :(r.rejected?'Rejected — nothing written':'Nothing changed'),
             u.length||r.rejected?'ok':'ok');},
     redraw:redraw||(()=>drawPage('agents'))});
}
async function applyAgents(){
  const r=await post('/api/agents/session',{...C(),refs:agRefs()});
  toast(`${r.active} agent(s) active · ${r.routed} in the CLAUDE.md table`,'ok');
  drawAgentPicker();
}

/* ── manager pages ── */
async function drawPage(id){
  const n=NAV.find(x=>x[0]===id);
  $('#ttl').textContent=n?n[2]:id;
  $('#tpath').textContent='';
  // the token is taken BEFORE the fetches start; each page function drops its
  // write if navigation moved on while it was waiting
  const nav=paintNow(LOADING);
  await n[4]()(nav);
}

/* Claude Code's OWN state: what it records about itself, which claudectl had
   never opened. Read-only except the settings editor and the disk sweep, and
   the sweep reports before it ever deletes. */
async function pgClient(nav){
  const [use,bg,disk,cc,am]=await Promise.all([
    api('/api/client/usage'),api('/api/background-agents'),
    api('/api/disk'),api('/api/cc-settings'),
    api('/api/automode?'+qs(CUR?{path:CUR.path}:{}))]);
  CCACCTS=cc.accounts||[];
  AM=am;
  const useRows=(k,label)=>{
    const rows=(use[k]||[]);
    if(!rows.length)return `<div class="empty">No ${label} recorded as used yet.</div>`;
    return `<table class="tbl"><tr><th>${label}</th><th class="num">Uses</th><th>Last</th></tr>`
      +rows.map(r=>`<tr><td>${esc(r.name)}</td><td class="num">${r.count||''}</td>
        <td style="color:var(--dim)">${esc(r.last_used)}</td></tr>`).join('')+'</table>';};
  const d=bg.daemon||{},tm=bg.teams||{};
  const bgCard=!d.recognised
    ?'<div class="empty">No background-agent daemon on this machine.</div>'
    :(d.workers||[]).length
      ?`<table class="tbl"><tr><th>Worker</th><th>State</th></tr>`+
        d.workers.map(w=>`<tr><td>${esc(w.id)}</td><td>${esc(w.state||'')}</td></tr>`).join('')+'</table>'
      :`<div class="empty">No background agents running${d.updated?' (roster updated '+esc(d.updated)+' ago)':''}.</div>`;
  const teamCard=!tm.recognised
    ?'<div class="empty">Agent teams are not in use on this machine.</div>'
    :`<div style="color:var(--dim);font-size:13px">${(tm.teams||[]).length} team(s), ${(tm.tasks||[]).length} task director(ies)</div>`;
  const mb=b=>(b/1048576).toFixed(1)+' MB';
  const diskRows=(disk.accounts||[]).map(a=>
    `<tr><td><b>${esc(a.account)}</b></td><td class="num">${mb(a.bytes)}</td>
      <td style="color:var(--dim);font-size:12px">${(a.stores||[]).map(s=>esc(s.name)+' '+mb(s.bytes)).join(' · ')}</td></tr>`).join('');
  /* Settings FIRST. The page opened on three diagnostic cards and buried the
     editor fifth, which is backwards: "what did it record about itself" is a
     question you ask occasionally, "change how Claude behaves" is why you came.
     Everything below the editor is read-mostly and stays in that order. */
  if(!paint(nav,`
    <div class="card"><h3>${ic('settings')} How Claude Code behaves</h3>
      <p class="secthint">These are Claude Code's own preferences — the same ones you would otherwise hand-edit in <code>settings.json</code>. Each account keeps its own file, and a change applies to <b>sessions you start after it</b>, not the one already running.</p>
      <p class="secthint">A dash (<b>—</b>) means <b>not set</b>: Claude Code falls back to its own built-in default. Clearing a field removes the key rather than writing a default in, because <i>off</i> and <i>absent</i> are not the same thing to it.</p>
      <div class="fld" style="margin:0 0 10px"><input id="ccQ" placeholder="Find a setting — type what you want to change…" spellcheck="false">
        <div style="color:var(--dim2);font-size:12px;margin-top:4px" id="ccCount"></div></div>
      ${ccTable(cc)}</div>
    ${autoModeCard(am)}
    <div class="card"><h3>${ic('ai')} What is actually being used</h3>
      <p class="secthint">Straight from Claude Code's own counters — the only record of which skills, plugins and agents are live rather than dead weight.</p>
      <div class="cols3">
        <div>${useRows('skills','Skill')}</div>
        <div>${useRows('plugins','Plugin')}</div>
        <div>${useRows('agents','Agent')}</div></div></div>
    <div class="card"><h3>${ic('robot')} Background agents</h3>
      <p class="secthint">Work Claude Code is running outside your session — a long job you sent to the background, and the teams it splits one into.</p>
      ${bgCard}
      <h3 style="margin-top:14px">Agent teams</h3>${teamCard}</div>
    <div class="card"><h3>${ic('folder')} Disk</h3>
      <p class="secthint">What Claude Code has stored for each account: transcripts, file snapshots for <code>/rewind</code>, and its caches. Preview first — cleanup lists what it would delete before it deletes anything.</p>
      <table class="tbl"><tr><th>Account</th><th class="num">Total</th><th>Stores</th></tr>${diskRows}</table>
      <div class="gcbar">
        <div class="fld" style="width:110px"><label for="gcDays">Keep days</label>
          <input id="gcDays" type="number" min="1" value="30"></div>
        <button class="btn sm" onclick="gcRun(false)">Preview cleanup</button>
        <span id="gcOut"></span></div></div>
    <div class="card"><h3>${ic('search')} Prompt history</h3>
      <p class="secthint">Every prompt you have typed into Claude Code, on any account. Useful for finding the phrasing that worked last time.</p>
      <div class="gcbar">
        <div class="fld" style="flex:1;min-width:220px">
          <input id="phQ" placeholder="search every prompt you have typed"
            onkeydown="if(event.key==='Enter')phSearch()"></div>
        <button class="btn sm" onclick="phSearch()">Search</button></div>
      <div id="phOut"></div></div>`))return;
  bindFilter('ccQ','.ccrow[data-f]','ccCount');
}
/* ── auto mode ──
   In auto mode a classifier reviews each action instead of you. It trusts your
   working directory and the repo's existing remotes and NOTHING else, so
   routine internal work gets blocked until you say what else is yours. Two
   halves, and they answer each other: the environment entries are the fix, and
   the denial list is the evidence for which entry to write. */
let AM=null;
function autoModeCard(am){
  const accts=am.accounts||[],den=am.denials||[];
  const modeChips=(a,i)=>(am.modes||[]).map(m=>
    `<span class="chip${(a.mode||'')===m?' on':''}" title="${esc((am.profiles||{})[m]||'')}"
       onclick="amSetMode(${i},${hesc(m)})">${esc((am.mode_labels||[])[(am.modes||[]).indexOf(m)]||m||'unset')}</span>`).join('');
  const rows=accts.map((a,i)=>`<div class="amrow2">
    <div class="amname" title="${esc(a.dir)}">${esc(a.name)}</div>
    <div class="chips">${modeChips(a,i)}</div>
    <div class="amenv">${a.environment.length
      ? `${a.environment.filter(e=>e!=='$defaults').length} trusted entry(ies)`
      : '<span style="color:var(--dim2)">defaults only</span>'}
      <button class="btn sm" onclick="amEditEnv(${i})">Edit</button></div></div>`).join('');
  // grouped, not a raw log: the question is "what keeps getting blocked"
  const denRows=den.length?den.map(g=>`<div class="lrow">
      <span class="tag warn">×${g.count}</span>
      <div><b>${esc(g.key)}</b>
        <div style="color:var(--dim);font-size:12px">${esc((g.samples||[])[0]||'')}</div>
        ${g.reason?`<div style="color:var(--dim2);font-size:11.5px">${esc(g.reason)}</div>`:''}</div>
    </div>`).join('')
    :'<div class="empty">Nothing blocked in this project yet.</div>';
  return `<div class="card"><h3>${ic('check')} Auto mode</h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:12px">A classifier reviews each
      action instead of you. It trusts this repo and its existing remotes; everything else
      counts as external until you say otherwise. <b>Written to each account's own
      <code>settings.json</code></b> — the classifier ignores <code>autoMode</code> from a
      project file on purpose, since a checked-in file could otherwise grant itself rules.</p>
    ${rows||'<div class="empty">No accounts configured.</div>'}
    <div class="sect"><div class="secth"><h4>Recently blocked, in this project</h4>
      <span class="secttag">${den.reduce((n,g)=>n+g.count,0)} denial(s)</span></div>
      <div class="secthint">From the <code>PermissionDenied</code> hook. A destination that
        keeps appearing belongs in the trusted list above; a command you want to run
        unreviewed belongs in an allow rule.</div>
      ${denRows}</div>
    <div class="sect"><div class="secth"><h4>Effective rules</h4>
      <button class="btn sm" onclick="amRules('config')">Show effective</button>
      <button class="btn sm" onclick="amRules('defaults')">Show built-in</button></div>
      <pre id="amRules" class="amrules"></pre></div></div>`;
}
async function amSetMode(i,mode){
  const a=(AM.accounts||[])[i];if(!a)return;
  const r=await post('/api/automode',{cfgdir:a.dir,mode});
  if(r&&r.ok){a.mode=mode;toast(`${a.name}: ${mode||'unset'}`,'ok');go('client');}
}
async function amEditEnv(i){
  const a=(AM.accounts||[])[i];if(!a)return;
  // "$defaults" is never shown or edited: it is added back on save, and a user
  // deleting it by accident would silently discard every built-in trust slot
  const cur=(a.environment||[]).filter(e=>e!=='$defaults').join('\n');
  const v=await ask('Trusted infrastructure — '+a.name,
    [{type:'textarea',label:'One entry per line',value:cur}],
    'Plain English, not patterns — the classifier reads them as prose. '
    +'e.g. "Source control: github.com/acme and all repos under it"');
  if(v===null)return;
  const r=await post('/api/automode',{cfgdir:a.dir,environment:v[0]});
  if(r&&r.ok){toast(r.message||'Saved','ok');go('client');}
}
async function amRules(which){
  const el=$('#amRules');if(!el)return;
  el.textContent='…';
  const r=await api('/api/automode/config?'+qs({which}));
  el.textContent=r.ok?JSON.stringify(r.rules,null,2):(r.error||'could not read');
}

/* Twenty-one raw camelCase keys against three unlabelled columns of white
   browser-default inputs is what this was. Three things fix it: the controls
   go through `.fld` so they are the app's inputs and follow the skin, the
   rows are grouped under headings so the list is scannable, and each row says
   whether it is set anywhere and whether the accounts agree. */
function ccTable(cc){
  const accts=cc.accounts||[],sch=cc.schema||{},groups=cc.groups||[],
        ghelp=cc.group_help||{};
  if(!accts.length)return '<div class="empty">No accounts configured.</div>';
  const head=`<div class="ccrow cchead"><div></div>`
    +accts.map(a=>`<div title="${esc(a.dir)}">${esc(a.name)}</div>`).join('')
    +`<div></div></div>`;
  /* A group name tells someone who already knows Claude Code where to look and
     tells everybody else nothing, so each carries the sentence saying what it
     is FOR. The `.fgrp` wrapper is what lets the filter take a whole heading
     away with its rows instead of leaving it standing over nothing; inside a
     grid it has to be display:contents, like `.ccrow` itself. */
  const body=groups.map(g=>{
    const keys=Object.keys(sch).filter(k=>sch[k].group===g);
    if(!keys.length)return '';
    return `<div class="fgrp"><div class="ccgrp">${esc(g)}
      ${ghelp[g]?`<div class="cchelp">${esc(ghelp[g])}</div>`:''}</div>`
      +keys.map(k=>ccRow(k,sch[k],accts,g)).join('')+'</div>';
  }).join('');
  return `<div class="cctable" style="--cc-accts:${accts.length}">${head}${body}</div>`;
}
function ccRow(k,s,accts,group){
  const vals=accts.map(a=>a.values[k]);
  const set=vals.filter(v=>v!==undefined).length;
  const differ=set>0&&new Set(vals.map(v=>JSON.stringify(v))).size>1;
  const state=!set?'<span class="ccdot off" title="not set anywhere — Claude Code uses its own default"></span>'
    :differ?'<span class="ccdot warn" title="the accounts disagree"></span>'
    :'<span class="ccdot on" title="set, and the same everywhere"></span>';
  // the raw key is in the haystack because it is what the docs and settings.json
  // call it — someone arriving from either searches for that, not for the label
  return `<div class="ccrow${differ?' differ':''}"
      data-f="${esc(k+' '+ccName(k)+' '+(s.help||'')+' '+(group||''))}">
    <div class="cclbl">${state}<div><b>${esc(ccName(k))}</b>
      <code>${esc(k)}</code>
      <div class="cchelp">${esc(s.help)}</div></div></div>`
    +accts.map((a,i)=>`<div class="fld">${ccInput(k,s,a,i)}</div>`).join('')
    +`<div class="ccall"><button class="btn sm" title="Apply the first account's value to all"
        onclick="ccAll(${hesc(k)})">${ic('group')}</button></div></div>`;
}
/* autoCompactWindow -> "Auto compact window". The raw key still shows, because
   it is what the documentation and settings.json call it. */
function ccName(k){
  const s=k.replace(/([a-z0-9])([A-Z])/g,'$1 $2').toLowerCase();
  return s.charAt(0).toUpperCase()+s.slice(1);
}
/* The account is passed by INDEX into CCACCTS, never as a path. A Windows
   config dir is full of backslashes, and interpolating one into a JS string
   literal inside an HTML attribute makes the browser read `\U` and `\.` as
   escapes: `C:\Users\mab\.claude` arrives as `C:Usersmab.claude`, which then
   fails the account allowlist as "invalid cfgdir". */
function ccInput(k,s,a,ai){
  const v=a.values[k],id=ccId(k,a.dir);
  const set=`onchange="ccSet(${hesc(k)},this,${ai})"`;
  if(s.kind==='bool')
    return `<select id="${id}" ${set}><option value=""${v===undefined?' selected':''}>—</option>
      <option value="true"${v===true?' selected':''}>on</option>
      <option value="false"${v===false?' selected':''}>off</option></select>`;
  if(s.kind==='enum')
    return `<select id="${id}" ${set}><option value="">—</option>`
      +s.choices.map(c=>`<option${v===c?' selected':''}>${esc(c)}</option>`).join('')+'</select>';
  const shown=v===undefined?'':(Array.isArray(v)?v.join(', '):(typeof v==='object'?JSON.stringify(v):v));
  const ph=s.kind==='json'?'{ }':(s.kind==='list'?'a, b, c':(s.kind==='int'?'number':'—'));
  return `<input id="${id}" value="${esc(String(shown))}" placeholder="${esc(ph)}" ${set}>`;
}
function ccId(k,dir){return 'cc_'+k+'_'+btoa(dir).replace(/[^a-zA-Z0-9]/g,'');}
async function ccSet(key,el,ai){
  const a=CCACCTS[ai];if(!a)return;
  const r=await post('/api/cc-settings',{key,value:el.value,cfgdir:a.dir});
  toast(r.ok?(r.message||'Saved'):(r.error||'Failed'),r.ok?'ok':'err');
  drawPage('client');          // redraw so the set/differs marker is honest
}
async function ccAll(key){
  const first=$('#'+ccId(key,(CCACCTS[0]||{}).dir||''));
  if(!first)return;
  const r=await post('/api/cc-settings',{key,value:first.value,all_accounts:true});
  toast(r.ok?'Applied to every account':(r.error||'Failed'),r.ok?'ok':'err');
  drawPage('client');
}
let CCACCTS=[];
async function gcRun(apply){
  const days=parseInt(($('#gcDays')||{}).value||'30',10)||30;
  const r=await post('/api/disk/gc',{days,apply});
  const mb=(r.bytes/1048576).toFixed(1);
  if(apply){$('#gcOut').textContent=`deleted ${r.files} files (${mb} MB)`;drawPage('client');return;}
  $('#gcOut').innerHTML=r.files
    ?`would delete <b>${r.files}</b> files (${mb} MB), keeping ${r.kept} named or tagged session(s)
       <button class="btn sm danger" onclick="gcApply(${days},${r.files},${hesc(mb)})">Delete them</button>`
    :'nothing older than that';
}
async function gcApply(days,files,mb){
  if(!await confirmBox('Delete '+files+' files?',
    'Frees about '+mb+' MB of Claude Code transcripts and snapshots. Sessions you named or tagged are kept. This cannot be undone.'))return;
  await gcRun(true);
}
async function phSearch(){
  const q=($('#phQ')||{}).value||'';
  const r=await api('/api/prompt-history?'+qs({q,limit:100}));
  const rows=(r.prompts||[]);
  $('#phOut').innerHTML=rows.length
    ?'<div class="dlist">'+rows.map(p=>`<div class="lrow" style="display:block">
        <div style="font:12px Consolas,monospace;white-space:pre-wrap">${esc(p.text)}</div>
        <div style="color:var(--dim2);font-size:11px">${esc(p.project)}</div></div>`).join('')+'</div>'
    :'<div class="empty">No matching prompts.</div>';
}

async function pgUsage(nav){
  const [plan,daily,projects]=await Promise.all([
    api('/api/usage/plan'),api('/api/usage/daily?days=14'),api('/api/usage/projects')]);
  const planRows=(plan.accounts||[]).map(a=>{
    const wins=(a.windows||[]).map(w=>{
      const hot=w.pct>=80;
      return `<div style="display:flex;align-items:center;gap:10px;padding:2px 0">
        <span style="width:80px;color:var(--dim);font-size:12px">${esc(w.label)}</span>
        <div class="bar${hot?' hot':''}" style="flex:1"><i style="width:${Math.min(100,w.pct)}%"></i></div>
        <span class="num" style="width:44px;text-align:right">${w.pct}%</span>
        <span style="color:var(--dim2);font-size:11px;width:130px">${esc(w.resets||'')}</span></div>`;}).join('');
    return `<div style="margin-bottom:10px"><b>${esc(a.account)}</b>
      <span style="color:var(--dim2);font-size:12px">${esc(a.email||'')}</span>${wins||'<div style="color:var(--dim);font-size:12px">no data yet</div>'}</div>`;}).join('');
  const maxTok=Math.max(1,...(daily.days||[]).map(d=>d.tokens));
  const dRows=(daily.days||[]).map(d=>`
    <div style="display:flex;align-items:center;gap:10px;padding:2px 0">
      <span style="width:80px;color:var(--dim);font-size:12px">${esc(d.day)}</span>
      <div class="bar" style="flex:1"><i style="width:${Math.round(100*d.tokens/maxTok)}%"></i></div>
      <span class="num" style="width:60px;text-align:right">${esc(d.tok_fmt)}</span>
      <span class="num" style="width:60px;text-align:right;color:var(--dim)">$${d.cost.toFixed(2)}</span></div>`).join('');
  const pRows=(projects.projects||[]).map(p=>`
    <tr><td>${esc(p.name)}</td><td class="num">${p.sessions}</td><td class="num">${p.msgs}</td>
    <td class="num">${p.usage.in}</td><td class="num">${p.usage.out}</td>
    <td class="num">${p.exact?'':'~'}$${p.cost.toFixed(2)}</td></tr>`).join('');
  const total=(projects.projects||[]).reduce((a,p)=>a+p.cost,0);
  if(!paint(nav,`
    <div class="card"><h3>Plan usage by account</h3>${planRows||'<div style="color:var(--dim)">checking…</div>'}</div>
    <div class="card"><h3>Daily tokens (14 days)</h3>
      ${INST.html('spark','daily',{fmt:'tok',unit:'peak day'})}
      ${dRows}</div>
    <div class="card"><h3>Per-project — total est. $${total.toFixed(2)}</h3>
      <table class="tbl"><tr><th>project</th><th>sess</th><th>msgs</th><th>in</th><th>out</th><th>est.$</th></tr>${pRows}</table></div>`))return;
  const days=(daily.days||[]).map(d=>d.tokens||0);
  INST.set('daily',{series:days});
  setRead('daily',Math.max(0,...days));
}

let SIDX=null;
async function pgSearch(nav){
  // this one paints its shell BEFORE the fetch so the input is focusable
  // immediately; the guard still matters for everything after the await
  if(!paint(nav,`<div class="card"><h3>${ic('search')} Search every session</h3>
    <div class="fld"><input id="gq" placeholder="Type to search names, titles, previews…"></div>
    <div id="gres" style="margin-top:10px"></div></div>`))return;
  if(!SIDX){const d=await api('/api/search-index');SIDX=d.rows||[];}
  if(nav!==NAV_ID)return;
  const draw=()=>{
    const q=($('#gq').value||'').toLowerCase().trim();
    const m=q?SIDX.filter(r=>q.split(/\s+/).every(w=>r.haystack.includes(w))):SIDX;
    $('#gres').innerHTML=m.slice(0,80).map((r,i)=>`
      <div class="qcard" style="margin-bottom:6px" onclick='gResume(${hesc(i)})'>
        <div class="info"><div class="t">${esc(r.display)}</div>
        <div class="s">${esc(r.project)} · ${esc(r.age)} ago</div></div>
        <button class="btn sm">Resume</button></div>`).join('')
      +(m.length>80?`<div style="color:var(--dim);padding:6px">…${m.length-80} more — refine your search</div>`:'');
    window._gmatch=m;
  };
  if(PENDING_SEARCH_Q){$('#gq').value=PENDING_SEARCH_Q;PENDING_SEARCH_Q='';}
  $('#gq').oninput=draw;draw();$('#gq').focus();
  const byProj={};for(const r of SIDX)byProj[r.project]=(byProj[r.project]||0)+1;
}
function gResume(i){const r=window._gmatch[i];
  askLaunch({title:'Resume — '+r.display,sub:r.project,isNew:false,
    path:r.path,enc:r.enc,choice:'resume:'+r.sid,cfgdir:r.cfgdir});}

/* which account the MCP page is showing. `claude mcp` honours CLAUDE_CONFIG_DIR,
   so the list, the detail and every add/remove name one account explicitly —
   the reader and the writer used to be able to disagree about which. */
let MCPACCT='';
async function pgMcp(nav){
  /* refresh=1: `get_mcp_status` is cached 30s for the dashboard's 10-second
     poll, and this page is where you come to find out whether a server is
     actually up. */
  const d=await api('/api/mcp?'+qs(Object.assign({refresh:1},MCPACCT?{cfgdir:MCPACCT}:{})));
  const srv=d.servers||[],up=srv.filter(x=>x.status==='ok').length;
  const accts=(ST.accounts||[]);
  const picker=accts.length>1?`<div class="chips" style="margin-bottom:10px">
      ${accts.map(a=>`<span class="chip${(MCPACCT||'')===(a.dir||'')?' on':''}"
        onclick='mcpAcct(${hesc(a.dir||'')})'>${esc(a.name)}</span>`).join('')}
    </div>`:'';
  if(!paint(nav,`<div class="card"><h3>MCP servers <span class="sp"></span>
    <button class="btn sm" onclick="mcpAdd()">${ic('add')} Add server</button></h3>
    ${picker}
    ${srv.length?`<div class="pghd">
      ${INST.html('ring','mcp',{fmt:'ratio',unit:'/–',sub:'up',label:'reachable'})}
      <div class="pghdt"><b>${up} of ${srv.length}</b> server${srv.length===1?'':'s'} responding
        ${up===srv.length?'<span class="tag ok">all healthy</span>'
          :`<span class="tag warn">${srv.length-up} unreachable</span>`}
        <div class="sub">A server that isn't responding still appears in your session's tool list — the calls just fail.</div></div>
    </div>`:''}
    ${srv.map(s=>`<div style="display:flex;align-items:center;gap:10px;padding:5px 0">
      <span style="color:${s.status==='ok'?'var(--ok)':'var(--warn)'}">${ic(s.status==='ok'?'check':'help')}</span><b style="flex:1">${esc(s.name)}</b>
      <button class="btn sm" onclick='mcpDetail(${hesc(s.name)})'>${ic('eye')} Detail</button>
      <button class="btn sm" onclick='mcpDocs(${hesc(s.name)})'>${ic('search')} Tool docs</button>
      <button class="btn sm danger" onclick='mcpRemove(${hesc(s.name)})'>Remove</button>
    </div>`).join('')||'<div style="color:var(--dim)">No MCP servers configured.</div>'}
    <p style="color:var(--dim);font-size:12.5px;margin:10px 0 0">Analyzing a server writes its tool documentation into a sentinel block of the account's <b>global CLAUDE.md</b>, which now has its own page.</p></div>`))return;
  if(srv.length){
    INST.set('mcp',{v:up/srv.length,tone:up===srv.length?'ok':up?'warn':'err'});
    setRead('mcp',up);
    setUnit('mcp','/'+srv.length);
  }
}

/* ── what claudectl itself did ───────────────────────────────────────────────
   Until this page existed a failure inside claudectl went to a NullHandler: the
   logger only wrote a file when CLAUDECTL_DEBUG was set, so every background job
   crash, every faulted handler and every one of claudectl's own Claude calls
   failing against a rate-limited account vanished. The level chips ride
   bindFilter's `extra` predicate rather than a query parameter, so the endpoint
   takes no arguments and switching level costs no round trip. */
let LOGLVL='';
function logLvl(v){LOGLVL=(LOGLVL===v?'':v);drawPage('logs');}
async function pgLogs(nav){
  const d=await api('/api/logs');
  const ev=d.events||[],now=Date.now()/1000;
  const n={error:0,warn:0,info:0};
  ev.forEach(e=>{if(n[e.lvl]!==undefined)n[e.lvl]++;});
  const tone={error:'err',warn:'warn',info:'ok'};
  const chips=['error','warn','info'].map(l=>
    `<span class="chip${LOGLVL===l?' on':''}" onclick="logLvl(${hesc(l)})">${l} ${n[l]}</span>`).join('');
  const rows=ev.map(e=>{
    const lvl=e.lvl||'error';
    return `<div class="lgrow" data-lvl="${esc(lvl)}" data-f="${esc(lvl+' '+(e.src||'')+' '+(e.msg||'')+' '+(e.detail||'')+' '+(e.proj||''))}">
      <div class="lghd"><span class="tag ${tone[lvl]||'err'}">${esc(lvl)}</span>
        <b>${esc(e.src||'?')}</b>
        <span class="sp"></span>
        <span class="aage">${esc(fmtAge(Math.max(0,now-(e.ts||0))))}</span></div>
      <div class="lgmsg">${esc(e.msg||'')}</div>
      ${e.proj?`<div class="asub">${esc(e.proj)}</div>`:''}
      ${e.detail?`<pre class="lgdet">${esc(e.detail)}</pre>`:''}
    </div>`;}).join('');
  if(!paint(nav,`<div class="card"><h3>${ic('history')} Logs <span class="sp"></span>
    <span class="tag${n.error?' err':''}">${n.error} error${n.error===1?'':'s'}</span></h3>
    <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px">Everything claudectl did on its own: its headless Claude calls, background jobs, the auto-memory scheduler, the failover proxy. Newest first, capped at ${Math.round((d.cap||0)/1024)} KB on disk.</p>
    ${ev.length?`<div class="chips" style="margin-bottom:10px">${chips}</div>
    <input id="lgq" class="in" placeholder="Filter events…" style="margin-bottom:8px">
    <span id="lgn" style="color:var(--dim);font-size:12px"></span>
    ${rows}`:`<div style="color:var(--dim)">Nothing recorded yet — claudectl writes here when one of its own calls, jobs or scheduled passes fails.</div>`}
    <p style="color:var(--dim);font-size:12px;margin:12px 0 0">File: <code>${esc(d.path||'')}</code><br>
      Verbose tracing: set <code>CLAUDECTL_DEBUG=1</code> and read <code>${esc(d.debug_log||'')}</code>.</p></div>`))return;
  bindFilter('lgq','.lgrow','lgn',el=>!LOGLVL||el.dataset.lvl===LOGLVL);
}

/* ── the account's global instructions ───────────────────────────────────────
   This was three cards at the BOTTOM of the MCP servers page, under the server
   list and a conventions table. It is the file Claude reads in every session on
   the account — the single most consequential piece of text claudectl can edit
   — and it was findable only by scrolling past something unrelated. Its own
   page, reachable from the sidebar. No new endpoints: the three it uses all
   already existed and worked. */
let GMDACCT='';
async function pgGlobalMd(nav){
  const gm=await api('/api/global-claude-md?'+qs(GMDACCT?{cfgdir:GMDACCT}:{}));
  const accts=gm.accounts||[];
  const picker=accts.length>1?`<div class="chips" style="margin-bottom:10px">
      ${accts.map(a=>`<span class="chip${(GMDACCT||'')===(a.dir||'')?' on':''}"
        onclick='gmdAcct(${hesc(a.dir||'')})'>${esc(a.name)}</span>`).join('')}
    </div>`:'';
  const words=(gm.text||'').trim()?(gm.text.trim().split(/\s+/).length):0;
  if(!paint(nav,`
    <div class="card"><h3>${ic('doc')} Global CLAUDE.md <span class="sp"></span>
      <button class="btn sm" onclick='post("/api/open-editor",{file:${hesc(gm.path||'')}}).then(r=>toast(r.ok?"Opened in your editor":"Could not open it","ok"))'>${ic('edit')} Open in editor</button>
      <button class="btn sm pri" onclick="gmSave()">Save</button></h3>
      ${picker}
      <p style="color:var(--dim);font-size:13px;margin:0 0 8px">Claude reads this file in <b>every</b> session on this account, so every word is paid for on every turn — roughly ${Math.round(words*1.3)} tokens as it stands.
        ${gm.exists?'':'<span class="tag warn">does not exist yet</span>'}</p>
      <textarea id="gmText" style="min-height:300px;font-family:var(--mono)">${esc(gm.text||'')}</textarea>
      <div style="color:var(--dim2);font-size:12px;margin-top:6px">${esc(gm.path||'')}</div></div>
    <div class="card"><h3>${ic('link')} Cross-project conventions <span class="sp"></span>
      <button class="btn sm" onclick="convSync()">${ic('download')} Promote into global CLAUDE.md</button></h3>
      <p style="color:var(--dim);font-size:13px;margin:0 0 8px">Preferences, corrections and decisions claudectl learned in <b>one</b> project and saw again in others — read from every account's memory, not from CLAUDE.md files. Promoting writes them into a sentinel block above; only that block is touched.</p>
      <div id="convOut"><span class="spin"></span></div></div>`))return;
  drawConv();
}
async function drawConv(){
  const c=await api('/api/conventions');const b=$('#convOut');if(!b)return;
  const rows=(c.conventions||[]),near=(c.near||[]);
  const promoted=rows.length
    ?`<div class="clog">${rows.map(r=>`<div>${esc(r.text||'')}
        <span class="tag">${r.projects} project${r.projects===1?'':'s'}</span>
        ${r.pinned?'<span class="tag">pinned</span>':''}</div>`).join('')}</div>`
    :'<div class="empty">Nothing qualifies yet — but these are close:</div>';
  /* The old empty state was a dead end: it stated a fact and offered no way to
     change it, so the card stayed empty forever. A near-miss with its own Pin
     button is the difference between a report and a control. */
  const cand=near.length?`<div class="lbl" style="margin-top:10px">Almost there</div>`
    +near.map(n=>`<div class="lrow">
        <div class="clamp3">${esc(n.text)}
          <div style="color:var(--dim2);font-size:11px">${esc(n.why)}</div></div>
        <button class="btn sm" onclick="convPin(${hesc(n.text)})">Pin</button></div>`).join('')
    :(rows.length?'':'<div class="empty">No preference or correction lessons recorded in any project yet — they are extracted when you review sessions.</div>');
  b.innerHTML=promoted+cand;
}
async function convPin(text){
  const r=await post('/api/conventions/pin',{text});
  toast(r.ok?`Pinned in ${r.pinned} place(s) — now promotable`:'Nothing matched',r.ok?'ok':'err');
  drawConv();
}
function gmdAcct(dir){GMDACCT=dir;drawPage('globalmd');}

/* ── Loops ───────────────────────────────────────────────────────────────────
   A `/loop` is SESSION-SCOPED: its tasks live in one conversation, fire only
   while that session is open and idle, and die with it. There is no daemon to
   talk to, so this page does the only three things that are real —

     start   open a session whose first typed message is `/loop …`
     watch   its transcript: each iteration is a turn
     stop    end that session (Esc inside the session clears the next wakeup;
             from out here, closing the session is the honest option)

   — and says so, rather than implying a scheduler claudectl does not own.

   loop.md is the other half: the prompt a BARE `/loop` runs. Project file wins
   over the account one, which is why both are editable here, side by side. */
let LOOPSCOPE='project',_loopTimer=null,LOOPKIND='schedule',LOOPPERM='auto',LOOPACCT='';
async function pgLoops(nav){
  const P=projCtx();
  const path=P?P.path:'';
  const scope=path?LOOPSCOPE:'user';
  const [d,md]=await Promise.all([
    api('/api/loops?'+qs(LOOPACCT?{cfgdir:LOOPACCT}:(P?{cfgdir:P.primary_cfgdir}:{}))),
    api('/api/loop-md?'+qs(Object.assign({scope},path?{path}:{},
        P?{cfgdir:P.primary_cfgdir}:{})))]);
  const rows=(d.loops||[]);
  const live=rows.filter(r=>r.running);
  const accts=d.accounts||[];
  const cost=v=>v?`<span class="tag">$${(+v).toFixed(2)}</span>`:'';
  const row=r=>`<div class="hrow">
      <span style="color:${r.running?'var(--ok)':'var(--dim)'}">${ic(r.running?'refresh':'check')}</span>
      <div class="info"><b>${esc(r.name)}</b>
        <span class="tag">${r.kind==='schedule'?'background':'in a session'}</span>
        <div style="color:var(--dim);font-size:12px"><code>${esc(r.text)}</code>${
          r.kind==='schedule'?` · every ${esc(r.interval||'?')} · ${esc(r.perm||'auto')}`:''}</div>
        ${r.last_result||r.last_error?`<div style="color:${r.last_error?'var(--warn)':'var(--dim2)'};font-size:12px;margin-top:2px">${esc((r.last_error||r.last_result).slice(0,160))}</div>`:''}</div>
      <span class="tag${r.running?' ok':''}">${r.kind==='schedule'
        ?(r.running?'scheduled':(r.expired?'expired':'stopped'))
        :(r.running?'session open':'ended')}</span>
      ${r.kind==='schedule'
        ?`${r.runs?`<span class="tag">${r.runs} run${r.runs===1?'':'s'}</span>`:''}${cost(r.last_cost)}
          ${r.running?`<span class="tag${r.expires_in<86400?' warn':''}">expires ${fmtAge(r.expires_in)}</span>`:''}`
        :`${r.iterations?`<span class="tag ok">${r.iterations} turns</span>`:''}
          ${r.last_activity?`<span class="tag">quiet ${fmtAge(r.last_activity)}</span>`:''}`}
      <span style="color:var(--dim2);font-size:12px">${fmtAge(r.age)} ago</span>
      ${r.journal&&r.journal.length?`<button class="btn sm" onclick='loopLog(${hesc(r.id)})'>log</button>`:''}
      ${r.running&&r.kind==='schedule'?`<button class="btn sm" onclick='loopRenew(${hesc(r.id)})'>renew</button>`:''}
      ${r.running
        ?`<button class="btn sm danger" onclick='loopStop(${hesc(r.id)})'>${r.kind==='schedule'?'Unschedule':'End session'}</button>`
        :`<button class="btn sm" onclick='loopForget(${hesc(r.id)})'>${ic('del')}</button>`}
    </div>`;
  window._loopRows=rows;
  const permNote=(d.perms||[]).find(x=>x.id===LOOPPERM);
  if(!paint(nav,`
    <div class="card"><h3>${ic('refresh')} Loops <span class="sp"></span>
      <span class="tag${live.length?' ok':''}">${live.length} live</span></h3>
      <p style="color:var(--dim);font-size:13px;margin:0 0 8px">Two kinds, because Claude Code only offers one. <b>In a session</b> is a real <code>/loop</code>: it fires while that session is open and idle, and dies with it. <b>In the background</b> is claudectl's own — an entry in ${esc(navigator.platform.startsWith('Win')?'Task Scheduler':'cron')} that runs headless <code>claude -p</code> on the interval, with claudectl closed and no session anywhere.</p>
      ${rows.length?rows.map(row).join(''):'<div class="empty">No loops started from claudectl yet.</div>'}</div>

    <div class="card"><h3>${ic('play')} Start a loop</h3>
      ${path?'':'<div class="empty">Open a project first — a loop runs inside one.</div>'}
      ${path?`
      <div class="chips" style="margin:0 0 10px">
        <span class="chip${LOOPKIND==='schedule'?' on':''}" onclick="loopKind('schedule')">in the background</span>
        <span class="chip${LOOPKIND==='session'?' on':''}" onclick="loopKind('session')">in a session</span>
      </div>
      <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px">${LOOPKIND==='schedule'
        ?`Runs <b>${esc(P.name)}</b> unattended on a timer, spending money without anyone watching — so it carries an expiry of ${d.ttl_days||7} days (renewable), your per-call budget cap, and the permission mode below. Each run is a fresh session that reads a rolling record of what the previous ones did, kept in the project's CLAUDE.md.`
        :`Opens a session in <b>${esc(P.name)}</b> and types the command for you. It fires while that session is open; closing it ends the loop.`}</p>
      <div class="grid2">
        <div class="fld"><label for="loopInt">Interval${LOOPKIND==='schedule'?'':' — blank = Claude decides'}</label>
          <input id="loopInt" placeholder="15m" spellcheck="false"></div>
        <div class="fld"><label for="loopPrompt">Prompt — blank = loop.md</label>
          <input id="loopPrompt" placeholder="check CI and fix what is red" spellcheck="false"></div>
      </div>
      ${LOOPKIND==='schedule'?`
      <div class="fld"><label>What it may do without asking</label>
        <div class="chips">${(d.perms||[]).map(pm=>`<span class="chip${LOOPPERM===pm.id?' on':''}"
          onclick='loopPerm(${hesc(pm.id)})' title="${esc(pm.note)}">${esc(pm.id)}</span>`).join('')}</div>
        <div style="color:var(--dim);font-size:12px;margin-top:4px">${esc(permNote?permNote.note:'')}</div></div>
      ${accts.length>1?`<div class="fld"><label>Account it runs under</label>
        <div class="chips">${accts.map(a=>`<span class="chip${(LOOPACCT||(P?P.primary_cfgdir:''))===(a.dir||'')?' on':''}"
          onclick='loopAcct(${hesc(a.dir||'')})'>${esc(a.name)}</span>`).join('')}</div></div>`:''}`:''}
      <div class="mrow"><span id="loopPreview" style="flex:1;color:var(--dim);font-size:12px;font-family:var(--mono)"></span>
        <button class="btn pri" onclick="loopStart()">${ic('play')} ${LOOPKIND==='schedule'?'Schedule it':'Start in a new session'}</button></div>`:''}</div>

    <div class="card"><h3>${ic('doc')} loop.md <span class="sp"></span>
      <button class="btn sm" onclick="loopAI()">${ic('ai')} Build with AI</button>
      <button class="btn sm pri" onclick="loopSaveHere()">Save</button></h3>
      <p style="color:var(--dim);font-size:13px;margin:0 0 8px">What a bare <code>/loop</code> does, and what a background loop runs when you leave the prompt empty. The project file wins wherever both exist, edits apply from the next iteration, and saving it empty deletes the file. Keep it short — anything past 25,000 bytes is truncated.</p>
      ${path?`<div class="chips" style="margin:0 0 10px">
        <span class="chip${scope==='project'?' on':''}" onclick="loopScope('project')">${esc(P.name)}${CUR?'':' (last opened)'}</span>
        <span class="chip${scope==='user'?' on':''}" onclick="loopScope('user')">account — every project</span></div>`:''}
      <textarea id="loopMd" style="min-height:200px;font-family:var(--mono)">${esc(md.text||'')}</textarea>
      <div style="color:var(--dim2);font-size:12px;margin-top:6px">${esc(md.file||'')}${md.exists?'':' — does not exist yet'}</div></div>`))return;
  const upd=()=>{const e=$('#loopPreview');if(!e)return;
    const i=($('#loopInt').value||'').trim(),p=($('#loopPrompt').value||'').trim();
    e.textContent=LOOPKIND==='schedule'
      ?`claude -p ${p?JSON.stringify(p):'<loop.md>'} --permission-mode ${LOOPPERM}  ·  every ${i||'?'}`
      :['/loop',i,p].filter(Boolean).join(' ');};
  if($('#loopInt')){$('#loopInt').oninput=upd;$('#loopPrompt').oninput=upd;upd();}
  // one poll while the page is open, and it stops with the page — the same
  // discipline as the dashboard: nothing keeps fetching for a detached view
  stopLoopPoll();
  if(live.length)_loopTimer=setTimeout(()=>{if(PAGE_==='loops')drawPage('loops');},15000);
}
function loopKind(k){LOOPKIND=k;drawPage('loops');}
function loopPerm(p){LOOPPERM=p;drawPage('loops');}
function loopAcct(d){LOOPACCT=d;drawPage('loops');}
function loopLog(id){
  const r=(window._loopRows||[]).find(x=>x.id===id);if(!r)return;
  drawerText('Loop · '+r.name,(r.journal||[]).map(e=>
    `${new Date((e.at||0)*1000).toLocaleString()}  ${e.ok?'ok':'FAILED'}  ${e.cost?'$'+(+e.cost).toFixed(3)+'  ':''}${e.secs}s\n${e.text||''}\n`
  ).join('\n')||'(no runs yet)');
}
async function loopRenew(id){
  const P=projCtx();
  const r=await post('/api/loops/stop',{id,renew:true,cfgdir:LOOPACCT||(P?P.primary_cfgdir:'')});
  toast(r.message||'',r.ok?'ok':'err');drawPage('loops');
}
function stopLoopPoll(){if(_loopTimer){clearTimeout(_loopTimer);_loopTimer=null;}}
function loopScope(sc){LOOPSCOPE=sc;drawPage('loops');}
async function loopStart(){
  const P=projCtx();if(!P){toast('Open a project first','err');return;}
  const r=await post('/api/loops/start',{path:P.path,enc:P.encoded,
    cfgdir:LOOPACCT||P.primary_cfgdir,project_name:P.name,
    kind:LOOPKIND,perm:LOOPPERM,
    interval:($('#loopInt').value||'').trim(),
    prompt:($('#loopPrompt').value||'').trim()});
  toast(r.ok?(r.message||('Started: '+r.text)):('Failed: '+(r.error||'')),r.ok?'ok':'err');
  drawPage('loops');
}
async function loopStop(id){
  const r0=(window._loopRows||[]).find(x=>x.id===id)||{};
  const ask=r0.kind==='schedule'
    ?['Remove this loop from the scheduler?','It cannot fire again. Nothing that has already run is undone.']
    :['End the session this loop runs in?','A /loop is session-scoped, so stopping it from outside means closing its session — anything else that session was doing ends too.'];
  if(!await confirmBox(ask[0],ask[1]))return;
  const P=projCtx();
  const r=await post('/api/loops/stop',{id,cfgdir:LOOPACCT||(P?P.primary_cfgdir:'')});
  toast(r.message||'',r.ok?'ok':'err');drawPage('loops');
}
async function loopForget(id){
  const P=projCtx();
  await post('/api/loops/stop',{id,forget:true,cfgdir:P?P.primary_cfgdir:''});
  drawPage('loops');
}
async function loopSaveHere(){
  const P=projCtx();
  const scope=(P&&LOOPSCOPE==='project')?'project':'user';
  const body={scope,text:$('#loopMd').value};
  if(scope==='project'){body.path=P.path;body.cfgdir=P.primary_cfgdir;}
  else body.cfgdir=P?P.primary_cfgdir:undefined;
  const r=await post('/api/loop-md',body);
  toast(r.removed?'loop.md removed':'Saved','ok');
  drawPage('loops');
}
async function loopAI(){
  const P=projCtx();
  const scope=(P&&LOOPSCOPE==='project')?'project':'user';
  const v=await ask('Build loop.md with AI',
    [{label:'What should each iteration do?',type:'textarea',
      ph:'check the release PR: if CI is red pull the failing log and push a minimal fix'}],
    'Claude drafts the file; you approve the text before anything is written.');
  if(v===null||!v[0].trim())return;
  inlineJob('#jban','loop_ai',{scope,description:v[0],
      path:P?P.path:'',cfgdir:P?P.primary_cfgdir:''},
    {label:'Writing loop.md',redraw:()=>drawPage('loops')});
}

async function convSync(){
  const r=await post('/api/conventions/sync',{cfgdir:GMDACCT||undefined});
  toast(r.ok?'Promoted into the global CLAUDE.md':'Nothing to promote',r.ok?'ok':'err');
  drawPage('globalmd');
}
function mcpAcct(dir){MCPACCT=dir;drawPage('mcp');}
async function gmSave(){
  const r=await post('/api/global-claude-md',{cfgdir:GMDACCT||undefined,text:$('#gmText').value});
  toast(r.ok?'Saved':'Write failed',r.ok?'ok':'err');}
async function mcpDetail(name){
  const r=await api('/api/mcp/detail?'+qs(Object.assign({name},MCPACCT?{cfgdir:MCPACCT}:{})));
  drawerText(`MCP · ${name}`,r.text||'(no details)');}
/* the analyze job now RETURNS the doc, so it can be read instead of only
   written into a file the GUI had no way to open */
function mcpDocs(name){
  inlineJob('#jban','mcp_analyze',Object.assign({name},MCPACCT?{cfgdir:MCPACCT}:{}),
    {label:`Analyzing MCP ${name}`,
     onDone:r=>{toast((r&&r.written)?'Tool docs written to global CLAUDE.md':'Analyzed','ok');
       if(r&&r.doc)drawerText(`MCP tools · ${name}`,r.doc);}});
}
async function mcpAdd(){
  const stdio=await ask('Add MCP server',[
    {label:'Name'},{label:'Transport',type:'select',
     options:[['stdio','stdio (command)'],['sse','sse (url)'],['http','http (url)']]},
    {label:'Command or URL'},
    {label:'Scope',type:'select',options:[['local','local'],['user','user'],['project','project']]},
    /* env vars for stdio, headers for http/sse — mirrors the TUI's
       _mcp_add_with_extras, which the GUI silently dropped, so a server needing
       a token could be added from the terminal and not from here */
    {label:'Env vars (stdio) — one KEY=VALUE per line',type:'textarea'},
    {label:'Headers (http/sse) — one "Name: value" per line',type:'textarea'}]);
  if(stdio===null)return;
  const body={name:stdio[0],transport:stdio[1],scope:stdio[3],
              cfgdir:MCPACCT||undefined};
  if(stdio[1]==='stdio'){body.command=stdio[2];body.env=stdio[4];}
  else {body.url=stdio[2];body.headers=stdio[5];}
  const r=await post('/api/mcp/add',body);
  toast(r.ok?'Added':'Failed: '+(r.error||''),r.ok?'ok':'err');drawPage('mcp');
}
async function mcpRemove(name){
  if(!await confirmBox(`Remove MCP server '${name}'?`))return;
  const r=await post('/api/mcp/remove',{name,cfgdir:MCPACCT||undefined});
  toast(r.ok?'Removed':'Failed: '+(r.error||''),r.ok?'ok':'err');drawPage('mcp');
}

/* `user` and `project · claudectl` are the storage scopes; the reader's
   question is who can call the thing. Same move as the hook events: say the
   answer, keep the raw word only where it is the identifier. */
/* `01-core-development` is a sort key wearing a heading's clothes. The number
   orders the folders on disk and means nothing to a reader; the words do. The
   raw name stays in each row's filter haystack, so searching for it still
   works. */
function agCat(c){
  const s=String(c||'').replace(/^\d+[-_]/,'').replace(/[-_]/g,' ');
  return s.charAt(0).toUpperCase()+s.slice(1);
}
const NO_CAT='Uncategorised';
/* Group installed agents the way the library is grouped — by category, with
   the unfiled ones last. `.fgrp` so the filter can take a heading away with
   the rows under it. */
function agByCat(rows,render){
  const by={};
  rows.forEach(r=>{const c=r.category||NO_CAT;(by[c]=by[c]||[]).push(r);});
  const names=Object.keys(by).sort((a,b)=>
    a===NO_CAT?1:b===NO_CAT?-1:a.localeCompare(b));
  return names.map(c=>`<div class="fgrp"><div class="hkwhen">
      <span>${esc(agCat(c))}</span><code>${by[c].length}</code></div>
    ${by[c].map(render).join('')}</div>`).join('');
}
function agScope(sc){
  const s=String(sc||'');
  if(s==='user')return 'every project';
  if(s.startsWith('project'))return 'only '+(s.split('·')[1]||'this project').trim();
  return s;
}
async function pgAgents(nav){
  await loadProv();
  const d=await api('/api/agents/library');
  const agRow=(a,extra,scope,raw)=>`
    <div class="agrow" data-f="${esc([a.name,a.desc,scope||'',raw||'',a.category||'',agCat(a.category||'')].join(' '))}">
      <div class="agmain"><b>${esc(a.name)}</b>${provTag('agent',a.name)}
        ${scope?`<span class="tag">${esc(scope)}</span>`:''}
        <div class="agdesc">${esc(a.desc)||'<span style="color:var(--dim2)">No description — Claude can only reach this one if you name it.</span>'}</div></div>
      <button class="btn sm" onclick='agView(${hesc(a.path)})'>view</button>${extra||''}</div>`;
  /* Divided by category, both lists. A flat roll of everything installed is
     the same wall the library would be without its folders, and it gets worse
     with every agent you write. `Uncategorised` is a real heading rather than
     a gap, and it sorts LAST so it never leads the page. */
  const own=agByCat(d.own||[],a=>agRow(a,
    `<button class="btn sm" onclick='post("/api/open-editor",{file:${hesc(a.path)}})'>edit</button>
     <button class="btn sm danger" onclick='agDel(${hesc(a.path)})'>${ic('del')}</button>`,
    agScope(a.scope)));
  const nLib=(d.categories||[]).reduce((n,c)=>n+c.agents.length,0);
  const lib=(d.categories||[]).map(c=>`
    <details style="margin-bottom:6px"><summary style="cursor:pointer;font-weight:600">${esc(agCat(c.category))} <span class="tag">${c.agents.length}</span></summary>
    ${c.agents.map(a=>agRow(a,'',agCat(c.category),c.category)).join('')}
    </details>`).join('');
  if(!paint(nav,`
    <div class="card"><h3>${ic('robot')} Your agents <span class="tag">${(d.own||[]).length}</span><span class="sp"></span>
      <button class="btn sm" onclick="agNew()">${ic('add')} Write one</button>
      <button class="btn sm" onclick="agAI()">${ic('ai')} Have Claude write one</button>
      <button class="btn sm" title="Rewrite every agent's description into trigger form (Use PROACTIVELY when …) — across every account, every project and the library. You approve the whole diff first; bodies and every other frontmatter field are untouched."
        onclick="agSharpen('all')">${ic('ai')} Sharpen descriptions</button></h3>
      <p class="secthint">An agent is a <b>second Claude</b> with its own instructions, its own tools and its own context window. Claude hands it a job, it works in that separate window, and only its answer comes back — so a long search never fills up the conversation you are having.</p>
      <p class="secthint">It gets picked by matching your task against its <b>description</b>, and nothing reads the body to decide. That is why a vague description means the agent never runs: <i>Sharpen descriptions</i> rewrites that one field everywhere the agent is installed, so the fix lands once instead of per project.</p>
      ${own||'<div class="empty">No agents of your own yet. Write one, or install one from the library below.</div>'}</div>
    <div class="card"><h3>${ic('folder')} Ready-made agents <span class="tag">${nLib}</span></h3>
      <p class="secthint">Specialists that ship with claudectl, by trade. Too many to read down — type to narrow, and the filter opens the categories that match and covers your own list above too.</p>
      <div class="fld" style="margin:0 0 8px"><input id="agQ" placeholder="Filter by name, description or trade…" spellcheck="false">
        <div style="color:var(--dim2);font-size:12px;margin-top:4px" id="agQCount"></div></div>
      ${lib||'<div class="empty">Library is empty.</div>'}</div>`))return;
  bindFilter('agQ','.agrow','agQCount');
}
/* the existing #drawer, shown with plain text. Everything that used to have no
   way to display its own output — `claude mcp get`, an MCP tool-doc analysis —
   goes here rather than each growing its own overlay. */
function drawerText(title,text){
  $('#dTitle').textContent=title;
  $('#dBody').innerHTML=`<div class="msg"><div class="body">${esc(text||'')}</div></div>`;
  $('#drawer').classList.add('show');
}
async function agView(path){
  const d=await api('/api/agents/read?file='+encodeURIComponent(path));
  $('#dTitle').textContent=d.meta&&d.meta.name||'Agent';
  $('#dBody').innerHTML=`<div class="card"><div class="kv">
    <span class="k">Tools</span><span>${esc(d.meta.tools||'(all)')}</span>
    <span class="k">Model</span><span>${esc(d.meta.model||'(inherit)')}</span></div></div>
    <div class="msg"><div class="body">${esc(d.body)}</div></div>`;
  $('#drawer').classList.add('show');
}
async function agNew(){
  const d=await api('/api/agents/library'+(CUR?'?'+qs({path:CUR.path}):''));
  const v=await ask('New agent',[{label:'Name'},{label:'Description'},
    {label:'Category — pick one, or type a new name',
     list:d.category_names||[],ph:NO_CAT},
    {label:'Scope',type:'select',options:[['user','user (all projects)'],['project','this project']]},
    {label:'Tools — none selected inherits all of them',type:'multi',
     options:(d.known_tools||[]).map(t=>[t,t])},
    {label:'Model',type:'select',
     options:[['','inherit'],...(d.models||[]).map(m=>[m.id,m.label])]},
    {label:'System prompt / instructions',type:'textarea'}],
    'The category is claudectl’s own filing, not something Claude Code reads — it only decides which heading the agent appears under here.');
  if(v===null||!v[0].trim())return;
  const r=await post('/api/agents/create',{name:v[0],description:v[1],
    category:v[2],scope:v[3],tools:v[4],model:v[5],path:CUR?CUR.path:'',body:v[6]});
  toast(r.ok?'Agent created':'Failed','ok');drawPage('agents');
}
async function agAI(){
  /* The same fields agNew() collects, because the agent lands in the same
     place. The old modal asked for one textarea and the server fed it in as
     the agent's NAME — it was the only field the TUI flow's first text_input
     would have read, had that flow been reachable from a job thread at all. */
  const d=await api('/api/agents/library'+(CUR?'?'+qs({path:CUR.path}):''));
  const v=await ask('Have Claude write an agent',[
    {label:'Name',ph:'security-reviewer'},
    {label:'What should this agent do?',type:'textarea'},
    {label:'Category — pick one, or type a new name',
     list:d.category_names||[],ph:NO_CAT},
    {label:'Scope',type:'select',options:CUR
      ?[['user','user (all projects)'],['project','this project']]
      :[['user','user (all projects)']]}],
    'Claude writes the description, tool list, model and system prompt. It lands in '
    +'.claude/agents/ — where Claude Code reads them — and you can edit it after.');
  if(v===null||!v[0].trim())return;
  inlineJob('#jban','agent_ai',{path:CUR?CUR.path:'',name:v[0],
      description:v[1],category:v[2],scope:v[3]},
    {label:'Generating agent',redraw:()=>drawPage('agents')});
}
async function agDel(path){
  if(!await confirmBox('Delete this agent?'))return;
  const r=await post('/api/agents/delete',{file:path});
  toast(r.ok?'Deleted':'Failed: '+(r.error||''),r.ok?'ok':'err');drawPage('agents');
}

/* ── Plugins ────────────────────────────────────────────────────────────────
   A plugin is the canonical unit of distribution now — a versioned bundle of
   skills, subagents, commands, hooks and MCP servers. claudectl managed every
   one of those individually and could not see the bundle around them.

   The listing is table stakes; `/plugin` already does it. The part worth
   building is PROVENANCE, which is why every row states what it contributed —
   and why the skill/agent/hook managers now badge entries that came from here.
   Without it those lists are unreadable after two marketplace installs, and the
   obvious action on something unrecognised (delete it) can break a plugin. */
/* which account the plugin page is showing. '' = the active one. */
let PLACCT='';
async function pgPlugins(nav){
  const [d,V]=await Promise.all([api('/api/plugins?'+qs(PLACCT?{cfgdir:PLACCT}:{})),
                                api('/api/versions').catch(()=>({}))]);
  VER=V||{};
  const accts=(d.accounts||[]);
  /* The confirmation that a plugin reached every account. It only ever showed
     whichever account was active, so four of five having nothing at all was
     invisible from the one page that exists to answer this. */
  const spread=names=>{
    const miss=accts.filter(a=>!(names||[]).includes(a.name)).map(a=>a.name);
    return miss.length?`<span class="tag warn" title="missing on ${esc(miss.join(', '))}">${accts.length-miss.length}/${accts.length} accounts</span>`
      :`<span class="tag ok" title="on ${esc((names||[]).join(', '))}">all ${accts.length} accounts</span>`;
  };
  const picker=accts.length>1?`<div class="chips" style="margin-bottom:10px">
      ${accts.map(a=>`<span class="chip${(PLACCT||'')===(a.dir||'')?' on':''}"
        onclick='plAcct(${hesc(a.dir||'')})'
        title="${a.plugins} plugin(s), ${a.marketplaces} marketplace(s)">${esc(a.name)} <b>${a.plugins}</b></span>`).join('')}
    </div>`:'';
  const mkts=(d.marketplaces||[]).map(m=>`
    <div class="hrow">
      <b style="min-width:200px">${esc(m.name)}</b>
      <span class="tag">${esc(m.source||'?')}</span>
      ${accts.length>1?spread(m.on_accounts):''}
      <span style="flex:1;color:var(--dim);font-size:12px">${esc(m.repo||m.path)}</span>
      <button class="btn sm danger" onclick='mktRemove(${hesc(m.name)})'>${ic('del')}</button>
    </div>`).join('');
  const vrows={};((V||{}).plugins||[]).forEach(r=>{vrows[r.key]=r;});
  const plugs=(d.plugins||[]).map(p=>{
    const gives=Object.entries(p.provides||{})
      .map(([k,v])=>`<span class="tag">${esc(k)} ${v.length}</span>`).join(' ');
    const vr=vrows[p.key]||{};
    return `<div class="hrow">
      <b style="min-width:200px">${esc(p.name)}</b>
      <span class="tag">${esc(p.marketplace)}</span>
      <span class="num" style="color:var(--dim2)">${esc(p.version)}</span>
      ${vr.outdated?`<span class="tag warn" title="the marketplace offers ${esc(vr.available||'')}">${esc(vr.available||'update')} available</span>`
        :(vr.outdated===false?'<span class="tag ok">current</span>':'')}
      ${p.missing?'<span class="tag warn">files missing</span>':''}
      ${accts.length>1?spread(p.on_accounts):''}
      <span style="flex:1">${gives||'<span style="color:var(--dim2);font-size:12px">ships nothing claudectl reads</span>'}</span>
      ${(accts.length>1&&(p.on_accounts||[]).length<accts.length)
        ?`<button class="btn sm pri" onclick='pluginSpread(${hesc(p.name)},${hesc(p.marketplace)})' title="Install it into the accounts that do not have it">Install everywhere</button>`:''}
      ${vr.outdated?`<button class="btn sm pri" onclick='pluginUpdate(${hesc(p.key)})'>Update</button>`:''}
      <button class="btn sm danger" onclick='pluginRemove(${hesc(p.key)})'>${ic('del')}</button>
    </div>`;}).join('');
  paint(nav,`
    ${selfCard(V)}
    ${verCard(V)}
    ${modelCard(V)}
    <div class="card" id="pluginCard"><h3>${ic('folder')} Installed plugins</h3>
      <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px">A plugin bundles skills, subagents, commands, hooks and MCP servers together. The tags say what each one actually placed on disk — the same information the Skills, Agents and Hooks pages now use to mark which of their rows came from a bundle rather than from you.${accts.length>1?' The <b>accounts</b> tag says how many of your logins have it: a plugin is a property of you, not of whichever account happened to be active when you installed it.':''}</p>
      ${picker}
      ${plugs||'<div style="color:var(--dim)">No plugins installed.</div>'}</div>
    <div class="card"><h3>Marketplaces <span class="sp"></span>
      <button class="btn sm" onclick="mktRefresh()">${ic('refresh')} Refresh</button>
      <button class="btn sm pri" onclick="mktAdd()">${ic('add')} Add marketplace</button></h3>
      <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px">A repo, a URL or a local path. Adding, installing and removing are delegated to the <code>claude</code> CLI: these files belong to Claude Code, the format has already changed once, and writing them directly would corrupt the state of the tool claudectl exists to support.</p>
      ${mkts||'<div style="color:var(--dim)">No marketplaces registered.</div>'}</div>
    <div class="card"><h3>Where they live</h3>
      <div class="kv"><span>plugins dir</span><code>${esc(d.dir||'')}</code></div>
      ${accts.length>1?`<div class="kv"><span>per account</span><span>${accts.map(a=>
        `${esc(a.name)} <b>${a.plugins}</b> plugin(s), <b>${a.marketplaces}</b> marketplace(s)`).join(' &nbsp;·&nbsp; ')}</span></div>`:''}
      </div>`);
}
/* Claude Code's own version. The card is on the Plugins page rather than on a
   page of its own because "what is installed and is it current" is one
   question asked of the whole toolchain, and a plugin update and a Claude Code
   update are the same kind of action with the same kind of risk. */
let VER=null;
/* claudectl's own version. A separate card from Claude Code's on purpose: it
   has no channel and no rollback (PyPI is not a versions directory on disk),
   and the one thing it does have that Claude Code's does not is a refusal —
   a checkout is updated with git, not by installing a release over it. */
function selfCard(V){
  const c=(V||{}).claudectl||{};
  if(!c.installed&&!c.error)return '';
  const state=c.error?`<span class="tag warn">could not check (${esc(c.error)})</span>`
    :c.update?`<span class="tag warn">${esc(c.latest)} available</span>`
    :(c.current?'<span class="tag ok">current</span>':'');
  const act=(c.update&&c.mode!=='checkout')
    ? `<button class="btn sm pri" onclick="selfUpdate()">Update claudectl</button>`:'';
  return `<div class="card" id="selfCard"><h3>${ic('bolt')} claudectl ${state}
      <span class="sp"></span>
      <button class="btn sm" onclick="verCheck()">${ic('refresh')} Check now</button>${act}
    </h3>
    <div class="kv"><span>installed</span><code>${esc(c.installed||'unknown')}</code></div>
    <div class="kv"><span>latest on PyPI</span><code>${esc(c.latest||'?')}</code></div>
    <div class="kv"><span>install mode</span><code>${esc(c.mode||'?')}</code>
      ${c.mode==='checkout'?'<span style="color:var(--dim2);font-size:12px">a git checkout — update it with <code>git pull</code>, not with pip</span>'
        :'<span style="color:var(--dim2);font-size:12px">the upgrade runs in its own window after claudectl exits, because pip cannot rewrite the script it is running from</span>'}</div>
    </div>`;
}
function selfUpdate(){
  inlineJob('#selfCard','claudectl_update',{},
            {label:'Scheduling the claudectl upgrade',
             onDone:()=>toast('claudectl upgrades once you close it')});
}
/* The model catalogue. It sits with the other two because it answers the same
   question of a third thing — "is what claudectl is showing you still what
   Anthropic offers?" — and because the ONE state a user has to be able to see
   is that the picker fell back to the bundled list, which is not visible from
   the picker itself.

   The retired-pin warnings are the only per-model notice there is. A newly
   released model needs no announcement: it is simply in the picker. */
function modelCard(V){
  const m=(V||{}).models||{};
  const notes=(m.notices||[]);
  const age=m.age||0;
  const when=!m.live?'':(age<90?'just now':age<5400?`${Math.round(age/60)}m ago`
    :age<172800?`${Math.round(age/3600)}h ago`:`${Math.round(age/86400)}d ago`);
  const state=m.live?`<span class="tag ok">${m.count} models</span>`
    :'<span class="tag warn">using the bundled list</span>';
  return `<div class="card" id="modelCard"><h3>${ic('bolt')} Models ${state}
      <span class="sp"></span>
      <button class="btn sm" onclick="verCheck()">${ic('refresh')} Check now</button>
    </h3>
    <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px">Read from Anthropic with the login Claude&nbsp;Code already holds, once a day, so a model released this week reaches the launch picker without a claudectl release. When it cannot be read, the picker falls back to the list shipped with this version — never to an empty one.</p>
    ${m.live?`<div class="kv"><span>catalogue</span><code>${m.count} models across ${m.families} families</code>
      <span style="color:var(--dim2);font-size:12px">checked ${esc(when)}</span></div>`
      :'<div class="kv"><span>catalogue</span><code>not fetched</code><span style="color:var(--dim2);font-size:12px">a logged-out account, no network, or auto-update set to off</span></div>'}
    ${notes.map(n=>`<div class="hrow"><span class="tag warn">retired</span>
      <span style="flex:1;font-size:12.5px">${esc(n)}</span></div>`).join('')}
    </div>`;
}
function verCard(V){
  const c=(V||{}).claude||{};
  if(!c.installed&&!c.error)return '';
  const cur=c.current, beh=c.behind;
  const state=c.error?`<span class="tag warn">could not check (${esc(c.error)})</span>`
    :cur?'<span class="tag ok">current</span>'
    :(beh>0?`<span class="tag warn">${beh} release${beh===1?'':'s'} behind</span>`
           :'<span class="tag warn">update available</span>');
  const locals=(c.local||[]).filter(v=>v!==c.installed).slice(0,4)
    .map(v=>`<button class="btn sm" title="already downloaded — rolls back without a fetch"
      onclick='claudeUpdate(${hesc(v)})'>${esc(v)}</button>`).join(' ');
  return `<div class="card" id="verCard"><h3>${ic('bolt')} Claude Code ${state}
      <span class="sp"></span>
      <button class="btn sm" onclick="verCheck()">${ic('refresh')} Check now</button>
      ${cur?'':'<button class="btn sm pri" onclick="claudeUpdate(\'\')">Update to latest</button>'}
    </h3>
    <div class="kv"><span>installed</span><code>${esc(c.installed||'unknown')}</code></div>
    <div class="kv"><span>latest / stable</span><code>${esc(c.latest||'?')} / ${esc(c.stable||'?')}</code></div>
    <div class="kv"><span>channel</span><code>${esc(c.channel||'latest')}</code>
      <span style="color:var(--dim2);font-size:12px">autoUpdatesChannel — what an unpinned update follows</span></div>
    <div class="kv"><span>install mode</span><code>${esc(c.mode||'?')}</code>
      ${c.mode==='npm'?'<span style="color:var(--dim2);font-size:12px">npm owns this binary — claudectl will not install the native build over it</span>':''}</div>
    <div class="hrow" style="margin-top:8px">
      <button class="btn sm" onclick="verPick()">Install a specific version…</button>
      ${locals?`<span style="color:var(--dim2);font-size:12px">on disk:</span> ${locals}`:''}
    </div></div>`;
}
async function verCheck(){
  const V=await api('/api/versions?refresh=1');VER=V;
  toast(((V.claude||{}).error)||'Checked',(V.claude||{}).error?'err':'ok');
  drawPage('plugins');
}
async function verPick(){
  const c=(VER||{}).claude||{};
  const v=await ask('Install a specific Claude Code version',
    [{k:'v',label:'Version',ph:c.latest||'2.1.241'}],
    'An exact version, or the word stable or latest. Newer releases first: '
    +((c.versions||[]).slice(0,8).join(', ')||'list unavailable'));
  if(!v||!v[0])return;
  claudeUpdate(v[0].trim());
}
function claudeUpdate(target){
  inlineJob('#verCard','claude_update',{target:target||''},
            {redraw:()=>drawPage('plugins')});
}
function pluginUpdate(key){
  inlineJob('#pluginCard','plugin_update',{key},{redraw:()=>drawPage('plugins')});
}
function mktRefresh(){
  inlineJob('#pluginCard','marketplace_refresh',{},{redraw:()=>drawPage('plugins')});
}
function plAcct(dir){PLACCT=dir;drawPage('plugins');}
/* Adding registers the marketplace on EVERY account — a source you trust is a
   property of you. Removing acts on the one account on screen, because deleting
   from four logins you did not name is a surprise, not a fan-out. */
async function mktAdd(){
  const v=await ask('Add marketplace',[{k:'src',label:'Repo, URL or path',ph:'owner/repo'}],
    'Claude Code fetches and validates the manifest. It is registered on every account.');
  if(!v||!v[0])return;
  const r=await post('/api/plugins/marketplace/add',{source:v[0],scope:'all'});
  toast(r.message||(r.ok?'Added':'Failed'),r.ok?'ok':'err');drawPage('plugins');
}
async function mktRemove(name){
  if(!await confirmBox('Remove marketplace '+name+'?',
    'Only from the account shown above.'))return;
  const r=await post('/api/plugins/marketplace/remove',{name,cfgdir:PLACCT||undefined});
  toast(r.message||'',r.ok?'ok':'err');drawPage('plugins');
}
async function pluginSpread(name,marketplace){
  if(!await confirmBox(`Install ${name} into every account?`,
    'A plugin ships agents and hooks straight into the auto-discovery surfaces, '
    +'so each account it reaches is another place its code runs.'))return;
  const r=await post('/api/plugins/install',{name,marketplace,scope:'all'});
  toast(r.message||(r.ok?'Installed':'Failed'),r.ok?'ok':'err');drawPage('plugins');
}
async function pluginRemove(key){
  if(!await confirmBox('Uninstall '+key+'? Anything it contributed goes with it.',
    'Only from the account shown above.'))return;
  const r=await post('/api/plugins/remove',{key,cfgdir:PLACCT||undefined});
  toast(r.message||'',r.ok?'ok':'err');drawPage('plugins');
}

/* Provenance badge. Fetched once and cached: each manager renders a flat list,
   and "did I install this or did a bundle?" is the one question that list
   cannot otherwise answer. */
let PROV=null;
async function loadProv(){
  if(PROV)return PROV;
  try{PROV=(await api('/api/plugins/provenance')).provenance||{};}catch(e){PROV={};}
  return PROV;
}
function provTag(kind,name){
  const src=((PROV||{})[kind]||{})[name];
  return src?`<span class="tag" title="Installed by the ${esc(src)} plugin — removing it here may break that plugin">${esc(src.split('@')[0])}</span>`:'';
}

/* ── Worktrees ──────────────────────────────────────────────────────────────
   Which agent is working where. claudectl could already launch into a worktree
   and had no idea what happened next; every tool in this category is built on
   the view that was missing.

   The join is the bit nobody else can make: a session's transcript records its
   cwd, a worktree is a path, so claudectl — which owns both — can say which
   SESSION is in which tree. And every one of them reads the same semantic
   memory, so they are not each rediscovering the codebase. */
function wtRow(w,repoPath,indent){
  const s=w.session,live=s&&s.live;
  return `<div class="hrow" style="padding-left:${indent}px">
      <span class="dot${live?' pip':''}" style="background:${live?'var(--ok)':'var(--dim2)'};color:var(--ok)"></span>
      <b style="min-width:${190-indent}px">${esc(w.name)}${w.main?' <span class="tag">main</span>':''}</b>
      <code style="min-width:190px;color:var(--dim)">${esc(w.branch)}</code>
      ${w.dirty?`<span class="tag warn">${w.dirty} uncommitted</span>`:'<span class="tag ok">clean</span>'}
      ${w.ahead?`<span class="tag">+${w.ahead}</span>`:''}
      ${w.behind?`<span class="tag warn">-${w.behind}</span>`:''}
      <span style="flex:1;color:var(--dim);font-size:12px">${
        s?`${esc(s.title||s.sid.slice(0,8))} · ${esc(s.account)} · ${s.msgs} msgs · ${live?'live now':fmtAge(s.age)}`
         :'<span style="color:var(--dim2)">no session here</span>'}</span>
      ${w.dirty?`<button class="btn sm" onclick='wtDiff(${hesc(w.path)})'>${ic('eye')} Diff</button>`:''}
      ${w.main?'':`<button class="btn sm" onclick='wtMerge(${hesc(repoPath)},${hesc(w.branch)})'>Merge</button>`}
    </div>`;
}
/* One repo and everything under it. <details> does the collapsing natively —
   no JS, no open/closed state to track, no icon to keep in sync. */
function repoGroup(r,multi,depth){
  const inner=(r.worktrees||[]).filter(w=>!w.main||!multi)
      .map(w=>wtRow(w,r.path,multi?18:0)).join('');
  const kids=(r.children||[]).map(c=>repoGroup(c,multi,depth+1)).join('');
  if(!multi)return inner;
  const live=(r.worktrees||[]).some(w=>w.session&&w.session.live);
  return `<details open class="rgrp" style="margin-left:${depth*14}px">
    <summary class="hrow" style="cursor:pointer">
      <span class="dot${live?' pip':''}" style="background:${live?'var(--ok)':'var(--dim2)'};color:var(--ok)"></span>
      <b style="min-width:200px">${esc(r.name)}</b>
      ${r.kind==='submodule'?'<span class="tag">submodule</span>':''}
      <code style="min-width:170px;color:var(--dim)">${esc(r.branch)}</code>
      ${r.dirty?`<span class="tag warn">${r.dirty} uncommitted</span>`:'<span class="tag ok">clean</span>'}
      ${r.ahead?`<span class="tag">+${r.ahead}</span>`:''}
      ${r.behind?`<span class="tag warn">-${r.behind}</span>`:''}
      <span style="flex:1"></span>
      ${(r.children||[]).length?`<span class="tag">${r.children.length} ${esc(r.sublabel)}</span>`:''}
    </summary>
    ${inner}${kids}</details>`;
}
async function drawWorktrees(){
  const nav=paintNow(LOADING);
  const d=await api('/api/worktrees?'+qs({path:CUR.path,enc:CUR.encoded,cfgdir:CUR.primary_cfgdir}));
  if(!d.repo){
    paint(nav,`<div class="card"><h3>Repos</h3>
      <div style="color:var(--dim)">No git repository here or below — nothing to show.</div></div>`);
    return;
  }
  const tops=d.repos||[],multi=!!d.multi;
  let nlive=0,nrepo=0;
  (function walk(list){list.forEach(r=>{nrepo++;
    nlive+=(r.worktrees||[]).filter(w=>w.session&&w.session.live).length;
    walk(r.children||[]);});})(tops);
  paint(nav,`
    <div class="card"><h3>${ic('fork')} Repos <span class="sp"></span>
      ${multi?`<span class="tag">${nrepo} repos</span>`:''}
      <span class="tag${nlive?' ok':''}">${nlive} live</span></h3>
      <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px">Every repo under this project — submodules and worktrees included — and the session working in each. All of them read the same project memory, so parallel agents are not each rediscovering the codebase.</p>
      ${tops.map(r=>repoGroup(r,multi,0)).join('')}</div>`);
}
function fmtAge(sec){
  if(sec<90)return 'just now';
  if(sec<5400)return Math.round(sec/60)+'m ago';
  if(sec<172800)return Math.round(sec/3600)+'h ago';
  return Math.round(sec/86400)+'d ago';
}
async function wtDiff(path){
  const d=await api('/api/worktree/diff?'+qs({wt:path}));
  await ask('Uncommitted changes',[],(d.diff||'(no diff)').slice(0,20000));
}
/* repoPath, not CUR.path: with many repos under one project a branch name no
   longer names a single repo, so the caller has to say which one it belongs to. */
async function wtMerge(repoPath,branch){
  if(!await confirmBox('Merge '+branch+' into the checked-out branch of '+repoPath+'?'))return;
  const r=await post('/api/worktree/merge',{path:repoPath,branch});
  toast(r.message||'',r.ok?'ok':'err');drawWorktrees();
}

/* ── Output styles ──────────────────────────────────────────────────────────
   The last Claude Code config surface claudectl did not manage. A style swaps
   the "how to behave" half of the system prompt — same tools, same permissions,
   different job — and it is set per project OR per account, which is precisely
   the pair claudectl already knows at launch. Clicking a card writes the
   `outputStyle` key and leaves every other key in that settings.json alone. */
/* ── Output styles ───────────────────────────────────────────────────────────
   The page was one undifferentiated grid of cards. It never said what a style
   IS (it replaces the behavioural half of the system prompt — not the tools,
   not CLAUDE.md, not a skill), where each one lives, or which of two files was
   actually in force. Clicking a card silently wrote the PROJECT settings.json,
   and with no project open that threw on `CUR.path`. `view` showed "(empty)"
   for the three built-ins, because those ship inside Claude Code and have no
   file — which reads as a broken button.

   Now: what is active and where it is pinned, then your styles by scope, then
   the built-ins, then claudectl's starters to copy. The scope of every write is
   a choice you can see. */
let OSSCOPE='user';
async function pgOStyles(nav){
  const P=projCtx();
  const path=P?P.path:'';
  if(!path)OSSCOPE='user';        // no project in play: there is only one target
  const d=await api('/api/output-styles?'+qs({path,cfgdir:P?P.primary_cfgdir:''}));
  const where=d.active_scope==='project'?`this project's <code>.claude/settings.json</code>`
    :d.active_scope==='user'?`your account's <code>settings.json</code>`:'';
  const card=st=>`
    <div class="preset${st.active?' on':''}" onclick='osPick(${hesc(st.name)})'>
      <b>${esc(st.name)}</b>
      <span>${esc(st.description||'No description.')}</span>
      <div style="display:flex;align-items:center;gap:6px;margin-top:6px;flex-wrap:wrap">
        ${st.active?'<span class="tag ok">in force</span>':''}
        ${st.lines?`<span class="tag">${st.lines} lines</span>`:''}
        <button class="btn sm" onclick='event.stopPropagation();osView(${hesc(st.name)})'>view</button>
        ${st.builtin||st.scope==='starter'?'':`<button class="btn sm danger" onclick='event.stopPropagation();osDel(${hesc(st.name)})'>${ic('del')}</button>`}
      </div></div>`;
  const starter=st=>`
    <div class="preset">
      <b>${esc(st.name)}</b>
      <span>${esc(st.description)}</span>
      <div style="display:flex;align-items:center;gap:6px;margin-top:6px;flex-wrap:wrap">
        <button class="btn sm" onclick='osView(${hesc(st.name)})'>view</button>
        <button class="btn sm pri" onclick='osInstall(${hesc(st.name)})'>copy to ${OSSCOPE}</button>
      </div></div>`;
  const of=sc=>(d.styles||[]).filter(x=>x.scope===sc);
  const grid=(rows,empty)=>rows.length
    ?`<div class="presetrow">${rows.map(card).join('')}</div>`
    :`<div class="empty">${empty}</div>`;
  const scopePick=path?`<div class="chips" style="margin:0 0 10px">
      <span class="chip${OSSCOPE==='user'?' on':''}" onclick="osScope('user')">account — every project</span>
      <span class="chip${OSSCOPE==='project'?' on':''}" onclick="osScope('project')">${esc(P.name)} only${CUR?'':' (last opened)'}</span>
    </div>`:'';

  paint(nav,`
    <div class="card"><h3>${ic('palette')} Output styles <span class="sp"></span>
      <button class="btn sm pri" onclick="osNew()">${ic('add')} New style</button></h3>
      <p style="color:var(--dim);font-size:13px;margin:0 0 8px">A style replaces the <b>behavioural</b> half of Claude Code's system prompt — how it talks and works. Tools, permissions, CLAUDE.md and skills are untouched: this is voice and method, not knowledge. It is a markdown file with YAML frontmatter, and selecting one writes <code>outputStyle</code> into a settings.json, leaving every other key in that file alone.</p>
      <div class="pghd" style="margin-bottom:8px"><div class="pghdt">
        <b>${esc(d.active||'default')}</b> is in force${where?' — pinned in '+where:' (nothing pins a style, so Claude Code uses its own default)'}
        <div class="sub">A project's settings.json shadows your account's, so a project pin wins wherever both name a style.</div></div></div>
      <p style="color:var(--dim);font-size:12.5px;margin:0">Writes below go to: ${scopePick?'':'<b>your account</b> — open a project to target it instead.'}</p>
      ${scopePick}</div>

    <div class="card"><h3>Your styles <span class="tag">account</span></h3>
      <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px"><code>${esc(d.user_dir||'')}</code> — available in every project on this account.</p>
      ${grid(of('user'),'None yet. Copy a starter below, or write one.')}</div>

    ${path?`<div class="card"><h3>This project <span class="tag">${esc(P.name)}</span></h3>
      <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px"><code>${esc(d.project_dir||'')}</code> — only here, and committed with the repo if you commit it.</p>
      ${grid(of('project'),'No project-specific styles.')}</div>`:''}

    <div class="card"><h3>Built into Claude Code</h3>
      <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px">Shipped inside Claude Code, so there is no file to edit — copy a starter or write your own to replace one.</p>
      ${grid(of('built-in'),'None reported.')}</div>

    <div class="card"><h3>${ic('download')} Starters from claudectl</h3>
      <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px">Four jobs Claude Code does not ship a style for. Copying one writes a normal markdown file you own and can edit — nothing stays linked to claudectl.</p>
      <div class="presetrow">${(d.starters||[]).map(starter).join('')}</div></div>`);
}
function osScope(sc){OSSCOPE=sc;drawPage('ostyles');}
async function osView(name){
  const P=projCtx();
  const d=await api('/api/output-style/read?'+qs({name,
    path:P?P.path:'',cfgdir:P?P.primary_cfgdir:''}));
  drawerText('Output style · '+name,d.body||'(this style has no text)');
}
/* Every write names its scope explicitly. It used to pass scope:'project'
   unconditionally and read CUR.path — which threw on a page that is reachable
   with no project open at all. */
function osTarget(){
  const P=projCtx();
  return OSSCOPE==='project'&&P
    ?{scope:'project',path:P.path,cfgdir:P.primary_cfgdir}
    :{scope:'user',cfgdir:P?P.primary_cfgdir:''};
}
async function osPick(name){
  const r=await post('/api/output-style/select',{name,...osTarget()});
  toast(r.message||'',r.ok?'ok':'err');drawPage('ostyles');
}
async function osInstall(name){
  const r=await post('/api/output-style/install',{name,...osTarget()});
  toast(r.message||'',r.ok?'ok':'err');drawPage('ostyles');
}
async function osNew(){
  const v=await ask('New output style',[
    {label:'Name',ph:'code-review'},
    {label:'One-line description'},
    {label:'Instructions',type:'textarea'}],
    'Saved as a markdown file with YAML frontmatter — the format Claude Code reads.');
  if(!v||!v[0])return;
  const r=await post('/api/output-style/save',
    {name:v[0],description:v[1],body:v[2],...osTarget()});
  toast(r.message||'',r.ok?'ok':'err');drawPage('ostyles');
}
async function osDel(name){
  if(!await confirmBox('Delete output style '+name+'?'))return;
  const P=projCtx();
  const r=await post('/api/output-style/delete',
    {name,path:P?P.path:'',cfgdir:P?P.primary_cfgdir:''});
  toast(r.message||'',r.ok?'ok':'err');drawPage('ostyles');
}

/* ── Checkpoints (READ-ONLY) ────────────────────────────────────────────────
   Claude Code snapshots a file before editing it so /rewind can restore it.
   The store's naming scheme is NOT documented, so the backend proves it holds
   on every call by hashing the paths this session actually edited; if nothing
   resolves it reports recognised:false and we say so instead of rendering a
   guess. Restoring stays with /rewind — claudectl only ever reads here. */
async function ckptS(i){
  const s=SESS[i];
  $('#dTitle').textContent='Checkpoints — '+(s.title||s.sid.slice(0,8));
  $('#dBody').innerHTML=LOADING;$('#drawer').classList.add('show');
  const d=await api('/api/checkpoints?'+qs({enc:CUR.encoded,
    cfgdir:s.cfgdir||CUR.primary_cfgdir,sid:s.sid}));
  const body=$('#dBody');if(!body)return;
  if(!d.store){body.innerHTML='<div class="empty">Claude Code kept no snapshots for this session.</div>';return;}
  if(!d.recognised){
    body.innerHTML=`<div class="card"><h3>Format not recognised</h3>
      <p style="color:var(--dim);font-size:13px">Snapshots exist for this session, but none of the files it edited match them. Claude Code's checkpoint layout is undocumented and appears to have changed, so claudectl is showing nothing rather than guessing which snapshot belongs to which file. <code>/rewind</code> inside the session still works.</p></div>`;
    return;
  }
  body.innerHTML='<div class="card"><h3>'+ic('history')+' Snapshots before each edit</h3>'
    +'<p style="color:var(--dim);font-size:12.5px;margin:0 0 8px">Read-only. Restoring is <code>/rewind</code> inside the session — claudectl never writes to this store.</p>'
    +(d.files||[]).map(f=>{
      const vs=f.versions;
      const chain=vs.map((v,n)=>{
        const prev=n?vs[n-1].v:0;
        return prev?`<button class="btn sm" onclick='ckptDiff(${hesc(s.sid)},${hesc(f.path)},${prev},${v.v},${hesc(s.cfgdir||"")})'>v${prev}→v${v.v}</button>`
                   :`<span class="tag">v${v.v}</span>`;}).join(' ');
      return `<div class="hrow">
        <b style="min-width:220px" title="${esc(f.path)}">${esc(f.name)}</b>
        <span class="tag">${vs.length} version${vs.length>1?'s':''}</span>
        <span style="flex:1;color:var(--dim);font-size:12px">${esc(f.path)}</span>
        ${chain}</div>`;}).join('')
    +(d.orphans?`<div style="color:var(--dim2);font-size:12px;margin-top:8px">${d.orphans} snapshot${d.orphans>1?'s':''} could not be matched to a file this session edited — most likely written before the part of the transcript that survives. Counted, not guessed at.</div>`:'')
    +'</div>';
}
async function ckptDiff(sid,file,a,b,cfgdir){
  const d=await api('/api/checkpoint/diff?'+qs({enc:CUR.encoded,
    cfgdir:cfgdir||CUR.primary_cfgdir,sid,file,a,b}));
  await ask(`v${a} → v${b}`,
    [{label:file,type:'textarea',value:(d.diff||'(identical)').slice(0,20000)}],
    'Read-only. Restoring is /rewind inside the session.');
}

/* ── Skills ─────────────────────────────────────────────────────────────────
   The scopes here are CLAUDE CODE'S, not ours. It loads personal
   (<account>/skills), project (<project>/.claude/skills), plugin skills and its
   own bundled ones — and nothing else. The old page led with a "Project skills"
   card that was inert unless a project happened to be open, and offered "copy to
   library", which wrote into ~/.claude/claudectl-skills: a directory nothing
   reads. So it listed files that could never run and said nothing about what
   actually does.

   Every row now carries the command you type and its real usage count, straight
   from Claude Code's own skillUsage counters. */
let SKACCT='',SKSCOPE='all';
async function pgSkills(nav){
  const P=projCtx();
  const path=P?P.path:'';
  await loadProv();          // badge rows a plugin brought in — see provTag
  const d=await api('/api/skills?'+qs(Object.assign(path?{path}:{},SKACCT?{cfgdir:SKACCT}:{})));
  const accts=d.accounts||[];
  const all=[].concat(d.personal||[],d.project||[],d.plugin||[],d.bundled||[]);
  const n=sc=>sc==='all'?all.length:all.filter(r=>r.scope===sc).length;

  /* TWO signals, never one number. `typed` is Claude Code's own counter and it
     counts ONE thing: the times you wrote /name. `in N/M sessions` is measured
     from the transcripts — how often something by that name actually ran. The
     gap between them is the whole point: caveman reads as "used twice, 56 days
     ago" and is in fact active in most of your sessions, because it arrives
     through a hook, and hooks touch no counter. */
  const usage=r=>{
    const typed=r.uses
      ?`<span class="tag ok" title="You typed /${esc(r.command)} ${r.uses} time(s). This counter only counts explicit invocation.">typed ${r.uses}×${r.last_used?' · '+esc(r.last_used):''}</span>`
      :'<span class="tag" title="Never invoked by name. A skill Claude loads on its own does not increment this.">never typed</span>';
    const live=r.sessions
      ?`<span class="tag ok" title="Measured from your transcripts${r.via?' — the '+esc(r.via)+' plugin\'s hook ran':''}">in ${r.sessions}/${r.of_sessions} sessions</span>`
      :'';
    return typed+live;
  };
  const warn=r=>(r.auto===false
      ?'<span class="tag warn" title="disable-model-invocation: true — Claude may never load this on its own; only you can, by typing it">manual only</span>':'')
    +(r.weak?'<span class="tag warn" title="Claude picks a skill by matching your task against its description. A missing or one-word description can only be found by name.">thin description</span>':'')
    +(r.shadowed?'<span class="tag warn" title="A personal skill of the same name wins — this one never runs">shadowed</span>':'');

  const act=r=>{
    const b=[];
    if(r.dir)b.push(`<button class="btn sm" onclick='skView(${hesc(r.dir)})'>view</button>`);
    if(r.scope==='personal'||r.scope==='project')
      b.push(`<button class="btn sm" onclick='post("/api/open-editor",{file:${hesc(r.dir+"\\\\SKILL.md")}})'>edit</button>`);
    if(r.scope!=='personal'&&r.dir)
      b.push(`<button class="btn sm" onclick='skSave(${hesc(r.dir)})' title="Copy into the personal scope of every account">→ personal</button>`);
    if(r.scope!=='project'&&r.dir&&path)
      b.push(`<button class="btn sm" onclick='skInstall(${hesc(r.dir)},"project")' title="Copy into ${esc(P.name)} only">→ project</button>`);
    if(r.scope==='personal'||r.scope==='project')
      b.push(`<button class="btn sm danger" onclick='skRemove(${hesc(r.dir)},${hesc(r.scope)})'>${ic('del')}</button>`);
    return b.join('');
  };
  const row=r=>`<div class="skrow" data-scope="${esc(r.scope)}"
      data-f="${esc(r.command+' '+r.name+' '+r.desc+' '+r.scope)}">
      <code class="skcmd">/${esc(r.command)}</code>
      <span class="tag">${esc(r.scope)}</span>${provTag('skill',r.name)}
      ${usage(r)}${warn(r)}
      <span class="sp"></span>${act(r)}
      <span class="skdesc">${esc(r.desc)||'<span style="color:var(--dim2)">No description — Claude can only reach this one if you type it.</span>'}</span></div>`;

  const chip=(id,label)=>`<span class="chip skchip${SKSCOPE===id?' on':''}"
      data-sc="${id}" onclick='skScope(${hesc(id)})'>${label} ${n(id)}</span>`;
  const acctChips=accts.length>1?`<div class="chips" style="margin:0 0 8px">
      ${accts.map(a=>`<span class="chip${(SKACCT||'')===(a.dir||'')?' on':''}"
        onclick='skAcct(${hesc(a.dir||'')})'>${esc(a.name)}</span>`).join('')}
    </div>`:'';

  if(!paint(nav,`
    <div class="card"><h3>${ic('ai')} Skills <span class="sp"></span>
      <span class="tag">${all.length} loadable</span></h3>
      <p class="secthint">A skill is a folder of instructions Claude loads <b>only when it needs it</b> — you type <code>/name</code>, or Claude reads the descriptions and picks one itself. That is why it costs almost nothing to have many of them, and why the description matters more than the body.</p>
      <p class="secthint">Everything Claude Code can load here, in the order it resolves them: <b>personal</b> (every project on an account) beats <b>project</b> (this one only), plugins are namespaced, and the built-in ones live inside Claude Code. The command is the folder name.</p>
      ${acctChips}
      <div class="chips" style="margin:0 0 8px">${chip('all','all')}${chip('personal','personal — every project')}${
        path?chip('project','project — '+esc(P.name)):''}${chip('plugin','from a plugin')}${chip('bundled','built into Claude Code')}</div>
      <div class="fld" style="margin:0"><input id="skQ" placeholder="Filter by name, description or scope…" spellcheck="false">
        <div style="color:var(--dim2);font-size:12px;margin-top:4px" id="skCount"></div></div></div>

    <div class="card">${all.length?all.map(row).join(''):'<div class="empty">No skills anywhere yet — add one below.</div>'}
      <p class="secthint" style="margin:10px 0 0">Two counters, and the gap between them is the point. <b>typed N×</b> is Claude Code's own tally of the times <i>you</i> wrote <code>/name</code>. <b>in N/M sessions</b> is measured from your transcripts — how often something by that name actually ran, including the times Claude loaded it on its own. A skill can read as "never typed" and still be live in most of your sessions.</p>
      <p class="secthint" style="margin:6px 0 0">The amber tags are the three ways a skill quietly never runs: <b>manual only</b> — it has opted out of Claude loading it, so only you can; <b>thin description</b> — too little for Claude to match a task against, so it is reachable by name alone; <b>shadowed</b> — a personal skill of the same name wins, and this copy is dead.</p>
      <p style="color:var(--dim2);font-size:12px;margin:6px 0 0">Personal: <code>${esc(d.personal_dir||'')}</code>${
        path?` · Project: <code>${esc(d.project_dir||'')}</code>`:''} · session counts from your last ${d.sessions_scanned||0} sessions across every account.</p></div>

    <div class="card"><h3>${ic('add')} Add a skill</h3>
      <p style="color:var(--dim);font-size:12.5px;margin:0 0 8px">Everything here lands in <b>personal — all ${accts.length||1} account${(accts.length||1)===1?'':'s'}</b> unless you pick the project.${path?'':' Open a project to install into one.'}</p>
      <div class="mrow" style="align-items:flex-end;flex-wrap:wrap">
        <div class="fld" style="flex:1;min-width:220px;margin:0"><label for="skTmpl">Starter shipped with claudectl</label>
          <select id="skTmpl">${(d.templates||[]).map(t=>
            `<option value="${esc(t.dir)}">${esc(t.command)} — ${esc((t.desc||'').slice(0,70))}</option>`).join('')}</select></div>
        <button class="btn sm" onclick="skTmplView()">view</button>
        <button class="btn pri sm" onclick="skTmplInstall('personal')">Install → personal</button>
        ${path?`<button class="btn sm" onclick="skTmplInstall('project')">→ ${esc(P.name)}</button>`:''}
      </div>
      <div class="mrow" style="margin-top:10px;flex-wrap:wrap">
        <button class="btn sm" onclick="skNew()">${ic('add')} Write one</button>
        <button class="btn sm" onclick="skAI()">${ic('ai')} Have Claude write one</button>
        <span class="sp"></span>
        <div class="fld" style="flex:1;min-width:260px;margin:0"><label for="skGitUrl">Clone a skill+agents bundle from GitHub — risk-scanned and shown to you first</label>
          <input id="skGitUrl" value="https://github.com/olsenbrands/fable-foreman"></div>
        <button class="btn sm" onclick="skGitInstall()">${ic('download')} Clone</button>
      </div></div>`))return;
  bindFilter('skQ','.skrow','skCount',
    el=>SKSCOPE==='all'||el.dataset.scope===SKSCOPE);
}
/* A chip is a filter, not a navigation: it re-runs the one filter pass and
   moves the highlight. Redrawing the page instead would re-fetch the whole
   inventory to hide four rows. */
function skScope(sc){
  SKSCOPE=sc;
  document.querySelectorAll('#content .skchip').forEach(c=>
    c.classList.toggle('on',c.dataset.sc===sc));
  const run=window['__f_skQ'];if(run)run();
}
function skTmplDir(){const s=$('#skTmpl');return s?s.value:'';}
function skTmplView(){const d=skTmplDir();if(d)skView(d);}
function skTmplInstall(scope){const d=skTmplDir();if(d)skInstall(d,scope);}
function skAcct(dir){SKACCT=dir;drawPage('skills');}
async function skSave(dir){
  const r=await post('/api/skills/library',{dir});
  toast(r.ok?`Personal — installed for ${(r.accounts||[]).length||1} account(s)`
            :'Failed: '+(r.error||''),r.ok?'ok':'err');
  drawPage('skills');
}
function skGitInstall(){
  const url=($('#skGitUrl').value||'').trim();
  if(!url){toast('Enter a git URL','err');return;}
  const scope=($('#skGitProj')&&$('#skGitProj').checked)?'project':'personal';
  const P=projCtx();
  inlineJob('#jban','skill_git_install',{path:P?P.path:'',url,scope,cfgdir:SKACCT||undefined},
    {label:'Installing skill',
     onDone:st=>toast((st.result&&st.result.message)||'Installed','ok'),
     redraw:()=>drawPage('skills')});
}
async function skView(dir){
  const d=await api('/api/skills/read?dir='+encodeURIComponent(dir));
  $('#dTitle').textContent=d.meta&&d.meta.name||'Skill';
  $('#dBody').innerHTML=`<div class="card"><div class="kv">
    <span class="k">Tools</span><span>${esc(d.meta['allowed-tools']||'(all)')}</span></div></div>
    <div class="msg"><div class="body">${esc(d.body)}</div></div>`;
  $('#drawer').classList.add('show');
}
async function skInstall(dir,scope){
  const P=projCtx();
  const r=await post('/api/skills/install',
    {dir,scope,path:P?P.path:'',cfgdir:SKACCT||undefined});
  toast(r.ok?(scope==='project'?'Installed into this project'
              :`Personal — installed for ${(r.accounts||[]).length||1} account(s)`)
            :'Install failed',r.ok?'ok':'err');
  drawPage('skills');
}
async function skRemove(dir,scope){
  const many=scope==='personal'&&(ST.accounts||[]).length>1;
  if(!await confirmBox('Delete this skill folder?',
    many?`It was installed for every account, so it is removed from all ${(ST.accounts||[]).length} of them.`
        :'Claude Code stops loading it immediately.'))return;
  const r=await post('/api/skills/remove',{dir,scope});
  toast(r.ok?(r.accounts&&r.accounts.length>1?`Deleted from ${r.accounts.length} accounts`:'Deleted')
            :'Failed',r.ok?'ok':'err');
  drawPage('skills');
}
/* Where a new skill lands is a CHOICE, not a side effect of whether a project
   happens to be open — that guess is what put skills where they only worked in
   one place. */
function skScopeField(){
  return projCtx()?{label:'Where',type:'select',
    options:[['personal','personal — every project'],
             ['project','only '+(projCtx()||{}).name]]}:null;
}
async function skNew(){
  const sc=skScopeField();
  const v=await ask('New skill',[{label:'Name (e.g. commit-message)'},
    {label:'Description — when should Claude use it?'},
    ...(sc?[sc]:[]),
    {label:'Instructions (markdown)',type:'textarea'}]);
  if(v===null||!v[0].trim())return;
  const scope=sc?v[2]:'personal';
  const P=projCtx();
  const r=await post('/api/skills/create',{name:v[0],description:v[1],
    body:sc?v[3]:v[2],scope,path:P?P.path:'',cfgdir:SKACCT||undefined});
  toast(r.ok?'Skill created':'Failed',r.ok?'ok':'err');drawPage('skills');
}
async function skAI(){
  const sc=skScopeField();
  const v=await ask('AI-generate skill',[{label:'Name'},
    ...(sc?[sc]:[]),
    {label:'Describe what the skill should do',type:'textarea'}]);
  if(v===null||!v[0].trim())return;
  const P=projCtx();
  inlineJob('#jban','skill_ai',{path:P?P.path:'',name:v[0],
      description:sc?v[2]:v[1],scope:sc?v[1]:'personal',cfgdir:SKACCT||undefined},
    {label:'Generating skill',redraw:()=>drawPage('skills')});
}

/* which account's hooks are on screen. '' = the active one. An install still
   fans out to EVERY account by default — the reader narrows, the writer does
   not, because what you provision is a property of you. */
let HKACCT='';
/* Plain English leads, the API name follows. `PreToolUse` is Claude Code's
   vocabulary, not the reader's; a list of nothing but those names can only be
   read by someone who has already memorised them. Served by /api/hooks from
   `hooks.EVENT_WHEN` so the phrasing has one home, and an event with no phrase
   degrades to its raw name rather than to a blank. */
let HKWHEN={};
function hkWhen(ev){return HKWHEN[ev]||ev||'anywhere';}
/* The trigger is stated ONCE per group instead of as a badge repeated on every
   row — the same move that turned the memory tab's 12-row wall into four
   groups. Order follows first appearance, so it is the payload's order and
   never a second list to keep in step. */
function hkGroup(items,render){
  const order=[],by={};
  items.forEach(it=>{const e=it.event||'';if(!by[e]){by[e]=[];order.push(e);}by[e].push(it);});
  return order.map(e=>`<div class="fgrp"><div class="hkwhen"><span>${esc(hkWhen(e))}</span>
      <code>${esc(e)}</code></div>${by[e].map(render).join('')}</div>`).join('');
}
async function pgHooks(nav){
  await loadProv();
  const d=await api('/api/hooks?'+qs(HKACCT?{cfgdir:HKACCT}:{}));
  HKWHEN=d.events||{};
  const accts=(d.accounts||[]);
  /* Only a real SKEW is worth a warning: a template missing from EVERY account
     is simply one you never installed, and tagging those turned fourteen clean
     rows into sixteen false alarms. */
  const skew=missing=>{
    const m=(missing||[]).length;
    return (m && m < accts.length)
      ? `<span class="tag warn" style="font-size:11px">missing on ${esc(missing.join(', '))}</span>` : '';
  };
  const hookRow=h=>`
    <div class="hrow${h.enabled?'':' off'}" style="display:flex;align-items:center;gap:10px;padding:4px 0${h.enabled?'':';opacity:.55'}">
      <input type="checkbox" ${h.enabled?'checked':''}
        title="${h.enabled?'Disable':'Enable'} this hook"
        onchange='hookToggle(${hesc(h.event)},${h.index},this.checked)'>
      <span style="flex:1">${esc(h.label)}${h.enabled?'':' <span style="color:var(--dim2);font-size:11px">(disabled)</span>'}</span>
      ${h.matcher?`<code style="color:var(--dim2);font-size:11px" title="Only runs for this: ${esc(h.matcher)}">${esc(h.matcher)}</code>`:''}
      <button class="btn sm danger" onclick='hookRm(${hesc(h.event)},${h.index},${h.enabled?'true':'false'})'>${ic('del')}</button></div>`;
  const active=hkGroup(d.hooks||[],hookRow);
  /* NOT `.hrow`: that class means "an installed hook", and the smoke tool
     counts one checkbox per `.hrow` to prove every hook can be turned off.
     A template is an offer, not a hook — it has nothing to toggle. */
  const tmplRow=t=>`
    <div class="trow" data-f="${esc(t.key+' '+t.desc+' '+hkWhen(t.event)+' '+t.event)}">
      <b style="min-width:170px">${esc(t.key)}</b>
      <span style="flex:1;color:var(--dim);font-size:12px">${esc(t.desc)}</span>
      ${skew(t.missing)}
      ${t.installed&&!(t.missing||[]).length?`<span class="tag ok">${ic('check')} installed</span>`
        :`<button class="btn sm" onclick='hookAdd(${hesc(t.key)})'>${ic('add')} Install</button>`}</div>`;
  const tmpl=hkGroup(d.templates||[],tmplRow);
  const picker=accts.length>1?`<div class="chips" style="margin-bottom:10px">
      ${accts.map(a=>`<span class="chip${(HKACCT||'')===(a.dir||'')?' on':''}"
        onclick='hookAcct(${hesc(a.dir||'')})'>${esc(a.name)} <b>${a.count}</b></span>`).join('')}
    </div>`:'';
  if(!paint(nav,`
    <div class="card"><h3>${ic('link')} Hooks <span class="sp"></span>
      <button class="btn sm" onclick="hookEditFile()">${ic('edit')} Edit settings.json</button>
      <button class="btn sm" onclick="hookPurge()">${ic('del')} Purge broken</button>
      <button class="btn sm" onclick="hookAI()">${ic('ai')} Have Claude write one</button></h3>
      <p class="secthint">A hook is a command <b>your machine</b> runs at a fixed moment — before a tool, after an edit, when a session starts. It is not a suggestion Claude may ignore: it always runs, and a hook that exits with an error can refuse the action outright. Each heading below says when its hooks fire.</p>
      ${picker}
      <p class="secthint">${HKACCT
        ?'Acting on this account only.'
        :'Turning one off or deleting it applies to <b>every</b> account, the same as installing. Pick an account above to act on that one alone.'}</p>
      ${active||'<div class="empty">No hooks installed. Pick one from the ready-made list below.</div>'}
      <div style="color:var(--dim2);font-size:12px;margin-top:8px">${esc(d.settings_path||'')}</div></div>
    <div class="card"><h3>${ic('add')} Ready-made hooks <span class="tag">${(d.templates||[]).length}</span></h3>
      <p class="secthint">Installing adds the hook to <b>every</b> account. A hook you disabled still counts as installed, so it is never silently re-added beside itself.</p>
      <div class="fld" style="margin:0 0 8px"><input id="hkQ" placeholder="Filter by name, what it does, or when it fires…" spellcheck="false">
        <div style="color:var(--dim2);font-size:12px;margin-top:4px" id="hkCount"></div></div>
      ${tmpl}</div>`))return;
  bindFilter('hkQ','.trow','hkCount');
}
function hookAcct(dir){HKACCT=dir;drawPage('hooks');}
async function hookEditFile(){
  const d=await api('/api/hooks?'+qs(HKACCT?{cfgdir:HKACCT}:{}));
  const r=await post('/api/open-editor',{file:d.settings_path});
  toast(r.ok?'Opened in your editor':'Could not open it','ok');}
async function hookAdd(key){const r=await post('/api/hooks/template',{key});
  toast(r.already?(r.message||'Already installed')
    :`Installed into ${(r.accounts||['this account']).join(', ')}`,'ok');drawPage('hooks');}
async function hookToggle(event,index,enabled){
  const r=await post('/api/hooks/toggle',{event,index,enabled,cfgdir:HKACCT||undefined});
  toast(r.ok?(enabled?'Enabled':'Disabled'):'Not found','ok');drawPage('hooks');}
async function hookRm(event,index,enabled){
  if(!await confirmBox('Remove this hook?'))return;
  await post('/api/hooks/remove',{event,index,enabled,cfgdir:HKACCT||undefined});
  toast('Removed','ok');drawPage('hooks');}
async function hookPurge(){const r=await post('/api/hooks/purge',{});
  toast(`Purged ${r.removed} broken hook(s)`,'ok');drawPage('hooks');}
async function hookAI(){
  const v=await ask('AI-generate hook',[{label:'What should the hook do?',type:'textarea'}]);
  if(v===null||!v[0].trim())return;
  /* HKACCT, like every other hook action on this page: without it an
     AI-generated hook always landed on the active account, whichever one the
     account chips above were showing. */
  inlineJob('#jban','hook_ai',{description:v[0],cfgdir:HKACCT||undefined},
    {label:'Generating hook',redraw:()=>drawPage('hooks')});
}

async function pgAccounts(nav){
  // /api/usage/plan is server-cached, so pairing it with the account list costs
  // nothing and lets every row carry its own quota ring instead of sending you
  // to the dashboard to find out which account is the one that's nearly full
  const [d,plan]=await Promise.all([api('/api/accounts'),
    api('/api/usage/plan').catch(()=>({}))]);
  const pw=(plan.accounts||[]);
  const quotaOf=name=>{
    const a=pw.find(x=>x.account===name);
    if(!a)return null;
    const wins=(a.windows||[]).map(w=>(w.pct||0)/100);
    return wins.length?Math.max(...wins):null;
  };
  if(!paint(nav,`<div class="card"><h3>Claude accounts <span class="sp"></span>
    <button class="btn sm" onclick="acctAdd()">${ic('add')} Add account</button></h3>
    ${(d.accounts||[]).map(a=>{
      const q=quotaOf(a.name);
      return `<div class="arow2 mo-in">
        <span class="dot${a.active?' pip':''}" style="background:${a.active?'var(--ok)':'var(--dim2)'};color:var(--ok)"></span>
        <b>${esc(a.name)}</b>
        ${a.active?'<span class="tag ok">active</span>':''}
        <span class="apath">${esc(a.resolved)}</span>
        ${q==null?'<span class="aq dim2">no usage data</span>'
          :INST.html('ring','acct:'+a.name,{fmt:'pct'})}
        <button class="btn sm" onclick='acctAct("switch",${hesc(a.name)})'>Switch</button>
        <button class="btn sm" onclick='acctTerm(${hesc(a.name)},${hesc(a.dir)})'>Open terminal</button>
        ${a.name!=='default'?`
          <button class="btn sm" onclick='acctRename(${hesc(a.name)})'>Rename</button>
          <button class="btn sm danger" onclick='acctAct("remove",${hesc(a.name)})'>${ic('del')}</button>`:''}
      </div>`;}).join('')}</div>
    <div class="card"><h3>${ic('refresh')} Sync accounts</h3>
      <p style="color:var(--dim);font-size:13px;margin:0 0 8px">What you provision — hooks, plugins, marketplaces, user agents, the global CLAUDE.md — is a property of <b>you</b>, not of whichever account happened to be active. This levels every account up to the union of them all. It only ever <b>adds</b>: an account keeps anything the others do not have, because there is no way to tell a deliberate choice from a gap.</p>
      <div id="syncOut"><span class="spin"></span></div></div>`))return;
  drawSync();
  for(const a of (d.accounts||[])){
    const q=quotaOf(a.name);
    if(q==null)continue;
    INST.set('acct:'+a.name,{v:q,tone:q>=.8?'err':q>=.6?'warn':null});
  }
  mounted();
  for(const a of (d.accounts||[])){
    const q=quotaOf(a.name);
    if(q!=null)setRead('acct:'+a.name,q*100);
  }
}
/* The diff is shown BEFORE anything is written: what reaches four more accounts
   here is hooks and plugins, which run code on every turn. Every plugin install
   still goes through the same review gate the single-account path uses. */
async function drawSync(){
  const el=$('#syncOut');if(!el)return;
  const d=await api('/api/accounts/sync');
  if(!$('#syncOut'))return;
  const kinds=[['plugins','plugins'],['marketplaces','mkts'],['hooks','hooks'],
               ['agents','agents']];
  const rows=(d.accounts||[]).map(r=>`<tr>
      <td><b>${esc(r.name)}</b></td>
      ${kinds.map(([k])=>`<td class="num">${r.have[k]}</td>`).join('')}
      <td class="num">${r.have.statusline?'yes':'—'}</td>
      <td class="num">${r.have.claude_md?'yes':'—'}</td>
      <td>${r.todo?`<span class="tag warn">${r.todo} missing</span>`:'<span class="tag ok">complete</span>'}</td>
    </tr>`).join('');
  const detail=(d.accounts||[]).filter(r=>r.todo).map(r=>{
    const m=r.missing,bits=[];
    for(const k of ['marketplaces','plugins','agents','hooks'])
      if((m[k]||[]).length)bits.push(`<b>${k}</b> ${esc(m[k].join(', '))}`);
    if(m.statusline)bits.push('<b>statusline</b> install');
    if(m.claude_md)bits.push('<b>CLAUDE.md</b> copy global instructions');
    return `<div class="lrow"><span class="tag warn">${esc(r.name)}</span>
      <div style="font-size:12.5px;color:var(--dim)">${bits.join(' · ')}</div></div>`;}).join('');
  $('#syncOut').innerHTML=`<table class="tbl"><tr><th>account</th>
      ${kinds.map(([,l])=>`<th class="num">${l}</th>`).join('')}
      <th class="num">stline</th><th class="num">CLAUDE.md</th><th></th></tr>${rows}</table>
    ${d.clean?'<div class="empty" style="color:var(--ok)">✓ Every account already has everything.</div>'
      :`<div style="margin-top:10px">${detail}</div>
        <div class="mrow"><button class="btn pri" onclick="syncApply()">Copy the missing items into every account</button></div>`}`;
}
async function syncApply(){
  if(!await confirmBox('Copy the missing items into every account?',
    'Hooks and plugins run code on every turn. Each plugin install is reviewed first.'))return;
  inlineJob('#jban','sync_accounts',{},{label:'Syncing accounts',
    onDone:st=>{const r=(st&&st.result)||{};
      toast(r.clean?'Nothing to do':`Synced ${(r.done||[]).length} item(s)`,'ok');},
    redraw:()=>drawPage('accounts')});
}
async function acctAdd(){
  const v=await ask('Add account',[{label:'Name (e.g. work, personal)'},
    {label:'Config dir (blank = ~/.claude-<name>)'}]);
  if(v===null||!v[0].trim())return;
  const r=await post('/api/accounts/action',{action:'add',name:v[0].trim(),dir:v[1].trim()});
  toast(r.ok?'Account added — open a terminal on it to /login':'Failed','ok');
  ST=await api('/api/state');drawPage('accounts');
}
async function acctAct(action,name){
  if(action==='remove'&&!await confirmBox(`Remove account '${name}' from the list?`,
    'Its config dir on disk is untouched.'))return;
  const r=await post('/api/accounts/action',{action,name});
  toast(r.ok?(action==='switch'?'Active account switched (restart to fully apply)':'Done')
    :(r.error||'Failed'),r.ok?'ok':'err');
  ST=await api('/api/state');drawPage('accounts');
}
async function acctRename(name){
  const v=await ask('Rename account',[{label:'New name',value:name}]);
  if(v===null||!v[0].trim()||v[0]===name)return;
  const r=await post('/api/accounts/action',{action:'rename',name,new:v[0].trim()});
  toast(r.ok?'Renamed':(r.error||'Failed'),r.ok?'ok':'err');
  ST=await api('/api/state');drawPage('accounts');
}
async function acctTerm(name,dir){
  const r=await post('/api/accounts/terminal',{name,dir});
  toast(r.ok?`Terminal opened as '${name}' — use /login if needed`:'Failed','ok');
}

/* GENERATED from NAV_GROUPS, TABS and the TUI's own ACTIONS table — never
   typed. The hand-written version listed "Project → Graph" (an endpoint that had
   no consumer) and omitted Plugins, Output styles, Claude Code, Review and
   Repos. docs/api.md needs a generator because Markdown cannot compute; this is
   JavaScript in the same scope as the arrays, so there is no staleness window
   at all. Every row carries data-help-row so the smoke test can count them
   against NAV.length + TABS.length. */
async function pgHelp(nav){
  const row=(where,what)=>`<tr data-help-row><td style="white-space:nowrap;color:var(--cyan)">${esc(where)}</td><td>${esc(what)}</td></tr>`;
  const tbl=(head,rows)=>`<table class="tbl"><tr><th>${head}</th><th>what it is for</th></tr>${rows}</table>`;
  const groups=NAV_GROUPS.map(([grp,items])=>
    `<div class="card"><h3>${ic(items[0][1])} ${esc(grp)}</h3>
     ${tbl('page',items.map(([,,l,blurb])=>row(l,blurb)).join(''))}</div>`).join('');
  const keys=(ST.tui_keys||[]);
  paint(nav,`
    <div class="card"><h3>${ic('help')} Projects</h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:10px">Pick a project in the sidebar; these are its tabs.</p>
    ${tbl('tab',TABS.map(([,l,blurb])=>row(l,blurb)).join(''))}</div>
    ${groups}
    <div class="card"><h3>${ic('ai')} Terminal UI keys</h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:10px">The sessions screen in <code>claudectl</code>'s terminal interface. Rendered from the same table the terminal itself reads, so the two cannot disagree — press <code>?</code> there for this list, <code>/</code> for a searchable palette.</p>
    ${keys.length?`<table class="tbl"><tr><th>key</th><th>action</th></tr>
      ${keys.map(([k,blurb])=>`<tr><td style="white-space:nowrap"><code>${esc(k)}</code></td><td>${esc(blurb)}</td></tr>`).join('')}</table>`
      :`<div class="empty">No key table in this build.</div>`}</div>
    <div class="card"><h3>${ic('palette')} Instruments &amp; motion</h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:10px">Gauges sit next to the numbers they describe and are fed from the fetches the page already made — they never poll on their own. Each one tweens to a new value and then <b>stops</b>: on a settled page nothing is animating, so anything you see moving is something actually happening.</p>
    <table class="tbl"><tr><th>instrument</th><th>what it reads</th></tr>
    <tr><td style="white-space:nowrap;color:var(--cyan)">Ring</td><td>A share of a whole — plan quota (highest window across your accounts), MCP servers up. Ticks light as the value passes them.</td></tr>
    <tr><td style="white-space:nowrap;color:var(--cyan)">Dial</td><td>A rate against your own history — tokens per hour now, scaled to your heaviest day. Zones mark where it starts costing.</td></tr>
    <tr><td style="white-space:nowrap;color:var(--cyan)">Sparkline</td><td>A trend — daily tokens, with the most recent point lit so you can tell which end is now.</td></tr>
    <tr><td style="white-space:nowrap;color:var(--cyan)">Equalizer</td><td>Live activity. The only gauge that keeps moving with a steady input, because "work is happening" is itself continuous. Flat means idle.</td></tr>
    <tr><td style="white-space:nowrap;color:var(--cyan)">Flow map</td><td>The workspace: one node per project, size by tokens, colour by account, dashed links where two projects share an account.</td></tr>
    </table>
    <p style="color:var(--dim2);font-size:12px;margin-top:10px">Motion elsewhere is transitional: numbers count to their new value, rows slide when they reorder, and a travelling border marks a job that is still running. <b>Settings → Appearance → Motion</b> sets how much of that you get; your OS "reduce motion" preference always wins.</p></div>`);
}

async function pgSettings(nav){
  const o=ST.options;
  if(!paint(nav,`<div class="card"><h3>Defaults</h3>
    ${fld('sEff','Effort')}${fld('sMod','Model')}${fld('sPerm','Permission mode')}
    ${fld('sThink','Thinking cap')}${fld('sSub','Subagent model')}
    ${fld('sShell','GUI window')}
    <div class="chips" id="sTheme" style="display:none"></div>
    <div class="mrow"><button class="btn pri" onclick="setSave()">Save</button></div></div>
  <div class="card"><h3>${ic('palette')} Appearance</h3>
    <div class="fld"><label>World — a complete look, locked</label>
      <p style="color:var(--dim);font-size:13px;margin:0 0 8px">A world owns everything at once: its own palette, shape, icons, background, overlay and hover behaviour. Nothing in it can be mixed with anything else — that is the point, and it is why these can go much further than a skin that has to survive 32 different palettes.</p>
      <div class="wgal" id="thWorld"></div>
      <div class="monote" id="wNote"></div></div>
    <div id="classicBlock">
      <p style="color:var(--dim);font-size:13px;margin-bottom:10px">Or build your own: a palette for the colours, a skin for the shape. Click a card to apply and save it. Each palette also carries a motion <i>personality</i> (how far things travel, how much they glow), so Mono stays terse and Dracula does not.</p>
      <div class="thgal" id="thGallery"></div>
      <div class="fld" style="margin-top:16px"><label>Skin — the shape of the app, independent of its colours</label>
        <div class="skgal" id="thSkin"></div>
        <div class="monote" id="skNote"></div></div>
    </div>
    <div class="fld" style="margin-top:14px"><label>Motion</label>
      <div class="chips" id="sMotion"></div>
      <div style="color:var(--dim2);font-size:12px;margin-top:6px">
        <b>full</b>: everything — gauges tween, rows slide when they reorder, running jobs get a travelling border, cards catch the pointer ·
        <b>subtle</b>: only the motion that carries information — value tweens, meter fills, page changes ·
        <b>off</b>: nothing moves.<br>
        Gauges stop once they reach their value either way, so an idle page renders no frames at all. Your OS <i>reduce motion</i> setting overrides this.</div></div>
    <div class="fld" style="margin-top:14px"><label>Surface transparency — how much of the background shows through</label>
      <input type="range" id="sSurf" min="40" max="100" step="2">
      <div class="frontends"><span>See-through</span><span>Solid</span></div>
      <div class="frontread" id="surfRead"></div>
      <div style="color:var(--dim2);font-size:12px;margin-top:4px">Applies to every theme. Each look proposes its own value — drag to override it everywhere, or
        <a href="#" id="surfReset" style="color:var(--cyan)">use each theme's default</a>.</div></div>
    <div class="fld" style="margin-top:14px"><label>Background</label>
      <div class="chips" id="sStage"></div>
      <div class="monote" id="stgNote"></div>
      <div style="color:var(--dim2);font-size:12px;margin-top:6px">
        One animated scene behind the whole app, chosen by the skin and driven by what the workspace is actually doing — idle crawls, a running job speeds it up, launching a session sends a shockwave through it.
        It stops entirely when the window is hidden, minimised or blurred, and <b>motion: off<\b> turns it off with everything else.</div></div></div>
  <div class="card"><h3>${ic('folder')} Paths &amp; limits</h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">Blank means auto-detect. A path that does not exist is <b>refused</b> rather than saved — pinning a broken one makes every launch fail with no clue why. Its own Save: the Defaults button above must not half-cover another card.</p>
    <div class="grid2">
      <div class="fld"><label>Editor <span style="color:var(--dim2)">— what "open in editor" runs</span></label>
        <input id="sEditor" placeholder="auto-detect (Notepad++, VS Code, notepad)"></div>
      <div class="fld"><label>claude.exe <span style="color:var(--dim2)">— the Claude Code binary</span></label>
        <input id="sClaudeExe" placeholder="auto-detect (~/.local/bin, then PATH)"></div>
    </div>
    <div class="grid2">
      <div class="fld"><label>CLAUDE_CONFIG_DIR <span style="color:var(--dim2)">— which account is active</span></label>
        <input id="sCfgDir" placeholder="default: ~/.claude"></div>
      <div class="fld"><label>Budget cap <span style="color:var(--dim2)">— $ per headless call, 0 = no cap</span></label>
        <input id="sBudget" type="number" min="0" max="1000" step="0.05"></div>
    </div>
    <div style="color:var(--dim2);font-size:12px">The cap is <code>--max-budget-usd</code> on claudectl's <b>own</b> Claude calls (memory, lessons, plans, reviews) — your interactive sessions are unaffected. Changing the config dir takes effect on restart.</div>
    <div class="mrow"><button class="btn pri" onclick="setPathsSave()">Save</button></div></div>
  <div class="card"><h3>${ic('doc')} Statusline <span class="sp"></span>
      <span class="tag" id="slDot">checking…</span></h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">Claude Code renders two rows at the bottom of every session — identity and git on the first, pressure on the second. claudectl can be those rows, and it puts things there nobody else can compute: how stale this project's memory is, how many sessions are still unmined for lessons, which account you are on, and the branch, uncommitted count and sub-repo roll-up for wherever you are. Plan limits come free from the session payload, so it never polls the API. It refuses to replace a statusline you wrote yourself.</p>
    <div class="kv"><span>preview</span><code id="slPrev" style="color:var(--dim);white-space:pre">—</code></div>
    <div id="slAccts"></div>
    <div class="mrow"><button class="btn" id="slBtn" onclick="slToggle()">…</button></div></div>
  <div class="card"><h3>${ic('chart')} OpenTelemetry export</h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">Claude Code exports metrics and events over OTLP. claudectl already owns the launch environment, so it can switch this on per account without you exporting variables by hand. <b>Prompt text is never collected</b> unless you also set <code>OTEL_LOG_USER_PROMPTS=1</code> — this toggle does not.</p>
    <div class="fld"><label>Enabled</label><div class="chips" id="sOtel"></div></div>
    <div class="grid2">
      <div class="fld"><label>Endpoint</label><input id="sOtelUrl" placeholder="http://localhost:4318"></div>
      <div class="fld"><label>Protocol</label><div class="chips" id="sOtelProto"></div></div>
    </div>
    <div class="fld"><label>Headers <span style="color:var(--dim2)">— comma-separated, e.g. Authorization=Bearer xyz</span></label>
      <input id="sOtelHdr" placeholder="leave blank for none"></div>
    <div class="mrow"><button class="btn pri" onclick="setOtelSave()">Save</button></div></div>
  <div class="card"><h3>Economy model</h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">Model used for claudectl's <b>own</b> internal Claude calls — memory extraction, lessons, CLAUDE.md / agent / hook / skill generation. Defaults to Haiku to cut cost. Your actual coding sessions are unaffected. <i>default</i> = your account's model.</p>
    ${fld('sExtract','Economy model')}
    <div class="mrow"><button class="btn pri" onclick="setExtractSave()">Save</button></div></div>
  <div class="card"><h3>${ic('bolt')} Memory limits</h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">What one memory cycle may spend, and how much the graph may hold. These lived only in <code>claudectl.json</code> — while the memory hub and the health check both told you to raise <code>memory_max_calls</code>, a setting with no control on either surface.</p>
    <div class="grid2">
      <div class="fld"><label>Max Claude calls per cycle <span style="color:var(--dim2)">— 0 = unlimited</span></label>
        <input id="sMemCalls" type="number" min="0" max="500" step="1"></div>
      <div class="fld"><label>Max entities kept <span style="color:var(--dim2)">— least-connected are evicted first</span></label>
        <input id="sMemEnts" type="number" min="50" max="20000" step="50"></div>
    </div>
    <div class="grid2">
      <div class="fld"><label>Refresh when a project is opened <span style="color:var(--dim2)">— for projects <i>not</i> on the auto-memory schedule; those refresh on their own interval and never on open</span></label><div class="chips" id="sMemOpen"></div></div>
      <div class="fld"><label>Learn lessons from sessions</label><div class="chips" id="sMemLessons"></div></div>
    </div>
    <div class="mrow"><button class="btn pri" onclick="setMemLimitsSave()">Save</button></div></div>
  <div class="card"><h3>${ic('map')} Plan → Execute</h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">Model that writes the plan (runs once, headless) vs the model that executes it interactively. Keep the plan model accurate — the expensive reasoning happens once.</p>
    ${fld('sPlanMod','Plan model')}${fld('sExecMod','Execute model')}
    <div class="mrow"><button class="btn pri" onclick="setPlanExecSave()">Save</button></div></div>
  <div class="card"><h3>${ic('plug')} Free execution — OmniRoute <span class="sp"></span>
      <span id="orDot" class="tag">checking…</span></h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">Route the <b>execute</b> half of Plan → Execute through a local
      <a href="https://github.com/diegosouzapw/OmniRoute" target="_blank" rel="noopener"
         style="color:var(--cyan);text-decoration:none">OmniRoute</a> proxy — auto-starts in the background the
      moment a Plan → Execute task runs, no terminal to babysit. Planning always stays on your real Anthropic
      account; only execution runs through it, and it's still a real, full <code>claude</code> session with this
      project's usual agents/skills/system-prompt.</p>
    <p id="orNeedsProvider" style="color:var(--warn);font-size:12px;margin-bottom:8px;display:none">
      OmniRoute's own per-connection status shows nothing passing below — but that check can be stale/wrong
      (confirmed: it reported a genuinely working no-auth connection as broken). Use <b>Send a live test</b> to know
      for real. If that also fails: adding a provider is dashboard-only right now (OmniRoute's CLI add commands
      crash on this platform — confirmed upstream bug). First run <code>omniroute setup --password &lt;yours&gt;</code>
      once if you haven't set a dashboard password, then open the dashboard below → Providers → Add Provider → try
      a free one (Pollinations, Puter, DuckDuckGo AI Chat…).</p>
    <div id="orConns" style="margin-bottom:8px"></div>
    <div class="grid2">
      <div class="fld"><label>Base URL</label><input id="orUrl" placeholder="http://localhost:20128"></div>
      <div class="fld"><label>API key</label><input id="orKey" type="password"
        placeholder="${ST.omniroute_has_key?'set — leave blank to keep':'leave blank if none needed'}"></div>
    </div>
    <div id="orModWrap"></div>
    <div id="orLiveResult" style="color:var(--dim);font-size:12.5px;margin:4px 0"></div>
    <div class="mrow">
      <button class="btn sm" onclick="orRefresh()">${ic('refresh')} Refresh</button>
      <button class="btn sm" onclick="orLiveTest()">${ic('bolt')} Send a live test</button>
      <button class="btn sm" onclick="orProbe()" title="Sends a few real requests to find working models. Each one is a billed request — on free tiers repeated runs will exhaust the key, so this stops as soon as it finds enough. The proxy then refines the list from real sessions at no cost.">${ic('check')} Find working models</button>
      <button class="btn sm" onclick="orDashboard()">${ic('ext')} Open OmniRoute dashboard</button>
      <span class="sp"></span>
      <button class="btn pri" onclick="orSave()">Save</button></div>
    <div id="orProbeOut" style="font-size:12.5px;margin-top:6px"></div>

    <div style="border-top:1px solid var(--line);margin:14px 0 10px"></div>
    <h3 style="font-size:14px;margin:0 0 6px">${ic('refresh')} Model failover
      <span class="sp"></span><span id="foDot" class="tag">off</span></h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">Claude Code retries a failed turn against the
      <b>same</b> model ~10× with backoff, so a model that has been dropped upstream (<code>401 not supported</code>)
      or whose provider rejects a tool schema (<code>400</code>) makes a session look frozen. List fallback models
      below and claudectl runs its own local proxy that retries the <b>next</b> one whenever a turn fails before any
      output reaches the session. Leave the list empty to disable.</p>
    <div class="fld"><label>Fallback models — one per line, tried in order after the selected model</label>
      <textarea id="foModels" rows="4" spellcheck="false"
        placeholder="auto/coding:free&#10;auto/best-coding&#10;auto/fast"></textarea></div>
    <div class="grid2">
      <div class="fld"><label>Proxy port</label><input id="foPort" placeholder="20129"></div>
      <div class="fld"><label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="checkbox" id="foQuiet"> Hide the proxy console window</label>
        <div style="color:var(--dim);font-size:12px;margin-top:4px">The window logs every turn — which model was
          tried, what failed, what served it. It doubles as live plan-execution progress. Hiding it keeps the log
          at <code>~/.claude/failover.log</code>.</div></div>
    </div>
    <div id="foResult" style="color:var(--dim);font-size:12.5px;margin:4px 0"></div>
    <div class="mrow">
      <button class="btn sm" onclick="foStop()">${ic('close')} Stop proxy</button>
      <span class="sp"></span>
      <button class="btn pri" onclick="foSave()">Save failover</button></div></div>
  <div class="card"><h3>Interface</h3>
    <p style="color:var(--dim);font-size:13px">Default interface on startup — the toggle in the bottom-left does the same. <code>--tui</code>/<code>--gui</code> flags always override.</p>
    ${fld('sUpd','Updates')}
    <p style="color:var(--dim);font-size:13px;margin:0 0 10px">One switch for everything claudectl fetches on your behalf: whether a newer release exists, and the current Claude model list. <b>Install on quit</b> runs the upgrade in its own window after claudectl closes — pip cannot rewrite the script it is running from. <b>Off</b> stops both checks.</p>
    ${fld('sNotif','Notifications')}
    <p style="color:var(--dim);font-size:13px;margin:0">A desktop notification when a background job that ran longer than ${20}s finishes — memory builds, plans, reviews — and when the detached memory worker is done, which has no window of its own at all. Quick jobs never notify.</p></div>
  <div class="card"><h3>${ic('refresh')} Auto-memory <span class="sp"></span>
    <span class="fld" style="margin:0"><select id="amInt" onchange="amSaveInterval(this.value)" style="width:auto"></select></span></h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">Projects checked below have their memory refreshed in the background — one pass when claudectl starts, then one every interval, whenever their files change. Only changed projects use Claude; nothing runs while unchanged.</p>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">A pass re-reads a few changed modules at most, and whatever is left waits for the <b>next</b> interval — so a big backlog is spread out rather than spent at once. Opening a checked project does not add a pass; this schedule is the only thing that spends on it.</p>
    <div id="amList"><span class="spin"></span></div></div>`))return;
  chipsFill($('#sEff'),o.efforts,null,ST.defaults.effort);
  chipsFill($('#sMod'),o.models,o.model_labels,ST.defaults.model);
  chipsFill($('#sPerm'),o.perms,o.perm_labels,ST.defaults.perm);
  chipsFill($('#sThink'),o.thinking,o.thinking_labels,ST.defaults.max_thinking);
  chipsFill($('#sSub'),o.models,o.model_labels,ST.defaults.subagent_model);
  /* /api/state used to nest these under `otel`, so every read here was
     undefined: the panel showed its defaults however OTEL was configured, and
     Save then wrote those defaults back over the real values. */
  chipsFill($('#sOtel'),['off','on'],['off','on'],ST.otel_enabled?'on':'off');
  chipsFill($('#sOtelProto'),['http/protobuf','grpc'],['http/protobuf','grpc'],
    ST.otel_protocol||'http/protobuf');
  if($('#sOtelUrl'))$('#sOtelUrl').value=ST.otel_endpoint||'';
  /* write-only: the value is a bearer token and never comes back down, so the
     field reports whether one is set and blank means "keep it". */
  if($('#sOtelHdr')){$('#sOtelHdr').value='';
    $('#sOtelHdr').placeholder=ST.otel_has_headers
      ?'set — leave blank to keep, or type to replace':'leave blank for none';}
  if($('#sEditor'))$('#sEditor').value=ST.editor||'';
  if($('#sClaudeExe'))$('#sClaudeExe').value=ST.claude_exe||'';
  if($('#sCfgDir'))$('#sCfgDir').value=ST.claude_config_dir||'';
  if($('#sBudget'))$('#sBudget').value=ST.headless_budget_usd||0;
  if($('#sMemCalls'))$('#sMemCalls').value=ST.memory_max_calls||0;
  if($('#sMemEnts'))$('#sMemEnts').value=ST.memory_max_entities||500;
  chipsFill($('#sMemOpen'),['open','off'],['yes, on open','no'],
    ST.memory_auto_refresh||'open');
  chipsFill($('#sMemLessons'),['auto','prompt','off'],
    ['learn automatically','learn, but let me review','off'],
    ST.memory_lessons||'prompt');
  slRefresh();
  chipsFill($('#sExtract'),o.models,o.model_labels,ST.extract_model||'');
  chipsFill($('#sShell'),['auto','qt','edge','browser'],
    ['auto (Qt → Edge → browser)','Qt native window','Edge app window','browser tab'],
    ST.gui_shell||'auto');
  // saves on pick rather than waiting for the Defaults card's Save button —
  // it is in a different card, and a Save that only covers some of the page is
  // worse than no Save at all
  chipsFill($('#sUpd'),['notify','auto','off'],
    ['tell me','install on quit','off'],ST.auto_update||'notify',
    async v=>{await post('/api/settings',{auto_update:v});ST.auto_update=v;
              toast('Updates: '+v,'ok');});
  chipsFill($('#sNotif'),['on','off'],['on','off'],
    ST.notifications===false?'off':'on',
    async v=>{const on=v==='on';await post('/api/settings',{notifications:on});
              ST.notifications=on;toast('Notifications: '+v,'ok');});
  drawThemeGallery();
  chipsFill($('#sPlanMod'),o.models,o.model_labels,ST.plan_model||'');
  chipsFill($('#sExecMod'),o.models,o.model_labels,ST.exec_model||'');
  $('#orUrl').value=ST.omniroute_base_url||'';
  $('#foModels').value=(ST.failover_models||[]).join('\n');
  $('#foPort').value=ST.failover_port||20129;
  $('#foQuiet').checked=!!ST.failover_quiet;
  foDot();
  drawAutoMemList();
  orRefresh();
}
/* ── theme gallery ──
   A dot swatch can't distinguish 26 palettes, so every card paints a real mock
   of the app in that palette's own colours — surface, panel, gradient button,
   body/secondary text, and the three state tints. Hover previews live, leaving
   reverts to the saved theme, clicking saves immediately (a theme is a setting
   you judge by looking at it, not one you stage behind a Save button). */
// 'oled' leads: a true-black neutral with one accent is what both users landed
// on unprompted, so it should be the first thing the gallery offers.
const FAM_ORDER=['oled','neutral','cyan','blue','azure','teal','green','sage',
  'amber','yellow','orange','red','magenta','purple','violet','rose','light'];
function themeCardHtml(n,t,cur){
  const sel=n===cur;
  const grad=`linear-gradient(135deg,${t.accent},${t.accent2})`;
  // the mock wears the skin that is actually selected, so the card shows the
  // combination you would get — a palette preview in the wrong shape is a lie
  const sk=(ST.skins||{})[skinFor(n)]||{};
  const mockSt=`background:${t.bg};border-color:${t.line};`
    +`border-radius:${Math.min(sk.radius||6,12)}px;border-width:${Math.min(sk.border||1,3)}px`;
  return `<div class="thcard${sel?' on':''}" data-v="${esc(n)}">
    <div class="thmock" style="${mockSt}">
      <div class="thbar" style="background:${t.panel};border-bottom-color:${t.line}">
        <i style="background:${grad}"></i><u style="background:${t.dim2}"></u></div>
      <div class="thbody">
        <div class="thl" style="background:${t.txt};width:70%"></div>
        <div class="thl" style="background:${t.dim};width:52%"></div>
        <div class="thl" style="background:${t.dim2};width:38%"></div>
        <div class="thfoot">
          <s style="background:${t.ok}"></s><s style="background:${t.warn}"></s>
          <s style="background:${t.err||t.accent2}"></s>
          <b style="background:${grad}"></b></div>
      </div></div>
    <div class="thname">${esc(t.label||n)}</div>
    <div class="thopts"><span class="thopt on">${esc((sk.label||'').toLowerCase()||t.motion)}</span></div></div>`;
}
/* ── skin picker ──
   A skin is a bigger change than a palette, so each tile previews the actual
   geometry: corner treatment, border weight, radius and heading type, drawn in
   the currently-selected palette. Hover previews live and leaving reverts, the
   same contract the palette gallery already uses. */
function drawSkinPicker(){
  const box=$('#thSkin');if(!box)return;
  const skins=ST.skins||{};
  const cur=skinFor(ST.theme);
  const t=(ST.themes||{})[themeFor()]||{};
  // only the CLASSIC skins are offered: a world skin is one part of a bundle
  // and is meaningless bolted onto an arbitrary palette
  const names=(ST.classic_skins||Object.keys(skins)).filter(n=>skins[n]);
  box.innerHTML=names.map(n=>{
    const sk=skins[n];
    const on=n===cur;
    const st=`border-radius:${Math.min(sk.radius,14)}px;border-width:${Math.min(sk.border,3)}px;`
      +`background:${t.panel||'var(--panel)'};border-color:${t.line||'var(--line)'}`;
    return `<div class="skcard${on?' on':''}" data-v="${esc(n)}" title="${esc(sk.blurb)}">
      <div class="skmock skm-${esc(n)}" style="${st}">
        <u style="background:${t.accent}"></u>
        <s style="background:${t.dim2}"></s><s style="background:${t.dim2};width:52%"></s>
      </div>
      <div class="skname">${esc(sk.label)}</div></div>`;}).join('')
    // "auto" hands the choice back to the palette, which is the default state
    +`<div class="skcard${ST.skin?'':' on'}" data-v="" title="Wear whatever skin the selected palette names as its default">
        <div class="skmock skm-auto"><u></u><s></s><s style="width:52%"></s></div>
        <div class="skname">auto</div></div>`;
  const note=$('#skNote');
  if(note)note.textContent=(skins[cur]||{}).blurb||'';
  box.querySelectorAll('.skcard').forEach(card=>{
    const n=card.dataset.v;
    // Blurb on hover, but NOTHING is applied until you click. Hover-to-preview
    // was both unasked-for and expensive: applySkin/applyTheme reach STAGE,
    // whose setTheme disposes and rebuilds the entire three.js scene, so
    // sweeping the pointer across the gallery rebuilt one scene per card.
    card.addEventListener('mouseenter',()=>{
      if(note)note.textContent=(skins[n||skinFor(ST.theme)]||{}).blurb||'';});
    card.addEventListener('mouseleave',()=>{
      if(note)note.textContent=(skins[skinFor(ST.theme)]||{}).blurb||'';});
    card.addEventListener('click',async()=>{
      ST.skin=n;
      localStorage.setItem('ctl_skin',n);
      applyTheme(ST.theme);
      drawThemeGallery();
      MO.burst($('#content'));      // show the new skin's signature immediately
      await post('/api/settings',{skin:n});
    });
  });
}
/* ── world picker ──
   Above the palette gallery, because it is the bigger decision: picking a world
   turns the other two pickers off. Click to apply — no hover preview anywhere
   in Appearance any more. */
function drawWorldPicker(){
  const box=$('#thWorld');if(!box)return;
  const worlds=ST.worlds||{};
  const note=$('#wNote');
  const card=(n,w)=>{
    const p=(ST.themes||{})[w?w.palette:'']||{};
    const on=ST.world===n;
    const grad=`linear-gradient(135deg,${p.accent||'var(--dim2)'},${p.accent2||'var(--dim)'})`;
    return `<div class="wcard${on?' on':''}" data-v="${esc(n)}" title="${esc(w?w.blurb:'')}">
      <div class="wmock wm-${esc(n)}" style="background:${p.bg||'var(--panel)'};
        border-color:${p.line||'var(--line)'}">
        <i style="background:${grad}"></i>
        <s style="background:${p.txt||'var(--txt)'}"></s>
        <s style="background:${p.dim||'var(--dim)'};width:54%"></s></div>
      <div class="wname">${esc(w?w.label:'Classic')}</div></div>`;
  };
  box.innerHTML=Object.keys(worlds).map(n=>card(n,worlds[n])).join('')
    +`<div class="wcard${ST.world?'':' on'}" data-v="" title="Build your own from a palette and a skin">
        <div class="wmock wm-none"><i></i><s></s><s style="width:54%"></s></div>
        <div class="wname">Classic</div></div>`;
  if(note)note.textContent=(worlds[ST.world]||{}).blurb
    ||'Classic: pick a palette and a skin yourself.';
  box.querySelectorAll('.wcard').forEach(c=>{
    const n=c.dataset.v;
    c.addEventListener('mouseenter',()=>{if(note)note.textContent=
      (worlds[n]||{}).blurb||'Classic: pick a palette and a skin yourself.';});
    c.addEventListener('click',async()=>{
      ST.world=n;
      localStorage.setItem('ctl_world',n);
      applyTheme(ST.theme);
      drawWorldPicker();drawThemeGallery();
      MO.burst($('#content'));
      await post('/api/settings',{world:n});
    });
  });
  // a world locks the classic pickers: it is all or nothing
  const cb=$('#classicBlock');
  if(cb){cb.classList.toggle('locked',!!ST.world);
    cb.setAttribute('aria-disabled',ST.world?'true':'false');}
}
function drawThemeGallery(){
  const cur=themeFor();
  // hidden chips keep chipVal($('#sTheme')) working for setSave(). Filled
  // FIRST: an empty #sTheme would make Save post theme:'' and silently reset
  // the user to the default palette.
  const h=$('#sTheme');
  if(h)h.innerHTML=`<span class="chip on" data-v="${esc(cur)}"></span>`;
  const box=$('#thGallery');if(!box)return;
  // hidden palettes stay loadable (a saved settings.json may name one) but are
  // not offered — see HIDDEN_PALETTES in themes.py. The one you are currently
  // wearing is always shown, or selecting it would look like it vanished.
  const names=Object.keys(ST.themes||{}).filter(n=>!ST.themes[n].hidden||n===cur);
  names.sort((a,b)=>{
    const fa=FAM_ORDER.indexOf(ST.themes[a].family),fb=FAM_ORDER.indexOf(ST.themes[b].family);
    return fa-fb||a.localeCompare(b);
  });
  box.innerHTML=names.map(n=>themeCardHtml(n,ST.themes[n],cur)).join('');
  // Click to select. No hover preview — see the note in drawSkinPicker: it
  // rebuilt a whole three.js scene per card the pointer crossed, and it was not
  // wanted anyway ("devo cliccare su un tema per selezionarlo").
  box.querySelectorAll('.thcard').forEach(card=>{
    card.addEventListener('click',()=>themePick(card.dataset.v));
  });
  drawWorldPicker();
  drawSkinPicker();
  chipsFill($('#sMotion'),['full','subtle','off'],null,ST.motion||'full');
  const mb=$('#sMotion');
  if(mb)mb.querySelectorAll('.chip').forEach(c=>c.addEventListener('click',()=>{
    ST.motion=c.dataset.v;
    MO.set(ST.motion);
    localStorage.setItem('ctl_motion',ST.motion);
    post('/api/settings',{motion:ST.motion});
    // gauges hold their last frame when motion goes off, so nudge them to
    // redraw once under the new rules rather than freezing mid-tween
    INST.refit();
  }));
  // The background gets its own switch rather than riding on `motion`. It is a
  // second GPU surface in a shell with a documented tearing history, so it has
  // to be turnable down without also flattening every transition in the app.
  chipsFill($('#sStage'),['cinematic','lite','off'],
    {cinematic:'cinematic',lite:'lite (no bloom)',off:'off'},ST.stage||'cinematic');
  const sb=$('#sStage');
  if(sb)sb.querySelectorAll('.chip').forEach(c=>c.addEventListener('click',()=>{
    ST.stage=c.dataset.v;
    localStorage.setItem('ctl_stage',ST.stage);
    if(window.STAGE)STAGE.setTier(ST.stage);
    post('/api/settings',{stage:ST.stage});
    const n=$('#stgNote');if(n)n.textContent=STAGE_NOTE[ST.stage]||'';
  }));
  const sn=$('#stgNote');if(sn)sn.textContent=STAGE_NOTE[ST.stage||'cinematic']||'';
  drawSurfaceSlider();
}
/* Surface transparency. Live on input (you judge this by looking at it, not by
   saving it) and persisted on release, so dragging does not fire a POST per
   pixel. 0 in storage means "whatever the look asks for". */
function drawSurfaceSlider(){
  const el=$('#sSurf');if(!el)return;
  const skOp=(curSkin().op!=null?curSkin().op:1);
  const show=v=>{const r=$('#surfRead');
    if(r)r.textContent=ST.surface?`${v}% opaque`:`${Math.round(skOp*100)}% opaque — this theme's default`;};
  el.value=String(ST.surface||Math.round(skOp*100));
  show(el.value);
  el.oninput=()=>{ST.surface=+el.value;applySkin(skinFor(ST.theme));show(el.value);};
  el.onchange=()=>post('/api/settings',{surface:+el.value});
  const rs=$('#surfReset');
  if(rs)rs.onclick=e=>{e.preventDefault();ST.surface=0;
    applySkin(skinFor(ST.theme));drawSurfaceSlider();post('/api/settings',{surface:0});};
}
const STAGE_NOTE={
  cinematic:'Full scene with bloom. Each skin brings its own — a wireframe horizon, a petal field, a neon flythrough.',
  lite:'The same scene without bloom. Two fewer render targets — try this first if the Qt window ever tears.',
  off:'No 3D background at all. Falls back to the static gradient wash.',
};
/* Applies and saves immediately. A theme is a setting you judge by looking at
   it, not one you stage behind a Save button — and the gallery previews on
   hover, so a Save step would only add a way to lose the choice. */
async function themePick(name){
  ST.theme=name;
  applyTheme(name);
  localStorage.setItem('ctl_theme',name);
  drawThemeGallery();
  await post('/api/settings',{theme:name});
}

async function setPlanExecSave(){
  await post('/api/settings',{plan_model:chipVal($('#sPlanMod')),exec_model:chipVal($('#sExecMod'))});
  ST.plan_model=chipVal($('#sPlanMod'));ST.exec_model=chipVal($('#sExecMod'));
  toast('Plan → Execute models saved','ok');
}

/* ── OmniRoute (github.com/diegosouzapw/OmniRoute) — free-tier exec.
   OmniRoute's own "auto" pseudo-model (docs/routing/AUTO-COMBO.md) scores
   every currently-healthy free model (health/quota/cost/latency/task-fit)
   and picks the best one PER REQUEST, transparently swapping to the next
   one via its circuit-breaker when the current one is rate-limited or
   exhausted — server-side, invisible to the claude client. That's "auto"
   below; specific model ids are only for manually pinning one. ── */
const OR_AUTO='auto/coding';
function orExecModel(){
  return chipVal($('#sOrAuto'))||chipVal($('#sOrPin'))||OR_AUTO;
}
async function orRefresh(){
  const dot=$('#orDot');if(!dot)return;
  dot.textContent='checking…';dot.className='tag';
  const st=await api('/api/omniroute/status');
  const warn=$('#orNeedsProvider');
  const conns=$('#orConns');
  if(!st.reachable){
    dot.textContent='not running';dot.className='tag warn';
    if(warn)warn.style.display='none';
    if(conns)conns.innerHTML='';
    const wrap=$('#orModWrap');
    if(wrap)wrap.innerHTML=`<div style="color:var(--dim);font-size:13px;margin:6px 0">
      Not running right now — it auto-starts in the background the moment you run a
      Plan → Execute task via OmniRoute, or start it now:</div>
      <div class="mrow" style="margin-top:0"><button class="btn sm" onclick="orStart()">${ic('bolt')} Start now</button></div>`;
    return;
  }
  // Provider state comes from OmniRoute's HTTP providerHealth, not its CLI --
  // `providers list --json` crashes on this platform and returns nothing even
  // with providers connected, which is why this card used to read "0 providers
  // connected" while 5 were live. CLOSED = healthy, HALF_OPEN = recovering,
  // OPEN = tripped.
  const provs=st.providers||[];
  const ok=provs.filter(p=>p.state==='CLOSED').length;
  const half=provs.filter(p=>p.state==='HALF_OPEN').length;
  const usable=st.usable_count||0;
  dot.textContent=provs.length
    ?`${ok} healthy${half?` · ${half} recovering`:''} · ${usable} usable model${usable===1?'':'s'}`
    :'0 providers connected';
  dot.className='tag '+(ok>0&&usable>0?'ok':'warn');
  if(warn)warn.style.display=(ok>0)?'none':'';
  const STATE={CLOSED:['ok','healthy'],HALF_OPEN:['warn','recovering'],OPEN:['err','down']};
  let html=provs.map(p=>{
    const[cls,lbl]=STATE[p.state]||['','unknown'];
    return `<div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12.5px">
      <span class="tag ${cls}">${lbl}</span><b>${esc(p.name)}</b>
      <span style="color:var(--dim)">${p.failures?esc(p.failures+' failure'+(p.failures===1?'':'s')):''}</span>
      </div>`;}).join('');
  (st.lockouts||[]).forEach(l=>{
    const mins=Math.round((l.remaining_ms||0)/60000);
    html+=`<div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12.5px">
      <span class="tag warn">locked</span><b>${esc(l.model||l.provider)}</b>
      <span style="color:var(--dim)">${esc(l.reason||'')}${mins?` · ${mins} min left`:''}</span></div>`;
  });
  if(conns)conns.innerHTML=html||'<div style="color:var(--dim);font-size:12.5px">No providers connected yet.</div>';
  const m=await api('/api/omniroute/models');
  const wrap=$('#orModWrap');if(!wrap)return;
  const cur=ST.omniroute_exec_model||OR_AUTO;
  // Only models on a configured, non-tripped provider that can actually run a
  // session. The full catalog lists every routable id regardless of whether a
  // provider backing it is connected, so offering it invites picking a model
  // that 401s on every turn.
  const real=(m.usable||[]).map(u=>u.id);
  const pinIds=real.length?real:m.models.filter(x=>!x.startsWith('auto/'));
  const nEx=Object.values(m.excluded||{}).reduce((a,v)=>a+v.length,0);
  const exLines=Object.entries(m.excluded||{}).map(([why,ids])=>
    `<div style="padding:2px 0"><b>${ids.length}</b> · ${esc(why)}
      <span style="color:var(--dim)">${esc(ids.slice(0,4).join(', '))}${ids.length>4?', …':''}</span></div>`).join('');
  wrap.innerHTML=`<div class="fld"><label>Execute model</label>
    <div class="chips" id="sOrAuto"></div>
    <details style="margin-top:8px"${pinIds.length&&!real.length?'':''}>
      <summary style="cursor:pointer;color:var(--dim);font-size:12px">Pin a specific model instead —
        ${pinIds.length} that can actually run a session</summary>
      <div class="chips" id="sOrPin" style="margin-top:6px;max-height:260px;overflow-y:auto"></div>
    </details>
    ${nEx?`<details style="margin-top:6px">
      <summary style="cursor:pointer;color:var(--dim);font-size:12px">${nEx} catalogued models hidden — why</summary>
      <div style="font-size:12px;margin-top:6px;line-height:1.55">${exLines}
        <div style="color:var(--dim);margin-top:6px">A provider must be connected in the OmniRoute
          dashboard before its models can serve anything, and a session needs tool support and a real
          context window.</div></div></details>`:''}
    </div>`;
  chipsFill($('#sOrAuto'),[OR_AUTO],['Auto — best free model, auto-fallback'],cur===OR_AUTO?OR_AUTO:'');
  chipsFill($('#sOrPin'),pinIds,pinIds.map(id=>m.labels[id]||id),cur!==OR_AUTO?cur:'');
  $('#sOrAuto').querySelectorAll('.chip').forEach(c=>c.addEventListener('click',()=>
    $('#sOrPin').querySelectorAll('.chip').forEach(x=>x.classList.remove('on'))));
  $('#sOrPin').querySelectorAll('.chip').forEach(c=>c.addEventListener('click',()=>
    $('#sOrAuto').querySelectorAll('.chip').forEach(x=>x.classList.remove('on'))));
}
function orTestConn(id){
  runJob('omniroute_test_connection',{conn_id:id},st=>{
    const r=st.result||{};
    // this self-check can be wrong either way -- report it as informational,
    // never as a verdict (that's what "Send a live test" is for)
    toast(`OmniRoute self-check: ${r.message||(r.ok?'ok':'reported an issue')} (may not reflect reality)`,'');
    orRefresh();
  });
}
function orLiveTest(){
  const model=orExecModel();
  $('#orLiveResult').textContent='Sending a real request through '+model+'…';
  runJob('omniroute_live_test',{model},st=>{
    const r=st.result||{};
    $('#orLiveResult').innerHTML=r.ok
      ?`<span style="color:var(--ok)">${ic('check')} Works — ${esc(r.message||'')}</span>`
      :`<span style="color:var(--err)">${ic('close')} Failed — ${esc(r.message||'')}</span>`;
    toast(r.ok?'Live test passed — OmniRoute is actually working':'Live test failed','ok');
  });
}
async function orStart(){
  runJob('omniroute_ensure',{},st=>{
    toast(st.result&&st.result.ok?'OmniRoute started':'Could not start — check it\'s installed','ok');
    orRefresh();
  });
}
function orDashboard(){
  window.open(($('#orUrl').value||'http://localhost:20128'),'_blank');
}
async function orSave(){
  const body={omniroute_base_url:$('#orUrl').value,omniroute_exec_model:orExecModel()};
  if($('#orKey').value)body.omniroute_api_key=$('#orKey').value;
  await post('/api/settings',body);
  ST=await api('/api/state');
  $('#orKey').value='';
  toast('OmniRoute settings saved','ok');
  orRefresh();
}

/* Probe every candidate with a real request. The catalog and the health endpoint
   can only predict; providerHealth membership in particular means "attempted",
   not "configured" (it grows as requests are made), so this is the one check
   that settles it — and its output is exactly the failover candidate list. */
let ORPROBE=[];
function orProbe(full){
  const out=$('#orProbeOut');
  out.innerHTML=`<span style="color:var(--dim)">Probing with real requests — stops as soon as 4 answer, ${full?'re-testing everything (spends more quota)':'skipping models already known dead'}, ${full?'240':'45'}s ceiling…</span>`;
  runJob('omniroute_probe',full?{full:true,want:0,budget:240}:{},st=>{
    const r=st.result||{};ORPROBE=r.results||[];
    if(!ORPROBE.length){out.innerHTML='<span style="color:var(--dim)">Nothing to probe.</span>';return;}
    // A timeout is not a verdict on the model -- it means the probe budget
    // expired (requests queue inside OmniRoute). Grouping it with hard failures
    // is what makes the same model look broken in one run and fine in the next.
    const CLASS={works:['ok','works'],timeout:['warn','no answer in time'],
      limited:['warn','rate/quota limit'],gone:['err','retired (410)'],
      auth:['err','key not authorized'],skipped:['','not probed'],error:['err','error']};
    const NOTE={timeout:'Budget expired, not a model fault — retried once already. Usually clears.',
      limited:'Free-tier budget spent on this key. Resets on a timer.',
      gone:'The provider removed this model id. Safe to forget.',
      auth:'That provider\'s key is missing or rejected — fix it in the OmniRoute dashboard.',
      skipped:'Stopped early — enough models answered, or the time ceiling was hit.'};
    const ok=ORPROBE.filter(x=>x.ok);
    const groups={};ORPROBE.filter(x=>!x.ok).forEach(x=>{(groups[x.status||'error']=groups[x.status||'error']||[]).push(x);});
    out.innerHTML=
      `<div style="margin-bottom:4px"><b>${ok.length}</b> of ${ORPROBE.length} answered.</div>`+
      ok.map(x=>`<div style="padding:1px 0"><span class="tag ok">works</span> ${esc(x.id)}
        <span style="color:var(--dim)">${esc(x.served&&x.served!==x.id?'served by '+x.served:'')}</span></div>`).join('')+
      Object.entries(groups).map(([k,list])=>{
        const[cls,lbl]=CLASS[k]||CLASS.error;
        return `<details style="margin-top:4px"><summary style="cursor:pointer;color:var(--dim);font-size:12px">
          ${list.length} · ${esc(lbl)}</summary>
          ${NOTE[k]?`<div style="color:var(--dim);font-size:12px;margin:4px 0">${esc(NOTE[k])}</div>`:''}
          ${list.map(x=>`<div style="padding:1px 0"><span class="tag ${cls}">${esc(lbl)}</span> ${esc(x.id)}
            <span style="color:var(--dim)">${esc((x.detail||'').slice(0,80))}</span></div>`).join('')}</details>`;
      }).join('')+
      `<div class="mrow" style="margin-top:6px">
        ${ok.length?`<button class="btn sm" onclick="orUseWorking()">${ic('check')} Use these as the failover list</button>`:''}
        <button class="btn sm" onclick="orProbe(true)" title="Re-tests every model including ones cached as permanently dead — slower">${ic('refresh')} Re-test everything</button></div>`;
    toast(ok.length?`${ok.length} model(s) actually work`:'No model answered — no provider is serving requests','ok');
  });
}
async function orUseWorking(){
  const ids=ORPROBE.filter(x=>x.ok).map(x=>x.id).slice(0,8);
  if(!ids.length)return;
  $('#foModels').value=ids.join('\n');
  await foSave();
  toast('Failover list set from the models that actually answered','ok');
}

/* ── model failover (claudectl's own proxy — see failover.py) ── */
function foDot(){
  const d=$('#foDot');if(!d)return;
  const n=(ST.failover_models||[]).length;
  d.textContent=n?`${n} fallback${n>1?'s':''}`:'off';
  d.style.color=n?'var(--ok)':'var(--dim)';
  d.style.borderColor='currentColor';d.style.background='transparent';
}
async function foSave(){
  const models=$('#foModels').value.split('\n').map(s=>s.trim()).filter(Boolean).slice(0,8);
  const port=parseInt($('#foPort').value,10)||20129;
  await post('/api/settings',
    {failover_models:models,failover_port:port,failover_quiet:$('#foQuiet').checked});
  ST=await api('/api/state');
  $('#foModels').value=(ST.failover_models||[]).join('\n');
  foDot();
  $('#foResult').innerHTML=models.length
    ?`<span style="color:var(--ok)">${ic('check')} Failover on — the proxy starts with your next Plan → Execute run.</span>`
    :`<span style="color:var(--dim)">Failover off — sessions go straight to OmniRoute.</span>`;
  toast('Failover settings saved','ok');
}
function foStop(){
  runJob('failover_stop',{},st=>{
    const r=st.result||{};
    $('#foResult').textContent=r.message||'stopped';
    toast(r.ok?'Failover proxy stopped':'Could not stop the proxy','ok');
  });
}
const AM_INTERVALS=[[900,'every 15 min'],[1800,'every 30 min'],[3600,'every 60 min'],
  [7200,'every 2 hours'],[21600,'every 6 hours']];
async function drawAutoMemList(){
  const d=await api('/api/memory/auto');
  const sel=$('#amInt');
  if(sel)sel.innerHTML=AM_INTERVALS.map(([v,l])=>
    `<option value="${v}"${v===d.interval?' selected':''}>${l}</option>`).join('');
  const rows=(d.projects||[]).sort((a,b)=>(b.auto-a.auto)).map(p=>`
    <label class="amrow">
      <input type="checkbox" ${p.auto?'checked':''} onchange="amToggle(${hesc(p.enc)},this.checked)">
      <span class="nm">${esc(p.name)}${p.running?' <span class="tag ok">updating…</span>':''}</span>
      <span class="pt">${esc(p.path)}</span></label>`).join('');
  // re-resolve AND guard: this lands after an await, so navigating away between
  // the fetch and here leaves the node detached. #amInt above was already
  // guarded; this one was not, and threw an uncatchable pageerror on a fast
  // settings→home hop.
  const list=$('#amList');
  if(list)list.innerHTML=rows||'<div style="color:var(--dim)">No projects found.</div>';
}
async function amToggle(enc,on){
  await post('/api/memory/auto',{enc,auto:on});
  const p=(ST.projects||[]).find(x=>x.encoded===enc);if(p)p.auto_memory=on;
  drawProjects();toast(on?'Auto-memory on':'Auto-memory off','ok');
}
async function amSaveInterval(v){
  await post('/api/memory/auto',{interval:parseInt(v)});
  toast('Auto-memory interval saved','ok');
}
async function setSave(){
  await post('/api/settings',{default_effort:chipVal($('#sEff')),default_model:chipVal($('#sMod')),
    default_permission:chipVal($('#sPerm')),default_max_thinking:chipVal($('#sThink')),
    default_subagent_model:chipVal($('#sSub')),gui_shell:chipVal($('#sShell')),
    theme:chipVal($('#sTheme'))});
  ST=await api('/api/state');applyTheme(ST.theme);
  localStorage.setItem('ctl_theme',chipVal($('#sTheme')));toast('Settings saved','ok');
}
async function setMemLimitsSave(){
  await post('/api/settings',{
    // 0 in the box means "no cap", which the backend spells as null
    memory_max_calls:(+$('#sMemCalls').value||0)||null,
    memory_max_entities:+($('#sMemEnts').value||500),
    memory_auto_refresh:chipVal($('#sMemOpen')),
    memory_lessons:chipVal($('#sMemLessons'))});
  ST=await api('/api/state');toast('Memory limits saved','ok');
}
async function setPathsSave(){
  const r=await post('/api/settings',{editor:$('#sEditor').value.trim(),
    claude_exe:$('#sClaudeExe').value.trim(),
    claude_config_dir:$('#sCfgDir').value.trim(),
    headless_budget_usd:+($('#sBudget').value||0)});
  if(r&&r.error){toast(r.error,'err');return;}
  ST=await api('/api/state');toast('Saved','ok');
}
async function setOtelSave(){
  const hdr=(($('#sOtelHdr')||{}).value||'').trim();
  const body={otel_enabled:chipVal($('#sOtel'))==='on',
    otel_endpoint:($('#sOtelUrl')||{}).value||'',
    otel_protocol:chipVal($('#sOtelProto'))};
  // omit, do not blank: the field is write-only, so an untouched save must keep
  // the headers rather than erase them
  if(hdr)body.otel_headers=hdr;
  await post('/api/settings',body);
  ST=await api('/api/state');toast('OTEL settings saved','ok');
}
async function slRefresh(){
  const d=await api('/api/statusline');
  const dot=$('#slDot'),btn=$('#slBtn'),prev=$('#slPrev'),acc=$('#slAccts');
  if(!dot||!btn)return;
  // The statusline is configured per account dir, so "installed" was never one
  // bool. PARTIAL is the state a machine is left in by the old single-account
  // installer, and showing it is how you can tell the two apart at a glance.
  // Installed and VISIBLE are different questions. An account can carry a
  // perfectly good statusLine and still show nothing — the classic renderer
  // does not draw one — so "installed" alone was a true answer to the wrong
  // question, which is exactly how this looked like it was working.
  const st=d.partial?'partial':(d.blocked?'installed, not showing'
    :(d.installed?'installed':'not installed'));
  dot.textContent=st;
  dot.className='tag'+(d.blocked||d.partial?' warn':(d.installed?' ok':''));
  btn.textContent=d.installed?'Remove from every account':'Install for every account';
  btn.className='btn'+(d.installed?' danger':' pri');
  if(prev)prev.textContent=d.preview||'—';
  const accts=d.accounts||[];
  // one row per account only when there is more than one — a single-account
  // user should not be shown an account picker they never asked about
  if(acc)acc.innerHTML=accts.length<2?'':accts.map((a,i)=>{
    const bl=a.blockers||[];
    return `<div class="kv"><span>${esc(a.name)}</span><span>
       <span class="tag${bl.length&&a.installed?' warn':(a.installed?' ok':'')}">${
         a.installed?(bl.length?'not showing':'installed'):'—'}</span>
       <button class="btn sm" onclick="slOne(${i})">${a.installed?'remove':'install'}</button>
     </span></div>`
     +(a.installed&&bl.length?`<div class="kv"><span></span>
        <span style="color:var(--warn);font-size:12px">${esc(bl[0].why)}
        ${bl[0].code==='cwd-dependent-command'
          ?`<button class="btn sm" onclick="slFix(${i})">Repair</button>`:''}
        </span></div>`:'');}).join('');
  SL_ACCTS=accts;
}
let SL_ACCTS=[];
/* one click for the thing the warning just described, rather than sending the
   user off to work out the remedy themselves */
async function slFix(i){
  const a=SL_ACCTS[i];if(!a)return;
  const r=await post('/api/statusline',{action:'install',cfgdir:a.cfgdir});
  toast(r.ok?'Repaired — restart that session':(r.message||'Failed'),r.ok?'ok':'err');
  slRefresh();
}
async function slOne(i){
  const a=SL_ACCTS[i];if(!a)return;
  const r=await post('/api/statusline',
    {action:a.installed?'remove':'install',cfgdir:a.cfgdir});
  toast(r.message||'',r.ok?'ok':'err');slRefresh();
}
async function slToggle(){
  const d=await api('/api/statusline');
  const r=await post('/api/statusline',{action:d.installed?'remove':'install'});
  toast(r.message||'',r.ok?'ok':'err');slRefresh();
}
async function setExtractSave(){
  await post('/api/settings',{extract_model:chipVal($('#sExtract'))});
  ST.extract_model=chipVal($('#sExtract'));toast('Economy model saved','ok');
}

/* ── launch modal (chips, not <select> — native dropdowns flicker under
      QtWebEngine) ── */
/* Null-guarded because these are called from ASYNC renderers: a settings page
   that awaits a fetch and then fills its chips will finish into a #content the
   SPA has already replaced if you navigated away in the meantime. Every one of
   these took an element that existed when the render started and may not by the
   time it lands. setV() and the gallery renderers have always guarded; the chip
   helpers did not, and threw an uncatchable pageerror on a fast settings→home
   hop. Cheaper here than at ~20 call sites. */
function chipsFill(el,vals,labels,cur,onpick){
  if(!el)return;
  el.innerHTML=vals.map((v,i)=>
    `<span class="chip${v===cur?' on':''}" data-v="${esc(v)}">${esc(labels?labels[i]:(v||'default'))}</span>`).join('');
  el.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
    el.querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));
    c.classList.add('on');if(onpick)onpick(c.dataset.v);});
}
function chipVal(el){if(!el)return '';const c=el.querySelector('.chip.on');return c?c.dataset.v:'';}
function chipSet(el,v){if(!el)return;el.querySelectorAll('.chip').forEach(c=>
  c.classList.toggle('on',c.dataset.v===v));}
// model cards: grid-aligned rows with SWE% / cost / capability / best-for
function modelCardsFill(el,cur){
  if(!el)return;
  const rows=[['','default','','','account model','—']].concat(ST.options.model_cards||[]);
  el.innerHTML=rows.map(([mid,lbl,cost,cap,bf,swe])=>
    `<div class="mcard${mid===cur?' on':''}" data-v="${esc(mid)}">`
    +`<span class="mn">${esc(lbl)}</span>`
    +`<span class="mswe">${esc(swe||'')}</span>`
    +`<span class="mcost">${esc(cost)}</span>`
    +`<span class="mcap">${esc(cap)}</span>`
    +`<span class="mbf">${esc(bf)}</span></div>`).join('');
  el.querySelectorAll('.mcard').forEach(c=>c.onclick=()=>{
    el.querySelectorAll('.mcard').forEach(x=>x.classList.remove('on'));
    c.classList.add('on');updateHint();});
}
function cardVal(el){const c=el.querySelector('.mcard.on');return c?c.dataset.v:'';}
function cardSet(el,v){el.querySelectorAll('.mcard').forEach(c=>
  c.classList.toggle('on',c.dataset.v===v));}
// effort as a real range slider (0..N over ST.options.efforts, incl '' default at 0)
/* The tick row is GENERATED, and that is the whole fix for "the thumb shows
   HIGH when the value is xhigh": index.html carried six labels while EFFORTS
   had grown to seven, so the thumb at 4/6 of the track sat under the sixth
   label's HIGH and `ultracode` was an unlabelled stop. One list, from the data
   that drives the slider itself. */
function effortTicksFill(){
  const host=$('#fEffTicks');if(!host)return;
  const es=ST.options.efforts||[],n=es.length-1;
  host.innerHTML=es.map((e,i)=>
    `<span style="--i:${n?i/n:0}">${esc(e||'default')}</span>`).join('');
}
function effortVal(){return (ST.options.efforts||[])[+($('#fEffort').value)]||'';}
function effortSet(v){const i=(ST.options.efforts||[]).indexOf(v);$('#fEffort').value=i<0?0:i;}
// ── single frontier slider: each stop IS an (model,effort) the advisor
// already rates 'ok', so a bad combo can't be dialed in from this control ──
function frontierRows(){return ST.options.frontier||[];}
function frontierIdx(){return +($('#fFrontier').value||0);}
function frontierRow(){const rows=frontierRows();return rows[frontierIdx()]||rows[rows.length-1]||['','','','','',''];}
function frontierIndexFor(m,e){return frontierRows().findIndex(r=>r[0]===m&&r[1]===e);}
function setPinMode(on){
  $('#fPinModel').checked=on;
  $('#fPinBlock').style.display=on?'':'none';
  if(on&&!cardVal($('#fModel'))){const [m,e]=frontierRow();cardSet($('#fModel'),m);effortSet(e);}
}
function currentModelEffort(){
  if($('#fPinModel').checked)return [cardVal($('#fModel')),effortVal()];
  const r=frontierRow();return [r[0],r[1]];
}
function updateFrontierReadout(){
  const [mid,eff,lbl,cost,swe,note]=frontierRow();
  $('#fFrontRead').innerHTML=`<b>${esc(lbl)} · ${esc(eff)}</b>`
    +`<div class="fsub">${esc(swe)} SWE · ${esc(cost)}</div>`
    +`<div class="fsub" style="font-family:inherit">${esc(note)}</div>`;
}
function updateHint(){
  const [m,e]=currentModelEffort();
  const a=((ST.options.advice||{})[m]||{})[e]||['ok',''];
  let lvl=a[0],msg=a[1];
  /* A model you pinned that Anthropic has since retired is still IN the list —
     dropping it would reset the field to 'default' behind your back — so the
     picker has to be the thing that says it is gone. Launching it otherwise
     fails with an API error that never mentions where the id came from. */
  if((ST.options.model_retired||[]).indexOf(m)>=0){
    lvl='warn';
    msg='Anthropic no longer offers '+m+' — this launch will fail. Pick another model.';
  }
  $('#mHint').className='mhint adv-'+lvl;
  $('#mHint').innerHTML=(lvl==='warn'?'note: ':lvl==='tip'?'tip: ':'')+esc(msg)
    +`  <a id="mGuide">model guide ›</a>`;
  $('#mGuide').onclick=openGuide;
  const role=(ST.options.effort_profiles||{})[e]||'';
  $('#fEffLabel').textContent=(e||'default')+(role?' · '+role:'');
  updateFrontierReadout();
  updatePermHint(m);
  markPreset();
}
/* What the chosen mode means, and — the part that matters — whether it will
   actually apply. `auto` is unavailable on some models, and Claude Code starts
   the session in manual without saying so, so claudectl says so here. */
function updatePermHint(model){
  const el=$('#fPermHint');if(!el)return;
  const p=chipVal($('#fPerm'));
  const note=((ST.options.perm_notes||{})[p]||{})[model||'']||['',''];
  const desc=(ST.options.perm_profiles||{})[p]||'';
  el.className='fsub'+(note[0]?' adv-'+note[0]:'');
  el.textContent=note[1]||desc;
}
function markPreset(){
  const [m,e]=currentModelEffort(),th=chipVal($('#fThink')),su=chipVal($('#fSub'));
  const ps=ST.options.presets||[];
  document.querySelectorAll('#fPresets .preset').forEach((el,i)=>{
    const f=ps[i][2]||{};
    const ok=(!('model'in f)||f.model===m)&&(!('effort'in f)||f.effort===e)
      &&(!('max_thinking'in f)||f.max_thinking===th)&&(!('subagent_model'in f)||f.subagent_model===su);
    el.classList.toggle('on',ok);});
}
function applyPreset(fields){
  setPinMode(true);
  if('model'in fields)cardSet($('#fModel'),fields.model||'');
  if('effort'in fields)effortSet(fields.effort||'');
  if('max_thinking'in fields)chipSet($('#fThink'),fields.max_thinking||'');
  if('subagent_model'in fields)chipSet($('#fSub'),fields.subagent_model||'');
  updateHint();
}
function presetSummary(fields){
  const f=fields||{};
  const mc=(ST.options.model_cards||[]).find(r=>r[0]===f.model);
  const mlbl=mc?mc[1]:(f.model?f.model:'account model');
  const eff=f.effort||'default';
  const bits=[mlbl+' · '+eff];
  if(mc&&mc[2])bits.push(mc[2]);
  if(mc&&mc[5])bits.push(mc[5]);
  if(f.subagent_model){
    const sc=(ST.options.model_cards||[]).find(r=>r[0]===f.subagent_model);
    bits.push('+'+(sc?sc[1]:f.subagent_model)+' subagents');
  }
  return bits.join(' · ');
}
function presetsFill(el){
  const ps=(ST.options.presets)||[];
  el.innerHTML=ps.map(([n,d,f],i)=>
    `<div class="preset" data-i="${i}"><b>${esc(n)}</b><span>${esc(d)}</span>`
    +`<span class="pm">${esc(presetSummary(f))}</span></div>`).join('');
  el.querySelectorAll('.preset').forEach(c=>c.onclick=()=>applyPreset(ps[+c.dataset.i][2]||{}));
}
function openGuide(){
  const cards=(ST.options.model_cards)||[];
  $('#gCards').innerHTML=cards.map(([mid,lbl,cost,cap,bf,swe])=>
    `<div class="mcard"><span class="mn">${esc(lbl)}</span>`
    +`<span class="mswe">${esc(swe||'')}</span>`
    +`<span class="mcost">${esc(cost)}</span><span class="mcap">${esc(cap)}</span>`
    +`<span class="mbf">${esc(bf)}</span></div>`).join('');
  const ep=ST.options.effort_profiles||{};
  $('#gEffort').innerHTML=(ST.options.efforts||[]).filter(e=>e).map(e=>
    `<div class="mhint"><b>${esc(e)}</b> — ${esc(ep[e]||'')}</div>`).join('');
  $('#govl').classList.add('show');
}
function askLaunch(cfg){
  PENDING=cfg;
  $('#mTitle').textContent=cfg.title;$('#mSub').textContent=cfg.sub||'';
  const o=ST.options,d=ST.defaults;
  const [curModel,curEffort]=defaultModelEffort();
  presetsFill($('#fPresets'));
  modelCardsFill($('#fModel'),curModel);
  const sl=$('#fEffort');sl.max=(o.efforts||[1]).length-1;effortSet(curEffort);
  effortTicksFill();          // same list that just set sl.max
  sl.oninput=()=>{updateHint();};
  const fsl=$('#fFrontier');fsl.max=Math.max((o.frontier||[1]).length-1,0);
  const fi=frontierIndexFor(curModel,curEffort);
  fsl.value=fi>=0?fi:Math.min(2,(o.frontier||[1]).length-1);
  fsl.oninput=()=>{setPinMode(false);updateHint();};
  $('#fPinModel').onchange=()=>{setPinMode($('#fPinModel').checked);updateHint();};
  // an explicit saved default that isn't one of the frontier stops → respect
  // it by opening pinned to the exact combo, rather than silently rounding
  setPinMode(fi<0&&!!(curModel||curEffort));
  chipsFill($('#fPerm'),o.perms,o.perm_labels,d.perm,()=>updateHint());
  chipsFill($('#fThink'),o.thinking,o.thinking_labels,d.max_thinking);
  chipsFill($('#fSub'),o.models,o.model_labels,d.subagent_model);
  chipsFill($('#fWt'),['','*'],['off','auto'],'');
  // OmniRoute: fetch available models and show the section only if reachable
  const orWrap=$('#fOmniWrap');orWrap.style.display='none';
  api('/api/omniroute/models').then(m=>{
    const models=m&&m.models||[];
    if(!models.length)return;
    const cur=ST.omniroute_exec_model||'';
    const vals=['',...models];
    const lbls=['off (use Anthropic API)',...models.map(id=>id==='auto/coding'?'auto/coding (dynamic)':id)];
    chipsFill($('#fOmni'),vals,lbls,cur);
    orWrap.style.display='';
  }).catch(()=>{});
  $('#fAcctWrap').style.display=(cfg.isNew&&ST.accounts.length>1)?'':'none';
  $('#fNameWrap').style.display=cfg.isNew?'':'none';
  $('#fWtWrap').style.display=cfg.isNew?'':'none';
  $('#fName').value='';
  if(cfg.isNew&&ST.accounts.length>1)
    chipsFill($('#fAcct'),ST.accounts.map(a=>a.dir),
              ST.accounts.map(a=>a.name),ST.active_cfgdir);
  updateHint();
  $('#ovl').classList.add('show');
}
async function doLaunch(){
  const c=PENDING;if(!c)return;
  const [model,effort]=currentModelEffort();
  const opts={effort,model,
    perm:chipVal($('#fPerm')),max_thinking:chipVal($('#fThink')),
    subagent_model:chipVal($('#fSub')),
    name:c.isNew?$('#fName').value:'',worktree:c.isNew?chipVal($('#fWt')):'',
    cfgdir:c.isNew&&ST.accounts.length>1?chipVal($('#fAcct')):(c.cfgdir||''),
    omniroute:chipVal($('#fOmni'))};
  $('#ovl').classList.remove('show');
  const r=await post('/api/launch',{path:c.path,enc:c.enc,choice:c.choice,opts});
  if(r.ok)toast('Launched in a new terminal window','ok');
  else toast('Launch failed: '+(r.error||'unknown'),'err');
}

/* ── open a new project by path (mirror of the TUI's __open_path__) ── */
let OSEL=-1,OROWS=[],_oTimer=null;
function openProjectByPath(){
  OSEL=-1;OROWS=[];
  $('#oPath').value='';$('#oSugg').innerHTML='';$('#oErr').textContent='';
  $('#oovl').classList.add('show');
  setTimeout(()=>$('#oPath').focus(),30);
  suggestPaths('');
}
async function suggestPaths(text){
  const d=await api('/api/path-complete?'+qs({text}));
  OROWS=d.dirs||[];OSEL=-1;
  $('#oSugg').innerHTML=OROWS.map((p,i)=>
    `<div class="s" data-i="${i}">${esc(p)}</div>`).join('')
    +(d.more?`<div class="more">… ${d.more} more — keep typing to narrow</div>`:'');
  $('#oSugg').querySelectorAll('.s').forEach(el=>
    el.onclick=()=>{$('#oPath').value=OROWS[+el.dataset.i]+'\\';
      $('#oPath').focus();scheduleSuggest();});
}
function scheduleSuggest(){clearTimeout(_oTimer);
  _oTimer=setTimeout(()=>suggestPaths($('#oPath').value),160);}
function oHighlight(){$('#oSugg').querySelectorAll('.s').forEach((el,i)=>
  el.classList.toggle('on',i===OSEL));
  const on=$('#oSugg .s.on');if(on)on.scrollIntoView({block:'nearest'});}
async function openPathSubmit(){
  // an active suggestion → drill into it instead of opening (matches TUI ENTER)
  if(OSEL>=0&&OROWS[OSEL]){$('#oPath').value=OROWS[OSEL]+'\\';
    OSEL=-1;suggestPaths($('#oPath').value);return;}
  const r=await post('/api/open-path',{path:$('#oPath').value});
  if(!r.ok){$('#oErr').textContent=r.error||'Could not open that path';return;}
  $('#oovl').classList.remove('show');
  askLaunch({title:'New session',sub:r.path,isNew:true,
    path:r.path,enc:r.enc,choice:'new'});
}

/* ── wiring ── */
$('#q').oninput=drawProjects;
$('#mCancel').onclick=()=>$('#ovl').classList.remove('show');
$('#mGo').onclick=doLaunch;
$('#gClose').onclick=()=>$('#govl').classList.remove('show');
$('#bOpenPath').onclick=openProjectByPath;
$('#oCancel').onclick=()=>$('#oovl').classList.remove('show');
$('#oOk').onclick=openPathSubmit;
$('#oPath').oninput=()=>{OSEL=-1;scheduleSuggest();};
$('#oPath').onkeydown=e=>{
  if(e.key==='ArrowDown'){e.preventDefault();if(OROWS.length){OSEL=(OSEL+1)%OROWS.length;oHighlight();}}
  else if(e.key==='ArrowUp'){e.preventDefault();if(OROWS.length){OSEL=(OSEL-1+OROWS.length)%OROWS.length;oHighlight();}}
  else if(e.key==='Enter'){e.preventDefault();openPathSubmit();}};
$('#dClose').onclick=()=>$('#drawer').classList.remove('show');
let __lastFocus=null;
document.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&$('#ovl').classList.contains('show')
     &&e.target.tagName!=='INPUT')doLaunch();
  if(e.key==='Escape'){
    const openModals=['#ovl','#govl','#oovl','#jovl','#povl','#actovl'].find(id=>$(id)&&$(id).classList.contains('show'));
    if(openModals)$(openModals).classList.remove('show');
    else $('#drawer').classList.remove('show');
    if(__lastFocus){__lastFocus.focus();__lastFocus=null;}
  }
});
// save focus when opening a modal, restore on close
document.querySelectorAll('.ovl').forEach(el=>{
  el.addEventListener('click',function(e){if(e.target===this&&__lastFocus){__lastFocus.focus();__lastFocus=null;this.classList.remove('show');}});
  const observer=new MutationObserver(function(){
    if(el.classList.contains('show')){__lastFocus=document.activeElement;
      setTimeout(function(){const f=el.querySelector('button, [href], input, textarea, select');if(f)f.focus();},50);}});
  observer.observe(el,{attributes:true,attributeFilter:['class']});
});
$('#bNew').onclick=()=>CUR&&askLaunch({title:'New session',sub:CUR.name,isNew:true,
  path:CUR.path,enc:CUR.encoded,choice:'new'});
$('#bCont').onclick=()=>CUR&&askLaunch({title:'Continue latest',sub:CUR.name,isNew:false,
  path:CUR.path,enc:CUR.encoded,choice:'continue',cfgdir:CUR.primary_cfgdir});
$('#bHide').onclick=toggleHidden;
$('#bTerm').onclick=async()=>{if(!CUR)return;
  const r=await post('/api/launch',{path:CUR.path,enc:CUR.encoded,choice:'terminal',opts:{}});
  toast(r.ok?'Terminal opened':'Failed: '+(r.error||''),r.ok?'ok':'err');};
async function setMode(m){await post('/api/settings',{ui_mode:m});ST.ui_mode=m;segDraw();
  toast('Default interface: '+m.toUpperCase()+' (next start)','ok');}
function segDraw(){
  $('#segTui').className=ST.ui_mode==='tui'?'on':'';
  $('#segGui').className=ST.ui_mode==='gui'?'on':'';}
$('#segTui').onclick=()=>setMode('tui');
$('#segGui').onclick=()=>setMode('gui');
// brand click → home
document.querySelector('.brand').style.cursor='pointer';
document.querySelector('.brand').onclick=()=>go('home');

/* ── all-accounts usage banner (mirrors the TUI's grid) ── */
let _uTimer=null;
/* one account row — always rendered, '—' until windows have data */
function usageRow(a){
  const wins=(a.windows||[]).map(w=>`<span class="uwin${w.pct>=80?' hot':''}">
    <span class="ulbl">${esc(w.label)}</span>
    <span class="ubarm"><i style="width:${Math.min(100,w.pct)}%"></i></span>
    <span class="upct">${Math.round(w.pct)}%</span>
    <span class="urst">${w.resets?'→ '+esc(w.resets):''}</span>
  </span>`).join('');
  return `<div class="urow">
    <span class="uacct" title="${esc(a.email||a.account)}">${esc(a.email||a.account)}</span>
    ${wins||'<span style="color:var(--dim2)">—</span>'}
  </div>`;
}
async function drawUsageBar(force){
  clearTimeout(_uTimer);
  if(document.hidden||!_VIS){_uTimer=setTimeout(drawUsageBar,60000);return;}
  if(force){const b=$('#uRefresh');if(b){b.classList.add('busy');b.innerHTML='<span class="spin"></span>';}}
  try{
    const d=await api('/api/usage/plan'+(force?'?refresh=1':''));
    $('#ubar').innerHTML=`<div class="urow">
      <button class="btn sm" id="uRefresh" title="Refresh usage now"
        onclick="drawUsageBar(true)">${ic('refresh')}</button>
      <div style="flex:1;display:flex;flex-direction:column;gap:2px">
        ${(d.accounts||[]).map(usageRow).join('')}
      </div></div>`;
    // a forced refresh only kicks the background poller — come back for it
    _uTimer=setTimeout(drawUsageBar,force?2500:60000);
  }catch(e){
    if(force){const b=$('#uRefresh');if(b){b.classList.remove('busy');b.innerHTML=ic('refresh');}}
    _uTimer=setTimeout(drawUsageBar,60000);
  }
}

/* ── "claudectl N is out" strip ──
   Its own element (#updbar), not a row inside #ubar, because drawUsageBar
   rewrites that wholesale every 60s. Never polls: one fetch per session. */
async function drawUpdateBar(){
  let c;
  try{c=(await api('/api/versions')).claudectl||{};}catch(e){return;}
  if(!c.update)return;
  const host=$('#updbar');if(!host)return;
  const act=c.mode==='checkout'
    ? `<span style="color:var(--dim2)">run <code>git pull</code> in your checkout</span>`
    : `<button class="btn sm pri" id="updNow">Update now</button>`;
  host.innerHTML=`<span class="uptxt">${ic('download')} <b>claudectl ${esc(c.latest)}</b>
    is available — you have ${esc(c.installed||'?')}</span>${act}
    <button class="btn sm" id="updHide">Dismiss</button>`;
  host.style.display='flex';
  const hide=$('#updHide');if(hide)hide.onclick=()=>{host.style.display='none';};
  const now=$('#updNow');
  if(now)now.onclick=()=>inlineJob('#updbar','claudectl_update',{},
    {label:'Scheduling the claudectl upgrade',
     onDone:()=>{toast('claudectl upgrades once you close it');
                 host.style.display='none';}});
}

/* global poll: which projects' memory is updating right now (scheduler or
   on-open), so the sidebar markers stay live regardless of the open page */
async function pollActiveMem(){
  try{
    const d=await api('/api/memory/active');
    const next=new Set(d.active||[]);
    const changed=next.size!==ACTIVE_MEM.size||[...next].some(p=>!ACTIVE_MEM.has(p));
    ACTIVE_MEM=next;
    if(changed)drawProjects();
  }catch(e){}
}
/* One ticker for the always-on chrome polls instead of two independent
   setTimeout chains each re-checking visibility on its own. */
/* the chips live in the top bar on every page, but only the dashboard poll
   knows failover/mcp state — off the dashboard, refresh them slowly */
function renderHdrChips(d){
  const hc=$('#hdrChips');if(!hc)return;
  const on=d.failover&&d.failover.running;
  const all=(d.mcp||[]),up=all.filter(m=>m.running).length;
  setV(hc,`<span class="hchip act${on?' ok':''}" onclick="go('settings')"
      title="Failover proxy — click to configure">failover${on&&d.failover.port?':'+d.failover.port:''}</span>`
    +`<span class="hchip act${up?' ok':''}" onclick="go('mcp')"
      title="MCP servers">mcp ${up}/${all.length}</span>`);
}
async function tickHdrChips(){
  if(PAGE_==='home')return;              // the dashboard poll already does it
  try{const d=await api('/api/dashboard');renderHdrChips(d);}catch(e){}
}
/* The ambient-motion layer that used to live here (26 generative canvas
   renderers mounted into ~30 places, looping forever) is gone. Readable
   gauges live in instruments.js and are placed next to the numbers they
   describe; the micro-interaction vocabulary lives in motion.js. Page
   renderers push data with INST.set() — nothing here fetches. */

const HB_MS=2500,HB_TASKS=[[1,pollMemProg],[2,pollActiveMem],[12,tickHdrChips]];
let HB_TIMER=null,HB_N=0;
function heartbeat(){
  HB_N++;
  if(document.hidden||!_VIS)return;
  for(const[every,fn]of HB_TASKS)if(HB_N%every===0)fn();
}
function startHeartbeat(){if(!HB_TIMER){heartbeat();HB_TIMER=setInterval(heartbeat,HB_MS);}}

(async()=>{
  ST=await api('/api/state');
  // restore saved theme/account from localStorage
  const lsTheme=localStorage.getItem('ctl_theme');
  const lsAcct=localStorage.getItem('ctl_account');
  if(lsTheme&&ST.themes&&ST.themes[lsTheme])ST.theme=lsTheme;
  if(lsAcct)ST.active_cfgdir=lsAcct;
  const lsMo=localStorage.getItem('ctl_motion');
  if(lsMo)ST.motion=lsMo;
  const lsSk=localStorage.getItem('ctl_skin');
  if(lsSk!==null)ST.skin=lsSk;
  const lsStg=localStorage.getItem('ctl_stage');
  if(lsStg)ST.stage=lsStg;
  const lsW=localStorage.getItem('ctl_world');
  if(lsW!==null&&(lsW===''||(ST.worlds||{})[lsW]))ST.world=lsW;
  MO.set(ST.motion||'full');
  // Tell the stage its tier BEFORE applyTheme, which hands it the palette and
  // skin. The vendor bundle is deferred, so boot() may not have happened yet —
  // STAGE holds the settings and mounts when `vendor-ready` fires.
  if(window.STAGE){
    STAGE.tier=ST.stage||'lite';
    if(STAGE.tier==='off'||!MO.on)STAGE._static();
  }
  applyTheme(ST.theme);segDraw();
  applySideWidth(ST.side_w||0);applyNavHeight(ST.nav_h||0);bindSideGrips();
  if(window.STAGE)STAGE.page(PAGE_);
  MO.spot($('#content'));
  watchContent();
  // the Qt shell and the 720px drawer both change #content's width WITHOUT
  // firing a window resize, so instruments re-fit from an observer instead
  if(window.ResizeObserver)new ResizeObserver(()=>INST.refit()).observe($('#content'));
  $('#bTerm').innerHTML=ic('terminal')+' Terminal';
  $('#bCont').innerHTML=ic('history')+' Continue';
  $('#bNew').innerHTML=ic('add')+' New session';
  render();drawUsageBar();startHeartbeat();
  // deferred, and fetched ONCE per session: /api/versions also walks every
  // marketplace on disk, which has no business competing with the first paint
  // or repeating on a timer for an answer that changes once a release.
  setTimeout(drawUpdateBar,3000);
})();
