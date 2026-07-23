"""Recent-work memory — a compact "what changed recently" log per project,
complementing the code-structure knowledge graph.

On session end a heuristic (token-free) capture records a one-line summary and
the files touched into a bounded ring buffer (.claudectl/memory/worklog.json).
On session start the digest is injected so the next session knows what the last
few sessions did. Opt-in per project (project_defaults[enc]['worklog']).

Inspired by thedotmack/claude-mem's session-observation → summary →
SessionStart-injection pattern. Capture is deliberately heuristic (no Claude
call) so it never spends tokens on every Stop.
"""

import json
import os
import time

CAP = 10                 # keep the most recent N sessions
DIGEST_N = 5             # inject at most this many
DIGEST_BUDGET = 700      # char budget for the injected block


def worklog_path(project_path):
    return os.path.join(os.path.abspath(project_path), '.claudectl', 'memory',
                        'worklog.json')


def load_worklog(project_path):
    try:
        with open(worklog_path(project_path), encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_worklog(project_path, entries):
    p = worklog_path(project_path)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(entries[-CAP:], f, indent=2)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def add_entry(project_path, entry):
    """Append (dedup by session_id) and trim to CAP. Returns the saved list."""
    entries = [e for e in load_worklog(project_path)
               if e.get('session_id') != entry.get('session_id')]
    entries.append(entry)
    entries = entries[-CAP:]
    save_worklog(project_path, entries)
    return entries


# ── heuristic capture from a session transcript (no Claude call) ──

_EDIT_TOOLS = {'Edit', 'Write', 'MultiEdit', 'NotebookEdit'}


def _iter_json(transcript_path):
    try:
        with open(transcript_path, encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def summarize_transcript(transcript_path):
    """Return (summary, sorted_files) from a session jsonl. Summary = the
    AI title if present, else the first user message. Files = paths from
    Edit/Write tool uses. All best-effort; tolerant of shape variations."""
    title = ''
    first_user = ''
    files = set()
    for obj in _iter_json(transcript_path):
        if not isinstance(obj, dict):
            continue
        if obj.get('type') == 'ai-title' and obj.get('title'):
            title = str(obj['title'])
        if not first_user and obj.get('role') == 'user' and isinstance(obj.get('content'), str):
            first_user = obj['content']
        # tool_use blocks live inside assistant message content
        msg = obj.get('message') if isinstance(obj.get('message'), dict) else obj
        content = msg.get('content') if isinstance(msg, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_use' and block.get('name') in _EDIT_TOOLS:
                    fp = (block.get('input') or {}).get('file_path') \
                        or (block.get('input') or {}).get('notebook_path')
                    if fp:
                        files.add(os.path.basename(str(fp)))
    summary = (title or first_user or '').strip().replace('\n', ' ')
    if len(summary) > 120:
        summary = summary[:117] + '…'
    return summary, sorted(files)


def capture_session(project_path, session_id, transcript_path):
    """Record one session's work. Returns the entry, or None if nothing useful
    (no summary and no files touched — don't clutter the log)."""
    summary, files = summarize_transcript(transcript_path)
    if not summary and not files:
        return None
    entry = {'session_id': session_id,
             'ended_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
             'summary': summary, 'files': files}
    add_entry(project_path, entry)
    return entry


# ── digest for injection ─────────────────────────────────────

def _ago(iso):
    try:
        t = time.mktime(time.strptime(iso, '%Y-%m-%dT%H:%M:%SZ'))
        secs = max(0, time.time() - t)
    except Exception:
        return ''
    for unit, n in (('d', 86400), ('h', 3600), ('m', 60)):
        if secs >= n:
            return f"{int(secs // n)}{unit} ago"
    return 'just now'


def render_digest(project_path, n=DIGEST_N, budget=DIGEST_BUDGET):
    """A tight markdown 'Recent work' block, or '' if the log is empty."""
    entries = load_worklog(project_path)
    if not entries:
        return ''
    lines = ["## Recent work (claudectl — last sessions)"]
    for e in reversed(entries[-n:]):
        when = _ago(e.get('ended_at', ''))
        files = e.get('files') or []
        ftail = f" — {', '.join(files[:5])}" + ('…' if len(files) > 5 else '') if files else ''
        summ = e.get('summary') or '(no summary)'
        line = f"- {when + ': ' if when else ''}{summ}{ftail}"
        if sum(len(x) + 1 for x in lines) + len(line) > budget:
            break
        lines.append(line)
    return '\n'.join(lines) if len(lines) > 1 else ''
