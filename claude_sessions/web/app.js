
'use strict';
const $=s=>document.querySelector(s);
const api=(p,opt={})=>fetch(p,{...opt,headers:{'X-Claudectl':'1',
  'Content-Type':'application/json',...(opt.headers||{})}}).then(r=>r.json());
const post=(p,body)=>api(p,{method:'POST',body:JSON.stringify(body||{})});

let ST=null, CUR=null, TAB='sessions', PAGE_='home', PENDING=null, SESS=[];
let ACTIVE_MEM=new Set();   // project paths whose memory is refreshing right now

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
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML;}
function C(){return {path:CUR.path,enc:CUR.encoded,cfgdir:CUR.primary_cfgdir};}
/* stable per-account color: default = green, others spread on the hue wheel */
function acctColor(name){
  if(!name||name==='default')return 'var(--ok)';
  const i=Math.max(0,(ST.accounts||[]).findIndex(a=>a.name===name));
  return `hsl(${(255+i*137)%360} 75% 66%)`;
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
const ic=n=>`<svg class="ic" viewBox="0 0 24 24"><path d="${ICONS[n]||ICONS.doc}"/></svg>`;

/* ── theme → CSS variables (palette mirrors the TUI themes) ── */
function applyTheme(name){
  const t=(ST.themes||{})[name];if(!t)return;
  const r=document.documentElement.style;
  const map={'--cyan':t.accent,'--violet':t.accent2,'--ok':t.ok,'--warn':t.warn,
    '--bg':t.bg,'--bg2':t.bg2,'--panel':t.panel,'--panel2':t.panel2,
    '--line':t.line,'--txt':t.txt,'--dim':t.dim,'--dim2':t.dim2,'--code':t.code};
  for(const[k,v]of Object.entries(map))if(v)r.setProperty(k,v);
  // text on gradient: dark bg themes use the bg color, light themes white
  const lum=parseInt(t.bg.slice(1,3),16);
  r.setProperty('--onacc',lum>128?'#ffffff':t.bg);
  r.setProperty('--grad',`linear-gradient(135deg,${t.accent},${t.accent2})`);
}

/* ── prompt/confirm helpers ── */
function ask(title,fields,sub){return new Promise(res=>{
  $('#pTitle').textContent=title;$('#pSub').textContent=sub||'';
  $('#pBody').innerHTML=fields.map((f,i)=>f.type==='textarea'
    ?`<div class="fld"><label>${esc(f.label)}</label><textarea id="pf${i}">${esc(f.value||'')}</textarea></div>`
    :f.type==='select'
    ?`<div class="fld"><label>${esc(f.label)}</label><div class="chips" id="pf${i}">${f.options.map((o,j)=>
        `<span class="chip${j===0?' on':''}" data-v="${esc(o[0])}">${esc(o[1])}</span>`).join('')}</div></div>`
    :`<div class="fld"><label>${esc(f.label)}</label><input id="pf${i}" value="${esc(f.value||'')}" placeholder="${esc(f.ph||'')}"></div>`).join('');
  document.querySelectorAll('#pBody .chips').forEach(box=>
    box.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
      box.querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));
      c.classList.add('on');}));
  $('#povl').classList.add('show');
  const done=v=>{$('#povl').classList.remove('show');res(v);};
  $('#pOk').onclick=()=>done(fields.map((f,i)=>{
    const el=$('#pf'+i);
    return el.classList.contains('chips')?chipVal(el):el.value;}));
  $('#pCancel').onclick=()=>done(null);
  const first=$('#pf0');if(first)first.focus();
});}
function confirmBox(title,sub){return ask(title,[],sub).then(v=>v!==null);}

function fld(id,label){return `<div class="fld"><label>${label}</label><div class="chips" id="${id}"></div></div>`;}

/* ── job runner ── */
async function runJob(kind,params,onDone){
  const r=await post('/api/job',{kind,...params});
  if(!r.ok){toast(r.error||'Could not start','err');return;}
  const jid=r.job;
  const memPath=kind==='memory_build'?params.path:null;
  let __plMsgs='',__plSub='',__plLabel='',__plGateTitle='';
  $('#jovl').classList.add('show');$('#jGate').style.display='none';
  $('#jCancelRow').style.display='';
  $('#jCancel').onclick=async()=>{await post(`/api/job/${jid}/cancel`);};
  const poll=async()=>{
    const st=await api(`/api/job/${jid}`);
    if(!st){$('#jovl').classList.remove('show');return;}
    if(st.label!==__plLabel){__plLabel=st.label;$('#jLabel').textContent=st.label;}
    let sub=`${st.elapsed||0}s elapsed`;
    if(memPath&&st.status==='running'){
      const mp=await api('/api/memory/progress?path='+encodeURIComponent(memPath));
      if(mp.progress)sub+=` — ${mp.progress}`;
    }
    if(sub!==__plSub){__plSub=sub;$('#jSub').textContent=sub;}
    const msgsHtml=(st.messages||[]).map(m=>
      `<div class="${m.ok?'':'bad'}">${esc(m.text)}</div>`).join('');
    if(msgsHtml!==__plMsgs){__plMsgs=msgsHtml;$('#jMsgs').innerHTML=msgsHtml;}
    if(st.status==='awaiting'&&st.gate){
      if(st.gate.title!==__plGateTitle){__plGateTitle=st.gate.title;
        $('#jTitle').innerHTML=esc(st.gate.title);}
      $('#jGate').style.display='';$('#jCancelRow').style.display='none';
      $('#jDiff').innerHTML=(st.gate.diff||[]).map(l=>{
        const c=l.startsWith('+++')||l.startsWith('---')||l.startsWith('@@')?'h'
              :l.startsWith('+')?'a':l.startsWith('-')?'d':'';
        return `<div class="${c}">${esc(l)}</div>`;}).join('')||'<div>(no diff)</div>';
      $('#jApply').onclick=async()=>{await post(`/api/job/${jid}/decide`,{apply:true});
        $('#jGate').style.display='none';$('#jCancelRow').style.display='';
        __plLabel='';$('#jTitle').innerHTML='<span class="spin"></span> <span id="jLabel">Working…</span>';
        setTimeout(poll,300);};
      $('#jReject').onclick=async()=>{await post(`/api/job/${jid}/decide`,{apply:false});
        $('#jGate').style.display='none';setTimeout(poll,300);};
      return;   // paused at gate — no auto-poll
    }
    if(st.status==='running'){setTimeout(poll,600);return;}
    $('#jovl').classList.remove('show');
    if(st.status==='done'){toast(st.label+' — done','ok');if(onDone)onDone(st);}
    else if(st.status==='cancelled')toast('Cancelled','');
    else if(st.error){toast(st.error,'err');if(onDone)onDone(st);}
    else toast('Failed','err');
  };
  $('#jTitle').innerHTML='<span class="spin"></span> <span id="jLabel">Working…</span>';
  poll();
}

/* ── sidebar ── */
const NAV=[['usage','chart','Usage'],['searchp','search','Search'],['mcp','plug','MCP servers'],
  ['agents','robot','Agents'],['skills','ai','Skills'],['hooks','link','Hooks'],['accounts','group','Accounts'],
  ['settings','settings','Settings'],['helpp','help','Help']];
function drawNav(){
  $('#nav').innerHTML=NAV.map(([id,i,l])=>
    `<div class="it${PAGE_===id?' sel':''}" onclick="go('${id}')">${ic(i)} ${l}</div>`).join('');
}
function drawProjects(){
  const q=($('#q').value||'').toLowerCase();
  const box=$('#plist');box.innerHTML='';
  ST.projects.filter(p=>!q||p.name.toLowerCase().includes(q)||p.path.toLowerCase().includes(q))
    .forEach(p=>{
      const el=document.createElement('div');
      el.className='proj'+(CUR&&PAGE_==='project'&&CUR.encoded===p.encoded?' sel':'');
      const tags=p.accounts.length>1?' '+p.accounts.slice(1).map(a=>
        `<span class="tag" style="color:${acctColor(a)};border-color:currentColor;background:transparent">${esc(a)}</span>`).join(' '):'';
      const active=ACTIVE_MEM.has(p.path);
      const amk=(p.auto_memory||active)
        ?`<span class="amk${active?' spin-on':''}" title="${active?'memory updating now':'auto-memory on'}">${ic('refresh')}</span>`:'';
      const last=p.last_active?` <span style="opacity:.7">· ${esc(p.last_active)} ago</span>`:'';
      el.innerHTML=`<div class="nm">${esc(p.name)}${tags}${amk}</div><div class="pt">${esc(p.path)}${last}</div>`;
      el.onclick=()=>openProject(p);
      box.appendChild(el);
    });
}

/* ── router ── */
function go(page){PAGE_=page;CUR=page==='project'?CUR:null;
  applyTheme(ST.theme);   // drop any unsaved theme preview
  render();}
function render(){
  drawNav();drawProjects();
  $('#tabs').style.display=PAGE_==='project'?'flex':'none';
  $('#pactions').style.display=PAGE_==='project'?'':'none';
  if(PAGE_!=='project')stopMemBadge();   // hide the badge off a project page
  if(PAGE_==='home')drawHome();
  else if(PAGE_==='project')drawProject();
  else drawPage(PAGE_);
}

/* ── home: bento dashboard ── */
function fmtTok(n){
  if(n>=1e6)return (n/1e6).toFixed(1)+'M';
  if(n>=1e3)return (n/1e3).toFixed(1)+'k';
  return String(n);
}
function drawHome(){
  $('#ttl').textContent='Welcome';$('#tpath').textContent='';
  const rec=ST.recent||[],projs=ST.projects||[];
  $('#content').innerHTML=`<div class="bento">
    <div class="card t-continue">${continueTileHtml(rec)}</div>
    <div class="card t-usage">${ic('chart')} <b>Usage</b>
      <div class="tstat" id="tUsageStat">loading…</div></div>
    <div class="card t-projects">${projectsTileHtml(projs)}</div>
    <div class="card t-search">${ic('search')} <b>Search sessions</b>
      <div class="fld" style="margin-top:8px"><input id="hqSearch" placeholder="Type to jump to any session…"></div>
      <div id="hqRes"></div></div>
    <div class="card t-actions">
      <button class="btn" onclick="openProjectByPath()">${ic('folder')} Open project</button>
      <button class="btn" onclick="go('settings')">${ic('settings')} Settings</button>
      <button class="btn" onclick="go('accounts')">${ic('group')} Accounts</button>
    </div>
  </div>`;
  bindHomeSearch();
  loadHomeUsage();
}
function continueTileHtml(rec){
  if(!rec.length)return `<b>Welcome</b><p style="color:var(--dim);margin-top:8px">
    Open a project on the left to get started.</p>`;
  const r=rec[0];
  const rest=rec.slice(1).map((x,i)=>`
    <div class="hrow">
      <div class="info"><b>${esc(x.project)}</b> <span style="color:var(--dim)">${esc(x.name)}</span></div>
      <span style="color:var(--dim);font-size:12px">${x.age?esc(x.age)+' ago':''}</span>
      <button class="btn sm" onclick="homeResume(${i+1})">Resume</button>
    </div>`).join('');
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
    </div>
    ${rest?`<div class="lbl" style="margin-top:16px">Other recent sessions</div>${rest}`:''}`;
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
function projectsTileHtml(projs){
  const n=projs.length,mem=projs.filter(p=>p.auto_memory).length,latest=projs[0];
  return `<div class="lbl">Projects</div>
    <div class="tstat">${n} total <span style="color:var(--dim);font-size:12px;font-weight:400">· ${mem} with auto-memory</span></div>
    ${latest?`<div style="margin-top:10px;font-size:12px;color:var(--dim)">Most recent</div>
      <div class="hlink" onclick="openProject(ST.projects[0])">${esc(latest.name)}</div>`:''}`;
}
async function loadHomeUsage(){
  try{
    const [daily,projects]=await Promise.all([
      api('/api/usage/daily?days=7'),api('/api/usage/projects')]);
    const tok=(daily.days||[]).reduce((a,d)=>a+(d.tokens||0),0);
    const cost=(projects.projects||[]).reduce((a,p)=>a+(p.cost||0),0);
    const el=$('#tUsageStat');if(!el)return;
    el.innerHTML=`${fmtTok(tok)} tok <span style="color:var(--dim);font-size:12px;font-weight:400">· $${cost.toFixed(2)} · 7d</span>`;
  }catch(_e){const el=$('#tUsageStat');if(el)el.textContent='—';}
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
  const res=$('#hqRes');if(!res)return;
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
const TABS=[['sessions','Sessions'],['memory','Memory'],['claudemd','CLAUDE.md'],
  ['review','Review'],['audit','Audit'],['pusage','Usage'],['planexec','Plan → Execute'],['tools','Tools']];
async function openProject(p){CUR=p;PAGE_='project';TAB='sessions';REVIEW=null;render();
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
    let prog=null;
    try{const r=await api('/api/memory/progress?'+qs({path:CUR.path}));prog=r.progress;}catch(e){}
    if(prog!=null){_memSeen=true;setMemBadge(prog);_memTimer=setTimeout(tick,2000);}
    else if(!_memSeen&&_memGrace-->0){_memTimer=setTimeout(tick,2000);}  // worker not up yet
    else{ // finished (or never started) — refresh memory view if it's showing
      const wasRunning=_memSeen;stopMemBadge();
      if(wasRunning){toast('Memory updated','ok');
        if(PAGE_==='project'&&TAB==='memory')drawMemory();}
    }
  };
  tick();
}
function drawProject(){
  $('#ttl').textContent=CUR.name;$('#tpath').textContent=CUR.path;
  $('#tabs').innerHTML=TABS.map(([id,l])=>
    `<div class="tab${TAB===id?' sel':''}" onclick="TAB='${id}';drawProject()">${l}</div>`).join('')
    +`<div class="tab" onclick="window.open('/graph?${qs({path:CUR.path,enc:CUR.encoded})}','_blank')">Graph ${ic('ext')}</div>`;
  ({sessions:drawSessions,memory:drawMemory,claudemd:drawClaudeMd,review:drawReview,
    audit:drawAudit,pusage:drawProjUsage,planexec:drawPlanExec,tools:drawTools}[TAB])();
}

/* review tab — confidence-scored review of the working diff */
let REVIEW=null;
const SEVCLR={critical:'#ff5c5c',high:'#ff8a5c',medium:'#ffd166',low:'var(--dim)'};
function drawReview(){
  const r=REVIEW;
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
          <code style="color:var(--accent)">${esc(f.file||'?')}:${esc(String(f.line||'?'))}</code>
        </div>
        <div style="margin:6px 0 2px;font-weight:600">${esc(f.summary||'')}</div>
        <div style="color:var(--dim);font-size:13px">${esc(f.detail||'')}</div></div>`;
    }).join('');
  }
  $('#content').innerHTML=`<div class="card"><h3>Code review <span class="sp"></span>
      <button class="btn sm pri" onclick="runReview(false)">${ic('search')} Review working changes</button>
      <button class="btn sm" onclick="runReview(true)">Staged only</button></h3>
      <p style="color:var(--dim);font-size:12px;margin:0 0 10px">Inspired by the Claude Code code-review plugin — confidence-scored, high-threshold, CLAUDE.md-aware.</p>
      ${out}</div>`;
}
function runReview(staged){
  runJob('review',{...C(),staged},st=>{REVIEW=st.result||{findings:[]};if(TAB==='review')drawReview();});
}

/* sessions tab */
async function drawSessions(archived){
  $('#content').innerHTML='<div class="empty"><span class="spin"></span> Loading…</div>';
  const d=archived?await api('/api/session/archived?'+qs({enc:CUR.encoded,cfgdir:CUR.primary_cfgdir}))
                  :await api('/api/sessions?enc='+encodeURIComponent(CUR.encoded));
  SESS=d.sessions||[];
  const tagsD=await api('/api/session/tags?'+qs({enc:CUR.encoded,cfgdir:CUR.primary_cfgdir}));
  const tags=tagsD.tags||{};
  const hdr=`<div style="display:flex;gap:8px;margin-bottom:12px;align-items:center">
    <span class="lbl" style="margin:0">${archived?'Archived':'Sessions'} (${SESS.length})</span>
    <span style="flex:1"></span>
    <button class="btn sm" onclick="drawSessions(${archived?'false':'true'})">
      ${archived?'← Active sessions':ic('archive')+' Archived'}</button></div>`;
  if(!SESS.length){
    $('#content').innerHTML=hdr+`<div class="empty">${archived?'No archived sessions.':'No sessions yet — start one with New session.'}</div>`;
    return;}
  $('#content').innerHTML=hdr+'<div class="slist">'+SESS.map((s,i)=>{
    const tg=(tags[s.sid]||[]).map(t=>`<span class="tag ok">${esc(t)}</span>`).join(' ');
    return `<div class="sess">
      <span class="dot" style="background:${acctColor(s.account)}"
            title="${esc(s.account||'')}"></span>
      <div class="info">
        <div class="t" id="st${i}">${esc(s.title||s.preview||s.sid.slice(0,8))} ${tg}</div>
        <div class="meta"><span>${esc(s.age)} ago</span><span>${s.count} msgs</span>
          ${s.tokens?`<span>${esc(s.tokens)} tok</span>`:''}
          ${s.account&&s.account!=='default'?`<span class="tag" style="color:${acctColor(s.account)};border-color:currentColor;background:transparent">${esc(s.account)}</span>`:''}</div>
      </div>
      <div class="acts">${archived?`
        <button class="btn sm danger" onclick="deleteS(${i},true)" title="Delete">${ic('del')}</button>`:`
        <button class="btn sm" onclick="toggleTune(${i})" title="Adjust power before resuming">${ic('settings')}</button>
        <button class="btn sm" onclick="viewS(${i})" title="Transcript">${ic('doc')}</button>
        <button class="btn sm" onclick="exportS(${i})" title="Export markdown">${ic('download')}</button>
        <button class="btn sm" onclick="filesS(${i})" title="Changed files">${ic('folder')}</button>
        <button class="btn sm" onclick="tagS(${i})" title="Tags">${ic('label')}</button>
        <button class="btn sm" onclick="renameS(${i})">Rename</button>
        <button class="btn sm" onclick="archiveS(${i})">Archive</button>
        <button class="btn sm" onclick="forkS(${i})">Fork</button>`}
      </div>
      <button class="btn sm pri" onclick="${archived?`restoreS(${i})`:`resumeS(${i})`}">${archived?'Restore':'Resume'}</button>
      </div>${archived?'':`
      <div class="rowtune" id="rowtune-${i}" style="display:none">
        <input type="range" class="rtfrontier" min="0" step="1">
        <div class="frontends"><span>Cheap &amp; fast</span><span>Max power</span></div>
        <div class="frontread rtread"></div>
        <button class="btn sm pri" onclick="resumeTuned(${i})" style="margin-top:8px">Resume with these settings →</button>
      </div>`}`;}).join('')+'</div>';
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
  $('#dBody').innerHTML=
    `<div class="card"><h3>Session info</h3><div style="font:12px Consolas,monospace;white-space:pre-wrap">${esc((m.lines||[]).join('\n'))}</div></div>`
    +(t.messages||[]).map(x=>`<div class="msg ${x.role==='user'?'user':''}">
      <div class="who">${x.role==='user'?'User':'Assistant'}</div>
      <div class="body">${esc(x.text)}</div></div>`).join('');
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

/* memory tab */
async function drawMemory(){
  $('#content').innerHTML='<div class="empty"><span class="spin"></span> Loading…</div>';
  const c=C();
  const [st,les,ws,wl]=await Promise.all([
    api('/api/memory/state?'+qs(c)),api('/api/lessons?'+qs(c)),
    api('/api/workspace-status?'+qs(c)),api('/api/worklog?'+qs(c))]);
  const lesRows=(les.lessons||[]).map(l=>`
    <tr><td>${l.status==='pending'?'…':l.status==='pinned'?ic('pin'):ic('check')} ${esc(l.status)}</td>
    <td><b>${esc(l.name)}</b><div style="color:var(--dim);font-size:12px">${esc(l.summary)}</div></td>
    <td class="num">${(l.confidence||0).toFixed(1)}</td>
    <td style="white-space:nowrap">
      <button class="btn sm" onclick="lessonAct('${l.id}','approve')" title="Approve">${ic('check')}</button>
      <button class="btn sm" onclick="lessonAct('${l.id}','pin')" title="Pin">${ic('pin')}</button>
      <button class="btn sm danger" onclick="lessonAct('${l.id}','evict')" title="Evict">${ic('close')}</button></td></tr>`).join('');
  $('#content').innerHTML=`
    <div class="card"><h3>Project memory <span class="sp"></span>
      <button class="btn sm" onclick="askMem()">${ic('chat')} Ask</button>
      <button class="btn sm" onclick="recallPrev()">${ic('eye')} Recall preview</button>
      <button class="btn sm pri" onclick="buildMemory()">${ic('bolt')} Build with Claude</button></h3>
      <div class="kv">
        <span class="k">Entities</span><span>${st.n_entities||0}</span>
        <span class="k">Lessons</span><span>${st.n_lessons||0} (${st.n_pending||0} pending review)</span>
        <span class="k">Unscanned sessions</span><span>${st.n_unscanned||0}</span>
        <span class="k">Generated</span><span>${esc(st.generated_at||'never')}</span>
        <span class="k">Prompt hook</span><span>${st.hook_on?'on':'off'}</span>
        <span class="k">Path-scoped rules</span><span>${st.rules_on?'on':'off'}</span>
      </div>
      <label class="autoline" title="Refresh this project's memory in the background — on GUI start and periodically — whenever its files change, without needing this tab open.">
        <input type="checkbox" id="autoMem" ${CUR.auto_memory?'checked':''} onchange="toggleAutoMem(this.checked)">
        <span>${ic('refresh')} Keep this project's memory updated automatically</span>
        ${st.n_entities?'':'<span class="tag warn">build memory once first</span>'}</label>
      <div id="memProg"></div></div>
    <div class="card"><h3>Lessons <span class="sp"></span>
      <button class="btn sm" onclick="runJob('lessons_scan',C(),()=>drawMemory())">${ic('school')} Learn from sessions${st.n_unscanned?` <span class="tag warn">${st.n_unscanned} new</span>`:''}</button>
      <button class="btn sm" onclick="lessonAct('','approve_all')">${ic('check')} Approve all pending</button></h3>
      ${lesRows?`<table class="tbl"><tr><th>status</th><th>lesson</th><th>conf</th><th></th></tr>${lesRows}</table>`
        :'<div style="color:var(--dim)">No lessons yet.</div>'}</div>
    <div class="card"><h3>Recent work</h3>
      <p style="color:var(--dim);font-size:12px;margin:0 0 8px">A token-free log of what each session changed, injected into the next session on start (claude-mem style).</p>
      <label class="autoline" style="margin-top:0" title="On session end, record a one-line summary + files touched; inject the last few on the next SessionStart.">
        <input type="checkbox" id="wlOn" ${wl.on?'checked':''} onchange="toggleWorklog(this.checked)">
        <span>${ic('school')} Track recent work for this project</span></label>
      ${(wl.entries||[]).length?`<table class="tbl" style="margin-top:10px"><tr><th>when</th><th>summary</th><th>files</th></tr>`
        +wl.entries.map(e=>`<tr><td style="white-space:nowrap;color:var(--dim)">${esc(e.ended_at||'')}</td>
          <td>${esc(e.summary||'')}</td>
          <td style="color:var(--dim);font-size:12px">${esc((e.files||[]).join(', '))}</td></tr>`).join('')
        +`</table>`:'<div style="color:var(--dim);margin-top:8px">No sessions recorded yet.</div>'}</div>
    <div class="card"><h3>Workspace status — score ${ws.score??'?'} ${ws.safe?'<span class="tag ok">safe</span>':'<span class="tag warn">attention</span>'}</h3>
      <div style="font:12px Consolas,monospace;white-space:pre-wrap">${esc((ws.lines||[]).join('\n'))}</div></div>`;
}
async function toggleWorklog(on){
  await post('/api/worklog',{enc:CUR.encoded,on});
  toast(on?'Recent-work tracking on':'Recent-work tracking off','ok');
}
/* live progress of a running memory scan (fg job or bg worker) */
async function pollMemProg(){
  const el=$('#memProg');
  if(el&&PAGE_==='project'&&TAB==='memory'){
    try{
      const d=await api('/api/memory/progress?'+qs({path:CUR.path}));
      el.innerHTML=(d.progress===null||d.progress===undefined)?''
        :`<div style="display:flex;gap:8px;align-items:center;margin-top:10px;color:var(--warn)">
           <span class="spin"></span> Memory scan running… ${esc(d.progress||'')}</div>`;
    }catch(e){}
  }
  setTimeout(pollMemProg,2500);
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
  runJob('memory_build',C(),()=>drawMemory());
}
async function askMem(){
  const v=await ask('Ask project memory',[{label:'Question'}]);
  if(v===null||!v[0].trim())return;
  runJob('memory_ask',{...C(),question:v[0]},st=>{
    $('#dTitle').textContent='Memory answer';
    $('#dBody').innerHTML=`<div class="msg"><div class="who">Answer</div>
      <div class="body">${esc(st.result||'')}</div></div>`;
    $('#drawer').classList.add('show');});
}
async function recallPrev(){
  const v=await ask('Recall preview',[{label:'Simulated prompt',ph:'e.g. fix the launch bug'}]);
  if(v===null)return;
  const d=await api('/api/recall-preview?'+qs({...C(),q:v[0]}));
  $('#dTitle').textContent=`Recall preview (${d.tokens||0} tok)`;
  $('#dBody').innerHTML=d.empty?'<div class="empty">Nothing would be injected.</div>'
    :`<div class="msg"><div class="body">${esc(d.context)}</div></div>`;
  $('#drawer').classList.add('show');
}

/* CLAUDE.md tab */
async function drawClaudeMd(){
  $('#content').innerHTML='<div class="empty"><span class="spin"></span></div>';
  const c=C();
  const [md,mm]=await Promise.all([api('/api/claude-md?'+qs(c)),
                                   api('/api/memory-map?'+qs(c))]);
  $('#content').innerHTML=`
    <div class="card"><h3>CLAUDE.md <span class="sp"></span>
      <button class="btn sm" onclick="cmScaffold()">${ic('doc')} Scaffold</button>
      <button class="btn sm" onclick="runJob('ai_scaffold',C(),()=>drawClaudeMd())">${ic('ai')} AI analyze</button>
      <button class="btn sm" onclick="runJob('ai_compress',C(),()=>drawClaudeMd())">${ic('shrink')} AI compress</button>
      <button class="btn sm" onclick="cmPrune()">${ic('cut')} Prune</button>
      <button class="btn sm" onclick="post('/api/open-editor',{file:CUR.path+'\\\\CLAUDE.md'})">${ic('edit')} Edit</button></h3>
      ${md.exists?`<div style="font:12px Consolas,monospace;white-space:pre-wrap;max-height:46vh;overflow-y:auto;background:var(--code);border-radius:8px;padding:12px">${esc(md.text)}</div>`
        :'<div style="color:var(--dim)">No CLAUDE.md yet — scaffold one.</div>'}</div>
    <div class="card"><h3>Memory files map</h3>
      ${(mm.files||[]).map(f=>`<div style="display:flex;gap:8px;padding:3px 0;align-items:center">
        <span style="width:18px">${f.exists?ic('check'):'—'}</span>
        <span style="flex:1">${esc(f.label)}</span>
        <span style="color:var(--dim2);font-size:11px">${esc(f.path)}</span>
        ${f.exists?`<button class="btn sm" onclick="post('/api/open-editor',{file:'${esc(f.path).replace(/\\/g,'\\\\')}'})">open</button>`:''}
      </div>`).join('')}</div>
    <div class="card"><h3>System prompt</h3><div id="spBox"></div></div>`;
  const sp=await api('/api/system-prompt?'+qs(c));
  $('#spBox').innerHTML=`<div class="fld"><textarea id="spText">${esc(sp.text)}</textarea></div>
    <div class="mrow"><button class="btn pri sm" onclick="spSave()">Save</button></div>`;
}
async function cmScaffold(){
  await post('/api/claude-md/scaffold',C());toast('Scaffolded','ok');drawClaudeMd();}
async function cmPrune(){
  const r=await post('/api/ctxaudit/prune',C());
  toast(`Pruned: ~${r.old_tokens} → ~${r.new_tokens} tok`,'ok');drawClaudeMd();}
async function spSave(){
  await post('/api/system-prompt',{...C(),text:$('#spText').value});
  toast('System prompt saved','ok');}

/* audit tab */
async function drawAudit(){
  $('#content').innerHTML='<div class="empty"><span class="spin"></span></div>';
  const c=C();
  const [d,deny]=await Promise.all([api('/api/ctxaudit?'+qs(c)),api('/api/deny?'+qs(c))]);
  const rows=(d.items||[]).map(it=>`
    <tr><td>${esc(it.label)} ${it.lazy?'<span class="tag">lazy</span>':''}</td>
    <td class="num">${it.tokens}</td>
    <td style="color:var(--warn);font-size:12px">${esc((it.warnings||[]).join(' · '))}</td></tr>`).join('');
  $('#content').innerHTML=`
    <div class="card"><h3>Context weight — ~${d.total||0} tok loaded every turn
      <span class="sp"></span>
      <button class="btn sm" onclick="cmPrune().then(()=>drawAudit())">${ic('cut')} Prune sessions</button>
      <button class="btn sm" onclick="post('/api/ctxaudit/compact',{path:CUR.path}).then(()=>{toast('Compact section added','ok');drawAudit()})">${ic('add')} Compact instructions</button></h3>
      <table class="tbl"><tr><th>item</th><th>tokens</th><th>warnings</th></tr>${rows}</table></div>
    <div class="card"><h3>Deny rules (token-heavy paths) <span class="sp"></span>
      <button class="btn sm pri" onclick="post('/api/deny/apply',{path:CUR.path}).then(r=>{toast(r.added+' added, '+r.existed+' existed','ok');drawAudit()})">Apply all</button></h3>
      ${(deny.patterns||[]).map(p=>`<div style="display:flex;gap:10px;padding:2px 0">
        <code style="color:var(--cyan)">${esc(p.pattern)}</code>
        <span style="color:var(--dim);font-size:12px">${esc(p.why)}</span></div>`).join('')
      ||'<div style="color:var(--dim)">Nothing heavy found.</div>'}</div>`;
}

/* project usage tab */
async function drawProjUsage(){
  $('#content').innerHTML='<div class="empty"><span class="spin"></span> Crunching…</div>';
  const d=await api('/api/usage/project?'+qs(C()));
  const rows=(d.sessions||[]).map(r=>`
    <tr><td>${esc(r.age)}</td><td>${esc(r.name)} ${r.account&&r.account!=='default'?`<span class="tag">${esc(r.account)}</span>`:''}</td>
    <td class="num">${r.msgs}</td><td class="num">${r.usage.in}</td>
    <td class="num">${r.usage.out}</td><td class="num">${r.exact?'':'~'}$${r.cost.toFixed(2)}</td></tr>`).join('');
  $('#content').innerHTML=`<div class="card"><h3>Per-session usage</h3>
    <table class="tbl"><tr><th>age</th><th>session</th><th>msgs</th><th>in</th><th>out</th><th>est.$</th></tr>${rows}</table></div>`;
}

/* tools tab */
function drawTools(){
  $('#content').innerHTML=`
    <div class="card"><h3>${ic('inject')} New chat with injected context</h3>
      <p style="color:var(--dim);font-size:13px;margin-bottom:10px">Start a new session seeded with another session's transcript — from any account, into any account.</p>
      <button class="btn" onclick="injectFlow()">Choose source session…</button></div>
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
        <button class="btn pri sm" onclick="saveDirs()">Save</button></div></div>`;
  drawAgentPicker();
  api('/api/extra-paths?'+qs(C())).then(d=>dirRows('xpaths',d.paths||[]));
  api('/api/add-dirs?'+qs(C())).then(d=>dirRows('xdirs',d.dirs||[]));
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
async function injectFlow(){
  const d=await api('/api/inject/sessions?'+qs({path:CUR.path}));
  if(!(d.sessions||[]).length){toast('No sessions found for this project','err');return;}
  const v=await ask('Inject context',[
    {label:'Source session',type:'select',options:d.sessions.map((s,i)=>
      [String(i),`[${s.account}] ${s.title} (${s.age} ago)`])},
    {label:'Launch under account',type:'select',options:ST.accounts.map(a=>[a.dir,a.name])}]);
  if(v===null)return;
  const s=d.sessions[parseInt(v[0])];
  const r=await post('/api/inject/launch',{path:CUR.path,folder:s.folder,sid:s.sid,
    account:s.account,target_cfgdir:v[1]});
  toast(r.ok?'New session launched with injected context':'Failed: '+(r.error||''),r.ok?'ok':'err');
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
function drawPlanExec(){
  const o=ST.options;
  $('#content').innerHTML=`
    <div class="card"><h3>${ic('map')} Plan → Execute</h3>
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
      ${ST.accounts.length>1?`<div class="fld"><label>Execute under account</label><div class="chips" id="peAcct"></div></div>`:''}
      <div class="mrow"><button class="btn pri" onclick="peRun()">${ic('play')} Write the plan…</button></div></div>
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
      </ol></div>`;
  chipsFill($('#pePlan'),o.models,o.model_labels,ST.plan_model||'');
  chipsFill($('#peEff'),o.efforts,null,'xhigh');
  $('#peVia').querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
    $('#peVia').querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));
    c.classList.add('on');peViaChange();});
  $('#peCouncil').querySelectorAll('.chip').forEach(c=>c.onclick=()=>c.classList.toggle('on'));
  if(ST.accounts.length>1) chipsFill($('#peAcct'),ST.accounts.map(a=>a.dir),ST.accounts.map(a=>a.name),ST.active_cfgdir);
  peViaChange();
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
  const body={...C(),task,model:chipVal($('#pePlan')),effort:chipVal($('#peEff')),council,via};
  window._peTask=task;
  runJob('plan_make',body,st=>{
    const r=st.result;
    if(!r||!r.plan){toast('Plan came back empty','err');return;}
    peShowPlan(r.plan);
  });
}
function peShowPlan(plan){
  $('#pePlanEdit').value=plan;
  $('#peEditCard').style.display='';
  const via=chipVal($('#peVia'));
  const execEl=$('#peExec');
  const execModel=execEl?chipVal(execEl):'';
  const acctEl=$('#peAcct');
  const account=acctEl?chipVal(acctEl):'';
  window._peExecCfg={via,execModel,account};
  $('#peDiscardBtn').onclick=()=>{$('#peEditCard').style.display='none';};
  $('#peApproveBtn').onclick=()=>peExecute();
  $('#peEditCard').scrollIntoView({behavior:'smooth',block:'start'});
}
async function peReplan(){
  const task=window._peTask||($('#peTask').value||'').trim();
  if(!task){toast('No task to re-plan','err');return;}
  const curPlan=$('#pePlanEdit').value;
  const feedback=prompt('What to change about the plan? (feedback)');
  if(!feedback)return;
  const via=chipVal($('#peVia'));
  const body={...C(),task,feedback,plan_text:curPlan,
    model:chipVal($('#pePlan')),effort:chipVal($('#peEff')),council:false,via};
  $('#peEditCard').style.display='none';
  runJob('plan_replan',body,st=>{
    const r=st.result;
    if(!r||!r.plan){toast('Re-plan failed','err');return;}
    peShowPlan(r.plan);
    toast('Plan updated based on feedback','ok');
  });
}
async function peExecute(){
  const task=window._peTask||($('#peTask').value||'').trim();
  const plan=$('#pePlanEdit').value;
  const perStep=!!$('#pePerStep').checked;
  const cfg=window._peExecCfg||{};
  $('#peEditCard').style.display='none';
  runJob('plan_launch',{...C(),task,plan_text:plan,per_step:perStep,
    via:cfg.via,model:cfg.execModel,account:cfg.account},st=>{
    const r=st.result||{};
    toast(`Execute session launched — ${esc(r.model||'')} via ${r.via==='omniroute'?'OmniRoute':'Anthropic'}`,'ok');
  });
}
/* mirrors the TUI's category-grouped library multi-select */
async function drawAgentPicker(){
  const [lib,cur]=await Promise.all([
    api('/api/agents/library?'+qs({path:CUR.path})),
    api('/api/agents/session?'+qs(C()))]);
  const on=new Set(cur.refs||[]);
  window._agLimit=cur.limit||10;
  const chip=(ref,label,desc)=>`<span class="chip${on.has(ref)?' on':''}" data-ref="${esc(ref)}"
    title="${esc(desc||'')}" onclick="this.classList.toggle('on');agCount()">${esc(label)}</span>`;
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
    +(cats||'<div style="color:var(--dim)">Agent library is empty.</div>')
    +`<div class="mrow" style="align-items:center">
      <span id="agTot" style="flex:1;color:var(--dim);font-size:12px"></span>
      <button class="btn sm" onclick="agClear()">Clear all</button>
      <button class="btn sm pri" onclick="applyAgents()">Apply to project</button></div>`;
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
async function applyAgents(){
  const r=await post('/api/agents/session',{...C(),refs:agRefs()});
  toast(r.active+' agent(s) active','ok');
}

/* ── manager pages ── */
async function drawPage(id){
  $('#ttl').textContent=({usage:'Usage',searchp:'Search all sessions',mcp:'MCP servers',
    agents:'Agents',skills:'Skills',hooks:'Hooks',accounts:'Accounts',settings:'Settings',helpp:'Help'})[id]||id;
  $('#tpath').textContent='';
  $('#content').innerHTML='<div class="empty"><span class="spin"></span> Loading…</div>';
  await ({usage:pgUsage,searchp:pgSearch,mcp:pgMcp,agents:pgAgents,skills:pgSkills,
          hooks:pgHooks,accounts:pgAccounts,settings:pgSettings,helpp:pgHelp}[id])();
}

async function pgUsage(){
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
  $('#content').innerHTML=`
    <div class="card"><h3>Plan usage by account</h3>${planRows||'<div style="color:var(--dim)">checking…</div>'}</div>
    <div class="card"><h3>Daily tokens (14 days)</h3>${dRows}</div>
    <div class="card"><h3>Per-project — total est. $${total.toFixed(2)}</h3>
      <table class="tbl"><tr><th>project</th><th>sess</th><th>msgs</th><th>in</th><th>out</th><th>est.$</th></tr>${pRows}</table></div>`;
}

let SIDX=null;
async function pgSearch(){
  $('#content').innerHTML=`<div class="card"><h3>${ic('search')} Search every session</h3>
    <div class="fld"><input id="gq" placeholder="Type to search names, titles, previews…"></div>
    <div id="gres" style="margin-top:10px"></div></div>`;
  if(!SIDX){const d=await api('/api/search-index');SIDX=d.rows||[];}
  const draw=()=>{
    const q=($('#gq').value||'').toLowerCase().trim();
    const m=q?SIDX.filter(r=>q.split(/\s+/).every(w=>r.haystack.includes(w))):SIDX;
    $('#gres').innerHTML=m.slice(0,80).map((r,i)=>`
      <div class="qcard" style="margin-bottom:6px" onclick='gResume(${JSON.stringify(i)})'>
        <div class="info"><div class="t">${esc(r.display)}</div>
        <div class="s">${esc(r.project)} · ${esc(r.age)} ago</div></div>
        <button class="btn sm">Resume</button></div>`).join('')
      +(m.length>80?`<div style="color:var(--dim);padding:6px">…${m.length-80} more — refine your search</div>`:'');
    window._gmatch=m;
  };
  if(PENDING_SEARCH_Q){$('#gq').value=PENDING_SEARCH_Q;PENDING_SEARCH_Q='';}
  $('#gq').oninput=draw;draw();$('#gq').focus();
}
function gResume(i){const r=window._gmatch[i];
  askLaunch({title:'Resume — '+r.display,sub:r.project,isNew:false,
    path:r.path,enc:r.enc,choice:'resume:'+r.sid,cfgdir:r.cfgdir});}

async function pgMcp(){
  const d=await api('/api/mcp');
  $('#content').innerHTML=`<div class="card"><h3>MCP servers <span class="sp"></span>
    <button class="btn sm" onclick="mcpAdd()">${ic('add')} Add server</button></h3>
    ${(d.servers||[]).map(s=>`<div style="display:flex;align-items:center;gap:10px;padding:5px 0">
      <span style="color:${s.status==='ok'?'var(--ok)':'var(--warn)'}">${ic(s.status==='ok'?'check':'help')}</span><b style="flex:1">${esc(s.name)}</b>
      <button class="btn sm" onclick="runJob('mcp_analyze',{name:'${esc(s.name)}'},()=>toast('Tool docs written to global CLAUDE.md','ok'))">${ic('search')} Analyze tools</button>
      <button class="btn sm danger" onclick="mcpRemove('${esc(s.name)}')">Remove</button>
    </div>`).join('')||'<div style="color:var(--dim)">No MCP servers configured.</div>'}</div>`;
}
async function mcpAdd(){
  const v=await ask('Add MCP server',[
    {label:'Name'},{label:'Transport',type:'select',
     options:[['stdio','stdio (command)'],['sse','sse (url)'],['http','http (url)']]},
    {label:'Command or URL'},
    {label:'Scope',type:'select',options:[['local','local'],['user','user'],['project','project']]}]);
  if(v===null)return;
  const body={name:v[0],transport:v[1],scope:v[3]};
  if(v[1]==='stdio')body.command=v[2];else body.url=v[2];
  const r=await post('/api/mcp/add',body);
  toast(r.ok?'Added':'Failed: '+(r.error||''),r.ok?'ok':'err');drawPage('mcp');
}
async function mcpRemove(name){
  if(!await confirmBox(`Remove MCP server '${name}'?`))return;
  const r=await post('/api/mcp/remove',{name});
  toast(r.ok?'Removed':'Failed: '+(r.error||''),r.ok?'ok':'err');drawPage('mcp');
}

async function pgAgents(){
  const d=await api('/api/agents/library');
  const own=(d.own||[]).map(a=>`
    <div style="display:flex;align-items:center;gap:10px;padding:4px 0">
      <b style="min-width:160px">${esc(a.name)}</b>
      <span class="tag">${esc(a.scope)}</span>
      <span style="flex:1;color:var(--dim);font-size:12px">${esc(a.desc)}</span>
      <button class="btn sm" onclick='agView(${JSON.stringify(a.path)})'>view</button>
      <button class="btn sm" onclick='post("/api/open-editor",{file:${JSON.stringify(a.path)}})'>edit</button>
      <button class="btn sm danger" onclick='agDel(${JSON.stringify(a.path)})'>${ic('del')}</button></div>`).join('');
  const lib=(d.categories||[]).map(c=>`
    <details style="margin-bottom:6px"><summary style="cursor:pointer;font-weight:600">${esc(c.category)} (${c.agents.length})</summary>
    ${c.agents.map(a=>`<div style="display:flex;gap:10px;padding:3px 0 3px 16px;align-items:center">
      <b style="min-width:170px">${esc(a.name)}</b>
      <span style="flex:1;color:var(--dim);font-size:12px">${esc(a.desc)}</span>
      <button class="btn sm" onclick='agView(${JSON.stringify(a.path)})'>view</button></div>`).join('')}
    </details>`).join('');
  $('#content').innerHTML=`
    <div class="card"><h3>My agents <span class="sp"></span>
      <button class="btn sm" onclick="agNew()">${ic('add')} New agent</button>
      <button class="btn sm" onclick="agAI()">${ic('ai')} AI-generate</button></h3>
      ${own||'<div style="color:var(--dim)">No user/project agents yet.</div>'}</div>
    <div class="card"><h3>Agent library</h3>${lib||'<div style="color:var(--dim)">Library is empty.</div>'}</div>`;
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
  const v=await ask('New agent',[{label:'Name'},{label:'Description'},
    {label:'Scope',type:'select',options:[['user','user (all projects)'],['project','this project']]},
    {label:'System prompt / instructions',type:'textarea'}]);
  if(v===null||!v[0].trim())return;
  const r=await post('/api/agents/create',{name:v[0],description:v[1],
    scope:v[2],path:CUR?CUR.path:'',body:v[3]});
  toast(r.ok?'Agent created':'Failed','ok');drawPage('agents');
}
async function agAI(){
  const v=await ask('AI-generate agent',[{label:'Describe what the agent should do',type:'textarea'}]);
  if(v===null||!v[0].trim())return;
  runJob('agent_ai',{path:CUR?CUR.path:'',description:v[0]},()=>drawPage('agents'));
}
async function agDel(path){
  if(!await confirmBox('Delete this agent?'))return;
  const r=await post('/api/agents/delete',{file:path});
  toast(r.ok?'Deleted':'Failed: '+(r.error||''),r.ok?'ok':'err');drawPage('agents');
}

/* ── Skills ── */
async function pgSkills(){
  const path=CUR?CUR.path:'';
  const d=await api('/api/skills'+(path?'?'+qs({path}):''));
  const proj=(d.project||[]).map(s=>`
    <div style="display:flex;align-items:center;gap:10px;padding:4px 0">
      <b style="min-width:170px">${esc(s.name)}</b>
      <span style="flex:1;color:var(--dim);font-size:12px">${esc(s.desc)}</span>
      <button class="btn sm" onclick='skView(${JSON.stringify(s.dir)})'>view</button>
      <button class="btn sm" onclick='post("/api/open-editor",{file:${JSON.stringify(s.dir+"\\\\SKILL.md")}})'>edit</button>
      <button class="btn sm danger" onclick='skRemove(${JSON.stringify(s.dir)})'>${ic('del')}</button></div>`).join('');
  const tmpl=(d.templates||[]).map(s=>`
    <div style="display:flex;align-items:center;gap:10px;padding:4px 0">
      <b style="min-width:170px">${esc(s.name)}</b>
      <span class="tag">${esc(s.source)}</span>
      <span style="flex:1;color:var(--dim);font-size:12px">${esc(s.desc)}</span>
      <button class="btn sm" onclick='skView(${JSON.stringify(s.dir)})'>view</button>
      ${path?`<button class="btn sm pri" onclick='skInstall(${JSON.stringify(s.dir)})'>install</button>`:''}</div>`).join('');
  $('#content').innerHTML=`
    <div class="card"><h3>Project skills <span class="sp"></span>
      <button class="btn sm" onclick="skNew()">${ic('add')} New skill</button>
      <button class="btn sm" onclick="skAI()">${ic('ai')} AI-generate</button></h3>
      <p style="color:var(--dim);font-size:12px;margin:0 0 8px">${path?`Installed in <code>${esc(path)}\\.claude\\skills</code> — Claude loads each on demand.`:'Open a project to install skills into it.'}</p>
      ${proj||'<div style="color:var(--dim)">No project skills yet — install a template below.</div>'}</div>
    <div class="card"><h3>Starter templates &amp; library</h3>
      <p style="color:var(--dim);font-size:12px;margin:0 0 8px">Bundled starters (credited in the README) plus your saved skills.</p>
      ${tmpl||'<div style="color:var(--dim)">No templates found.</div>'}</div>
    <div class="card"><h3>${ic('download')} Install from GitHub</h3>
      <p style="color:var(--dim);font-size:13px;margin-bottom:10px">Clone a skill+agents bundle straight from its repo — e.g.
        <a href="https://github.com/olsenbrands/fable-foreman" target="_blank" rel="noopener"
           style="color:var(--cyan);text-decoration:none">fable-foreman</a> (MIT, Jordan Olsen), which delegates
        execution to worker/verifier subagents. If a free execute model is set under Settings, its agents'
        <code>model:</code> pin is rewritten to that model automatically.</p>
      <div class="fld"><input id="skGitUrl" value="https://github.com/olsenbrands/fable-foreman"></div>
      <div class="mrow"><button class="btn pri sm" onclick="skGitInstall()">${ic('download')} Clone &amp; install</button></div></div>`;
}
function skGitInstall(){
  const url=($('#skGitUrl').value||'').trim();
  if(!url){toast('Enter a git URL','err');return;}
  runJob('skill_git_install',{path:CUR?CUR.path:'',url},st=>{
    toast((st.result&&st.result.message)||'Installed','ok');drawPage('skills');
  });
}
async function skView(dir){
  const d=await api('/api/skills/read?dir='+encodeURIComponent(dir));
  $('#dTitle').textContent=d.meta&&d.meta.name||'Skill';
  $('#dBody').innerHTML=`<div class="card"><div class="kv">
    <span class="k">Tools</span><span>${esc(d.meta['allowed-tools']||'(all)')}</span></div></div>
    <div class="msg"><div class="body">${esc(d.body)}</div></div>`;
  $('#drawer').classList.add('show');
}
async function skInstall(dir){
  const r=await post('/api/skills/install',{dir,path:CUR?CUR.path:''});
  toast(r.ok?'Installed into project':'Install failed',r.ok?'ok':'err');drawPage('skills');
}
async function skRemove(dir){
  if(!await confirmBox('Remove this skill from the project?'))return;
  const r=await post('/api/skills/remove',{dir});
  toast(r.ok?'Removed':'Failed',r.ok?'ok':'err');drawPage('skills');
}
async function skNew(){
  const v=await ask('New skill',[{label:'Name (e.g. commit-message)'},
    {label:'Description — when should Claude use it?'},
    {label:'Instructions (markdown)',type:'textarea'}]);
  if(v===null||!v[0].trim())return;
  const r=await post('/api/skills/create',{name:v[0],description:v[1],
    body:v[2],path:CUR?CUR.path:''});
  toast(r.ok?'Skill created':'Failed',r.ok?'ok':'err');drawPage('skills');
}
async function skAI(){
  const v=await ask('AI-generate skill',[{label:'Name'},
    {label:'Describe what the skill should do',type:'textarea'}]);
  if(v===null||!v[0].trim())return;
  runJob('skill_ai',{path:CUR?CUR.path:'',name:v[0],description:v[1]},()=>drawPage('skills'));
}

async function pgHooks(){
  const d=await api('/api/hooks');
  const active=(d.hooks||[]).map(h=>`
    <div style="display:flex;align-items:center;gap:10px;padding:4px 0">
      <span class="tag">${esc(h.event)}</span>
      <span style="flex:1">${esc(h.label)}</span>
      ${h.matcher?`<code style="color:var(--dim2);font-size:11px">${esc(h.matcher)}</code>`:''}
      <button class="btn sm danger" onclick='hookRm(${JSON.stringify(h.event)},${h.index})'>${ic('del')}</button></div>`).join('');
  const tmpl=(d.templates||[]).map(t=>`
    <div style="display:flex;align-items:center;gap:10px;padding:4px 0">
      <b style="min-width:170px">${esc(t.key)}</b>
      <span style="flex:1;color:var(--dim);font-size:12px">${esc(t.desc)}</span>
      ${t.installed?`<span class="tag ok">${ic('check')} installed</span>`
        :`<button class="btn sm" onclick='hookAdd(${JSON.stringify(t.key)})'>${ic('add')} Install</button>`}</div>`).join('');
  $('#content').innerHTML=`
    <div class="card"><h3>Active hooks <span class="sp"></span>
      <button class="btn sm" onclick="hookPurge()">${ic('del')} Purge broken</button>
      <button class="btn sm" onclick="hookAI()">${ic('ai')} AI-generate</button></h3>
      ${active||'<div style="color:var(--dim)">No hooks installed.</div>'}</div>
    <div class="card"><h3>Templates</h3>${tmpl}</div>`;
}
async function hookAdd(key){const r=await post('/api/hooks/template',{key});
  toast(r.ok?'Hook installed':'Failed','ok');drawPage('hooks');}
async function hookRm(event,index){
  if(!await confirmBox('Remove this hook?'))return;
  await post('/api/hooks/remove',{event,index});toast('Removed','ok');drawPage('hooks');}
async function hookPurge(){const r=await post('/api/hooks/purge',{});
  toast(`Purged ${r.removed} broken hook(s)`,'ok');drawPage('hooks');}
async function hookAI(){
  const v=await ask('AI-generate hook',[{label:'What should the hook do?',type:'textarea'}]);
  if(v===null||!v[0].trim())return;
  runJob('hook_ai',{description:v[0]},()=>drawPage('hooks'));
}

async function pgAccounts(){
  const d=await api('/api/accounts');
  $('#content').innerHTML=`<div class="card"><h3>Claude accounts <span class="sp"></span>
    <button class="btn sm" onclick="acctAdd()">${ic('add')} Add account</button></h3>
    ${(d.accounts||[]).map(a=>`
      <div style="display:flex;align-items:center;gap:10px;padding:6px 0">
        <span style="color:${a.active?'var(--ok)':'var(--dim2)'}">●</span>
        <b style="min-width:110px">${esc(a.name)}</b>
        ${a.active?'<span class="tag ok">active</span>':''}
        <span style="flex:1;color:var(--dim2);font-size:12px">${esc(a.resolved)}</span>
        <button class="btn sm" onclick='acctAct("switch",${JSON.stringify(a.name)})'>Switch</button>
        <button class="btn sm" onclick='acctTerm(${JSON.stringify(a.name)},${JSON.stringify(a.dir)})'>Open terminal</button>
        ${a.name!=='default'?`
          <button class="btn sm" onclick='acctRename(${JSON.stringify(a.name)})'>Rename</button>
          <button class="btn sm danger" onclick='acctAct("remove",${JSON.stringify(a.name)})'>${ic('del')}</button>`:''}
      </div>`).join('')}</div>`;
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

async function pgHelp(){
  const row=(where,what)=>`<tr><td style="white-space:nowrap;color:var(--cyan)">${where}</td><td>${what}</td></tr>`;
  $('#content').innerHTML=`
    <div class="card"><h3>${ic('help')} Where everything lives</h3>
    <table class="tbl"><tr><th>place</th><th>what you can do</th></tr>
    ${row('Sidebar','Filter and open projects; global pages below; TUI/GUI default toggle')}
    ${row('Top bar','Terminal · Continue latest · New session (effort, model, permission, account, thinking cap, subagent model, name, worktree)')}
    ${row('Usage banner','Live per-account plan usage; refresh button re-fetches now, auto-updates every minute')}
    ${row('Project → Sessions','Resume, fork, rename, tag, archive/restore, delete, export markdown, transcript + session info (tokens/cost), changed files')}
    ${row('Project → Memory','Build memory with Claude, ask memory, recall preview, lessons review (approve/pin/evict), learn from sessions, workspace status, live scan progress')}
    ${row('Project → CLAUDE.md','View, scaffold, AI analyze, AI compress, prune, edit in editor, memory files map, system prompt')}
    ${row('Project → Audit','Context weight audit (token cost per item), prune sessions, compact instructions, deny rules')}
    ${row('Project → Usage','Per-session token/cost stats for this project')}
    ${row('Project → Tools','Inject context from another session/account, plan with one model → execute with another, project agents, extra PATH entries, add directories')}
    ${row('Project → Graph','Interactive architecture graph (opens in browser)')}
    ${row('Usage page','Plan usage by account, daily tokens, per-project costs')}
    ${row('Search page','Search every session across all projects and accounts')}
    ${row('MCP / Agents / Hooks','Managers: add/remove servers + AI tool analysis, create/AI-generate agents, install/AI-generate hooks')}
    ${row('Accounts page','Add/rename/remove/switch accounts, open terminal under an account')}
    ${row('Settings','Launch defaults, GUI window shell, theme')}
    </table></div>`;
}

async function pgSettings(){
  const o=ST.options;
  $('#content').innerHTML=`<div class="card"><h3>Defaults</h3>
    ${fld('sEff','Effort')}${fld('sMod','Model')}${fld('sPerm','Permission mode')}
    ${fld('sThink','Thinking cap')}${fld('sSub','Subagent model')}
    ${fld('sShell','GUI window')}${fld('sTheme','Theme')}
    <div class="mrow"><button class="btn pri" onclick="setSave()">Save</button></div></div>
  <div class="card"><h3>Economy model</h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">Model used for claudectl's <b>own</b> internal Claude calls — memory extraction, lessons, CLAUDE.md / agent / hook / skill generation. Defaults to Haiku to cut cost. Your actual coding sessions are unaffected. <i>default</i> = your account's model.</p>
    ${fld('sExtract','Economy model')}
    <div class="mrow"><button class="btn pri" onclick="setExtractSave()">Save</button></div></div>
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
      <button class="btn sm" onclick="orDashboard()">${ic('ext')} Open OmniRoute dashboard</button>
      <span class="sp"></span>
      <button class="btn pri" onclick="orSave()">Save</button></div></div>
  <div class="card"><h3>Interface</h3>
    <p style="color:var(--dim);font-size:13px">Default interface on startup — the toggle in the bottom-left does the same. <code>--tui</code>/<code>--gui</code> flags always override.</p></div>
  <div class="card"><h3>${ic('refresh')} Auto-memory <span class="sp"></span>
    <span class="fld" style="margin:0"><select id="amInt" onchange="amSaveInterval(this.value)" style="width:auto"></select></span></h3>
    <p style="color:var(--dim);font-size:13px;margin-bottom:8px">Projects checked below have their memory refreshed in the background — on GUI start and on the interval — whenever their files change. Only changed projects use Claude; nothing runs while unchanged.</p>
    <div id="amList"><span class="spin"></span></div></div>`;
  chipsFill($('#sEff'),o.efforts,null,ST.defaults.effort);
  chipsFill($('#sMod'),o.models,o.model_labels,ST.defaults.model);
  chipsFill($('#sPerm'),o.perms,o.perm_labels,ST.defaults.perm);
  chipsFill($('#sThink'),o.thinking,o.thinking_labels,ST.defaults.max_thinking);
  chipsFill($('#sSub'),o.models,o.model_labels,ST.defaults.subagent_model);
  chipsFill($('#sExtract'),o.models,o.model_labels,ST.extract_model||'');
  chipsFill($('#sShell'),['auto','qt','edge','browser'],
    ['auto (Qt → Edge → browser)','Qt native window','Edge app window','browser tab'],
    ST.gui_shell||'auto');
  // theme swatches with live preview
  const themeNames=Object.keys(ST.themes||{});
  const curTheme=ST.theme||'default';
  $('#sTheme').innerHTML=themeNames.map(n=>{
    const t=ST.themes[n];
    const bg=t?t.panel||t.bg||'#1a1b26':'#1a1b26';
    const accent=t?t.accent||'#7dcfff':'#7dcfff';
    return `<span class="chip${n===curTheme?' on':''}" data-v="${esc(n)}"
      style="display:inline-flex;align-items:center;gap:6px">
      <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
        background:${accent};border:1.5px solid var(--line);flex:none"></span>
      ${esc(n)}</span>`;
  }).join('');
  $('#sTheme').querySelectorAll('.chip').forEach(c=>
    c.addEventListener('click',()=>{
      $('#sTheme').querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));
      c.classList.add('on');applyTheme(c.dataset.v);
    }));
  chipsFill($('#sPlanMod'),o.models,o.model_labels,ST.plan_model||'');
  chipsFill($('#sExecMod'),o.models,o.model_labels,ST.exec_model||'');
  $('#orUrl').value=ST.omniroute_base_url||'';
  drawAutoMemList();
  orRefresh();
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
  // OmniRoute's own per-connection test can be flat-out wrong (confirmed:
  // reported working no-auth connections as "error"/"not supported") --
  // a listed connection is a real fact (it's configured); whether it WORKS
  // is only trustworthy from "Send a live test" below. So the connection
  // count drives the dot (neutral 'ok' once >0), never the self-check flag.
  const nConn=(st.connections||[]).length;
  dot.textContent=nConn>0?`${nConn} provider(s) connected`:'0 providers connected';
  dot.className='tag '+(nConn>0?'ok':'warn');
  if(warn)warn.style.display=nConn>0?'none':'';
  if(conns)conns.innerHTML=(st.connections||[]).map(c=>
    `<div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12.5px">
      <span class="tag ok">connected</span>
      <b>${esc(c.name||c.provider)}</b>
      <span style="color:var(--dim)">${esc(c.provider)}</span>
      <span class="sp"></span>
      <button class="btn sm" onclick="orTestConn('${esc(c.id)}')" title="OmniRoute's own self-check — can be wrong; use 'Send a live test' below for the real answer">${ic('refresh')} Self-check</button></div>`
  ).join('')||'<div style="color:var(--dim);font-size:12.5px">No providers connected yet.</div>';
  const m=await api('/api/omniroute/models');
  const wrap=$('#orModWrap');if(!wrap)return;
  const cur=ST.omniroute_exec_model||OR_AUTO;
  wrap.innerHTML=`<div class="fld"><label>Execute model</label>
    <div class="chips" id="sOrAuto"></div>
    <details style="margin-top:8px">
      <summary style="cursor:pointer;color:var(--dim);font-size:12px">Pin a specific model instead (${m.models.length} available)</summary>
      <div class="chips" id="sOrPin" style="margin-top:6px;max-height:260px;overflow-y:auto"></div>
    </details></div>`;
  chipsFill($('#sOrAuto'),[OR_AUTO],['Auto — best free model, auto-fallback'],cur===OR_AUTO?OR_AUTO:'');
  chipsFill($('#sOrPin'),m.models,m.models.map(id=>m.labels[id]||id),cur!==OR_AUTO?cur:'');
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
const AM_INTERVALS=[[900,'every 15 min'],[1800,'every 30 min'],[3600,'every 60 min'],
  [7200,'every 2 hours'],[21600,'every 6 hours']];
async function drawAutoMemList(){
  const d=await api('/api/memory/auto');
  const sel=$('#amInt');
  if(sel)sel.innerHTML=AM_INTERVALS.map(([v,l])=>
    `<option value="${v}"${v===d.interval?' selected':''}>${l}</option>`).join('');
  const rows=(d.projects||[]).sort((a,b)=>(b.auto-a.auto)).map(p=>`
    <label class="amrow">
      <input type="checkbox" ${p.auto?'checked':''} onchange="amToggle('${esc(p.enc)}',this.checked)">
      <span class="nm">${esc(p.name)}${p.running?' <span class="tag ok">updating…</span>':''}</span>
      <span class="pt">${esc(p.path)}</span></label>`).join('');
  $('#amList').innerHTML=rows||'<div style="color:var(--dim)">No projects found.</div>';
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
async function setExtractSave(){
  await post('/api/settings',{extract_model:chipVal($('#sExtract'))});
  ST.extract_model=chipVal($('#sExtract'));toast('Economy model saved','ok');
}

/* ── launch modal (chips, not <select> — native dropdowns flicker under
      QtWebEngine) ── */
function chipsFill(el,vals,labels,cur){
  el.innerHTML=vals.map((v,i)=>
    `<span class="chip${v===cur?' on':''}" data-v="${esc(v)}">${esc(labels?labels[i]:(v||'default'))}</span>`).join('');
  el.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
    el.querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));
    c.classList.add('on');});
}
function chipVal(el){const c=el.querySelector('.chip.on');return c?c.dataset.v:'';}
function chipSet(el,v){el.querySelectorAll('.chip').forEach(c=>
  c.classList.toggle('on',c.dataset.v===v));}
// model cards: grid-aligned rows with SWE% / cost / capability / best-for
function modelCardsFill(el,cur){
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
  const lvl=a[0],msg=a[1];
  $('#mHint').className='mhint adv-'+lvl;
  $('#mHint').innerHTML=(lvl==='warn'?'note: ':lvl==='tip'?'tip: ':'')+esc(msg)
    +`  <a id="mGuide">model guide ›</a>`;
  $('#mGuide').onclick=openGuide;
  const role=(ST.options.effort_profiles||{})[e]||'';
  $('#fEffLabel').textContent=(e||'default')+(role?' · '+role:'');
  updateFrontierReadout();
  markPreset();
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
  sl.oninput=()=>{updateHint();};
  const fsl=$('#fFrontier');fsl.max=Math.max((o.frontier||[1]).length-1,0);
  const fi=frontierIndexFor(curModel,curEffort);
  fsl.value=fi>=0?fi:Math.min(2,(o.frontier||[1]).length-1);
  fsl.oninput=()=>{setPinMode(false);updateHint();};
  $('#fPinModel').onchange=()=>{setPinMode($('#fPinModel').checked);updateHint();};
  // an explicit saved default that isn't one of the frontier stops → respect
  // it by opening pinned to the exact combo, rather than silently rounding
  setPinMode(fi<0&&!!(curModel||curEffort));
  chipsFill($('#fPerm'),o.perms,o.perm_labels,d.perm);
  chipsFill($('#fThink'),o.thinking,o.thinking_labels,d.max_thinking);
  chipsFill($('#fSub'),o.models,o.model_labels,d.subagent_model);
  chipsFill($('#fWt'),['','*'],['off','auto'],'');
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
    cfgdir:c.isNew&&ST.accounts.length>1?chipVal($('#fAcct')):(c.cfgdir||'')};
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
    const openModals=['#ovl','#govl','#oovl','#jovl','#povl'].find(id=>$(id)&&$(id).classList.contains('show'));
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
let _uTries=0,_uTimer=null;
async function drawUsageBar(force){
  clearTimeout(_uTimer);
  if(force)$('#uRefresh').innerHTML='<span class="spin"></span>';
  try{
    const d=await api('/api/usage/plan'+(force?'?refresh=1':''));
    const rows=(d.accounts||[]).filter(a=>(a.windows||[]).length);
    $('#ubar').innerHTML=`<div class="urow">
      <button class="btn sm" id="uRefresh" title="Refresh usage now"
        onclick="drawUsageBar(true)">${ic('refresh')}</button>
      <div style="flex:1;display:flex;flex-direction:column;gap:2px">
      ${rows.map(a=>`<div class="urow">
        <span class="uacct" title="${esc(a.email||a.account)}">${esc(a.email||a.account)}</span>
        ${a.windows.map(w=>`<span class="uwin${w.pct>=80?' hot':''}">
          <span class="ulbl">${esc(w.label)}</span>
          <span class="ubarm"><i style="width:${Math.min(100,w.pct)}%"></i></span>
          <span class="upct">${Math.round(w.pct)}%</span>
          ${w.resets?`<span class="urst">→ ${esc(w.resets)}</span>`:''}
        </span>`).join('')}</div>`).join('')
        ||'<span style="color:var(--dim2)">usage: no data yet</span>'}
      </div></div>`;
    // background fetch may not have data yet — retry briefly, then settle
    if(!rows.length&&_uTries++<10)_uTimer=setTimeout(drawUsageBar,3000);
    else _uTimer=setTimeout(drawUsageBar,60000);
  }catch(e){_uTimer=setTimeout(drawUsageBar,60000);}
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
  setTimeout(pollActiveMem,4000);
}

(async()=>{
  ST=await api('/api/state');
  // restore saved theme/account from localStorage
  const lsTheme=localStorage.getItem('ctl_theme');
  const lsAcct=localStorage.getItem('ctl_account');
  if(lsTheme&&ST.themes&&ST.themes[lsTheme])ST.theme=lsTheme;
  if(lsAcct)ST.active_cfgdir=lsAcct;
  applyTheme(ST.theme);segDraw();
  $('#bTerm').innerHTML=ic('terminal')+' Terminal';
  $('#bCont').innerHTML=ic('history')+' Continue';
  $('#bNew').innerHTML=ic('add')+' New session';
  render();drawUsageBar();pollMemProg();pollActiveMem();
})();
