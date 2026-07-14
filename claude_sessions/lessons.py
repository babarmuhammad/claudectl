"""Session learning — distill lessons from finished session transcripts into
the semantic memory graph.

Post-session scan (not a Stop hook: claudectl owns the lifecycle and the TUI
is the review point). One `claude -p` call per session extracts error→fix
pairs, decisions, corrected assumptions, and preferences as type='lesson'
entities with a pending → approved/pinned lifecycle. Pending lessons never
leave the TUI; only approved/pinned are injectable by recall. Unpinned lessons
decay after `memory_lessons_ttl` sessions unused.
"""

import os
import time
import json

from . import memory
from . import config as _c

TAIL_MSGS = 30
TAIL_CHARS = 30000
MAX_LESSONS = 8
MIN_AGE_SEC = 60          # session considered ended
JACCARD_MERGE = 0.6


# ── discovery ────────────────────────────────────────────────

def _all_folders(proj_folder):
    """proj_folder plus this same project's session folder under every OTHER
    account, so lessons are learned from every account's sessions."""
    from .sessions import project_session_folders
    return project_session_folders(proj_folder)


def _sid_path(proj_folder, sid):
    """Resolve a sid to the account folder its transcript actually lives in."""
    for f in _all_folders(proj_folder):
        p = os.path.join(f, sid + '.jsonl')
        if os.path.isfile(p):
            return p
    return os.path.join(proj_folder, sid + '.jsonl')


def pending_sids(proj_folder, mem):
    """Transcript sids not yet scanned for lessons, oldest first — across
    EVERY account that has sessions for this project."""
    from .sessions import is_internal_session
    scanned = mem.get('lessons_scanned', {})
    now = time.time()
    out = []
    seen_sids = set()
    for folder in _all_folders(proj_folder):
        if not os.path.isdir(folder):
            continue
        for nm in os.listdir(folder):
            if not nm.endswith('.jsonl'):
                continue
            sid = nm[:-6]
            if sid in scanned or sid in seen_sids:
                continue
            p = os.path.join(folder, nm)
            try:
                if now - os.path.getmtime(p) < MIN_AGE_SEC:
                    continue                  # probably still running
                if os.path.getsize(p) < 500:
                    continue                  # too small to learn from
            except OSError:
                continue
            if is_internal_session(p):
                continue                      # claudectl's own claude -p calls
            seen_sids.add(sid)
            out.append((os.path.getmtime(p), sid))
    return [sid for _mt, sid in sorted(out)]


# ── extraction ───────────────────────────────────────────────

def _transcript_tail(proj_folder, sid):
    from .transcript import iter_transcript
    msgs = iter_transcript(_sid_path(proj_folder, sid))
    tail = msgs[-TAIL_MSGS:]
    parts, total = [], 0
    for m in reversed(tail):                  # newest first until char cap
        piece = f"{m['role'].upper()}: {m['text'][:2000]}"
        if total + len(piece) > TAIL_CHARS:
            break
        parts.append(piece)
        total += len(piece)
    return '\n\n'.join(reversed(parts))


def extract_lessons(project_path, proj_folder, sid):
    """ONE Claude call → [{title,summary,kind,confidence,files}] (≤8)."""
    corpus = _transcript_tail(proj_folder, sid)
    if not corpus.strip():
        return []
    prompt = (
        "You are distilling durable LESSONS from a coding-session transcript so "
        "future sessions on this project start smarter. Extract only facts worth "
        "remembering long-term: error→fix pairs, decisions made (and why), "
        "corrected assumptions, and user preferences. Skip anything one-off.\n\n"
        "Output ONLY valid JSON, no prose, no code fences:\n"
        '{"lessons":[{"title":"short name","summary":"one concise sentence",'
        '"kind":"error_fix|decision|correction|preference",'
        '"confidence":0.0,"files":["rel/path"]}]}\n\n'
        f"At most {MAX_LESSONS} lessons. Empty list if nothing durable.\n\n"
        f"TRANSCRIPT (most recent session):\n{corpus}"
    )
    data = memory._parse_json(memory._claude_stdin(
        prompt, os.path.abspath(project_path),
        crumbs=('CLAUDECTL', 'LESSONS', sid[:8]),
        label=f"Learning from session {sid[:8]}..."))
    if not isinstance(data, dict):
        return []
    out = []
    for l in (data.get('lessons') or [])[:MAX_LESSONS]:
        if not isinstance(l, dict) or not l.get('title'):
            continue
        out.append({'title': str(l['title'])[:80],
                    'summary': str(l.get('summary', ''))[:300],
                    'kind': l.get('kind', 'decision'),
                    'confidence': float(l.get('confidence', 0.5) or 0.5),
                    'files': [str(f) for f in (l.get('files') or [])][:4]})
    return out


# ── merge / decay ────────────────────────────────────────────

def _jaccard(a, b):
    ta, tb = memory._tokens(a), memory._tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def merge_lessons(mem, new, sid):
    """Append new lessons as pending entities; near-duplicates merge into the
    existing lesson (keep higher confidence, bump last_used). High-confidence
    lessons auto-approve (memory_lessons_autoapprove) to cut manual review.
    Returns #added."""
    from .config import load_settings
    auto = load_settings().get('memory_lessons_autoapprove', 0.8) or 0
    existing = [e for e in mem.get('entities', []) if e.get('type') == 'lesson']
    counter = mem.get('session_counter', 0)
    added = 0
    for i, l in enumerate(new):
        dup = next((e for e in existing
                    if _jaccard(e.get('summary', ''), l['summary']) > JACCARD_MERGE), None)
        if dup is not None:
            dup['confidence'] = max(dup.get('confidence', 0), l['confidence'])
            dup['last_used'] = counter
            dup.setdefault('sids', []).append(sid)
            continue
        status = 'approved' if (auto and l['confidence'] >= auto) else 'pending'
        ent = {'id': f'lesson:{sid}:{i}', 'name': l['title'], 'type': 'lesson',
               'summary': l['summary'], 'kind': l['kind'],
               'confidence': l['confidence'], 'status': status,
               'sid': sid, 'sids': [sid], 'created_at': memory._iso(),
               'last_used': counter, 'repo': '', 'module': '(project)',
               'source_files': l['files'], 'rank': 0}
        mem['entities'].append(ent)
        existing.append(ent)
        added += 1
    return added


def apply_decay(mem, settings=None):
    """Evict unpinned lessons unused for > memory_lessons_ttl sessions."""
    from .config import load_settings
    ttl = (settings or load_settings()).get('memory_lessons_ttl', 30)
    counter = mem.get('session_counter', 0)
    def keep(e):
        if e.get('type') != 'lesson' or e.get('status') == 'pinned':
            return True
        return counter - e.get('last_used', counter) <= ttl
    before = len(mem.get('entities', []))
    mem['entities'] = [e for e in mem.get('entities', []) if keep(e)]
    return before - len(mem['entities'])


def start_background_scan(project_path, proj_folder, sids):
    """Learn lessons from finished sessions in a daemon thread (headless Claude
    calls) so 'auto' mode never blocks the TUI on project open."""
    import threading
    from . import memory as _memory
    if not sids:
        return None
    if _memory.scan_lock_status(project_path) is not None:
        return None                          # a detached worker is already on it

    def _work():
        _memory._tls.silent = True
        try:
            scan_sessions(project_path, proj_folder, sids)
        except Exception:
            from . import config as _c
            _c.log.exception('lessons: background scan failed')

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    return t


def scan_sessions(project_path, proj_folder, sids=None):
    """Extract + merge lessons for the given (or all pending) sids. Saves the
    graph. Returns (n_added, n_scanned)."""
    mem = memory.load_memory(project_path, proj_folder)
    todo = sids if sids is not None else pending_sids(proj_folder, mem)
    added = 0
    for i, sid in enumerate(todo):
        lessons = extract_lessons(project_path, proj_folder, sid)
        mem['session_counter'] = mem.get('session_counter', 0) + 1
        added += merge_lessons(mem, lessons, sid)
        mem.setdefault('lessons_scanned', {})[sid] = memory._iso()
        # persist after EVERY transcript (each cost a Claude call) — an
        # interrupted scan keeps its completed work and never re-scans it
        memory.save_memory(project_path, proj_folder, mem)
        memory._report_progress(f"lessons {i + 1}/{len(todo)}")
    if todo:
        apply_decay(mem)
        memory.save_memory(project_path, proj_folder, mem)
        try:
            from . import conventions
            conventions.sync_to_global()             # promote cross-project conventions
        except Exception:
            pass
    return added, len(todo)


# ── review UI ────────────────────────────────────────────────

_MARK = {'pending': '[pending]', 'approved': '[ok]', 'pinned': '[pin]'}


def review_screen(project_path, proj_folder, project_name):
    """Approve / pin / evict lessons. Only approved|pinned are injectable."""
    from .ui import wait_event, flash
    from . import render
    from .config import C_DIM, C_RESET, C_OK, C_WARN
    sel = 0
    while True:
        mem = memory.load_memory(project_path, proj_folder)
        lessons = [e for e in mem.get('entities', []) if e.get('type') == 'lesson']
        lessons.sort(key=lambda e: (e.get('status') != 'pending', -e.get('confidence', 0)))
        frame = [render.header('CLAUDECTL', project_name, 'LESSONS'), '']
        if not lessons:
            frame += [f"  {C_DIM}No lessons yet. They appear after sessions are scanned",
                      f"  (badge in the sessions menu, or 'auto' in settings).{C_RESET}"]
        sel = max(0, min(sel, len(lessons) - 1)) if lessons else 0
        for i, e in enumerate(lessons):
            mark = _MARK.get(e.get('status', 'pending'), '[?]')
            col = C_WARN if e.get('status') == 'pending' else C_OK
            cur = '▸' if i == sel else ' '
            frame.append(f"  {cur} {col}{mark:<9}{C_RESET} {e.get('name', '')}"
                         f"  {C_DIM}({e.get('kind', '')}, conf {e.get('confidence', 0):.1f}){C_RESET}")
            if i == sel:
                frame.append(f"      {C_DIM}{e.get('summary', '')}{C_RESET}")
        frame += ['', render.hline(), '', render.hint_keys(
            [('↑↓', 'select'), ('a', 'approve'), ('P', 'pin'), ('x', 'evict'),
             ('A', 'approve all pending'), ('ESC', 'back')])]
        render.render_frame(frame)
        ev = wait_event()
        if ev[0] == 'esc':
            return
        if ev[0] == 'up':
            sel = max(0, sel - 1)
        elif ev[0] == 'down':
            sel = min(len(lessons) - 1, sel + 1) if lessons else 0
        elif ev[0] == 'char' and lessons:
            ch = ev[1]
            e = lessons[sel]
            if ch == 'a':
                _set_status(project_path, proj_folder, e['id'], 'approved')
            elif ch == 'P':
                _set_status(project_path, proj_folder, e['id'], 'pinned')
            elif ch == 'x':
                _evict(project_path, proj_folder, e['id'])
            elif ch == 'A':
                n = 0
                for l in lessons:
                    if l.get('status') == 'pending':
                        _set_status(project_path, proj_folder, l['id'], 'approved')
                        n += 1
                flash(f"Approved {n} lessons", secs=1.2)


def _set_status(project_path, proj_folder, lesson_id, status):
    mem = memory.load_memory(project_path, proj_folder)
    for e in mem.get('entities', []):
        if e.get('id') == lesson_id:
            e['status'] = status
            break
    memory.save_memory(project_path, proj_folder, mem)


def _evict(project_path, proj_folder, lesson_id):
    mem = memory.load_memory(project_path, proj_folder)
    mem['entities'] = [e for e in mem.get('entities', []) if e.get('id') != lesson_id]
    memory.save_memory(project_path, proj_folder, mem)
