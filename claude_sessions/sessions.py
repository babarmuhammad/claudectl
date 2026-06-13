import os
import json
import time
import re

from .config import BAD_PREFIXES, BAD_CONTAINS, last_session_file, projects_dir


# ── session parsing ──────────────────────────────────────────

_info_cache = {}   # jsonl_path -> ((mtime_ns, size), (preview, count, title))


def _extract_texts(obj):
    """Pull text blocks from a message object's content."""
    content = obj.get('content') or obj.get('message', {}).get('content', '')
    texts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                texts.append(block.get('text', '').strip())
    elif isinstance(content, str):
        texts.append(content.strip())
    return texts


def _good_text(text):
    if not text or len(text) < 5:
        return False
    if any(text.startswith(p) for p in BAD_PREFIXES):
        return False
    if any(b in text.lower() for b in BAD_CONTAINS):
        return False
    return True


_EMPTY_STATS = {
    'preview': '', 'count': 0, 'title': '',
    'usage_by_model': {}, 'models': [],
    'first_ts': None, 'last_ts': None,
    'branch': '', 'cwd': '', 'api_errors': 0,
}


def _iso_to_epoch(ts):
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


def _parse_session(jsonl_path):
    """Single-pass parse, cached by (mtime, size). Returns a stats dict:
    preview, count, title, usage_by_model, models, first_ts, last_ts,
    branch, cwd, api_errors. JSON-serializable (feeds the disk cache)."""
    try:
        st = os.stat(jsonl_path)
    except OSError:
        return dict(_EMPTY_STATS)
    key = (st.st_mtime_ns, st.st_size)
    cached = _info_cache.get(jsonl_path)
    if cached and cached[0] == key:
        return cached[1]

    try:
        with open(jsonl_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return dict(_EMPTY_STATS)

    s = dict(_EMPTY_STATS)
    s['usage_by_model'] = {}
    s['models'] = []
    for line in lines:
        ls = line.strip()
        if not ls:
            continue
        try:
            obj = json.loads(ls)
        except Exception:
            continue

        if obj.get('type') == 'ai-title' and not s['title']:
            s['title'] = (obj.get('title', '') or obj.get('content', '')).strip()

        ts = obj.get('timestamp')
        if isinstance(ts, str):
            ep = _iso_to_epoch(ts)
            if ep is not None:
                if s['first_ts'] is None or ep < s['first_ts']:
                    s['first_ts'] = ep
                if s['last_ts'] is None or ep > s['last_ts']:
                    s['last_ts'] = ep

        if obj.get('gitBranch'):
            s['branch'] = obj['gitBranch']
        if obj.get('cwd'):
            s['cwd'] = obj['cwd']
        if obj.get('isApiErrorMessage'):
            s['api_errors'] += 1

        msg  = obj.get('message') or {}
        role = obj.get('role') or msg.get('role', '')
        if role in ('user', 'assistant'):
            s['count'] += 1
        if role == 'user':
            for text in _extract_texts(obj):
                if _good_text(text):
                    s['preview'] = text[:65].replace('\n', ' ')   # last good one wins
                    break
        elif role == 'assistant':
            model = msg.get('model', '')
            if model.startswith('<'):   # '<synthetic>' internal marker
                model = ''
            usage = msg.get('usage') or {}
            if model and model not in s['models']:
                s['models'].append(model)
            if usage and model:
                u = s['usage_by_model'].setdefault(
                    model, {'in': 0, 'out': 0, 'cache_read': 0, 'cache_create': 0})
                u['in']           += usage.get('input_tokens', 0) or 0
                u['out']          += usage.get('output_tokens', 0) or 0
                u['cache_read']   += usage.get('cache_read_input_tokens', 0) or 0
                u['cache_create'] += usage.get('cache_creation_input_tokens', 0) or 0

    _info_cache[jsonl_path] = (key, s)
    return s


def get_session_info(jsonl_path):
    """Returns (last_user_preview: str, msg_count: int). Cached by mtime+size."""
    s = _parse_session(jsonl_path)
    return s['preview'], s['count']


def get_session_title(jsonl_path):
    """AI-generated session title from the transcript, '' if none. Cached."""
    return _parse_session(jsonl_path)['title']


def get_session_stats(jsonl_path):
    """Full per-session stats dict (tokens, models, timestamps, branch...). Cached."""
    return _parse_session(jsonl_path)


def get_session_rich_summary(jsonl_path, max_user_msgs=15):
    """Extract ai-title + significant user messages for AI context building."""
    try:
        with open(jsonl_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return '', []

    ai_title = ''
    user_msgs = []

    for line in lines:
        ls = line.strip()
        if not ls:
            continue
        try:
            obj = json.loads(ls)
            # Grab ai-title if present
            if obj.get('type') == 'ai-title' and not ai_title:
                ai_title = obj.get('title', '') or obj.get('content', '')
            # Collect user messages
            role = obj.get('role') or obj.get('message', {}).get('role', '')
            if role == 'user':
                content = obj.get('content') or obj.get('message', {}).get('content', '')
                texts = []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            texts.append(block.get('text', '').strip())
                elif isinstance(content, str):
                    texts.append(content.strip())
                for text in texts:
                    if not text or len(text) < 5:
                        continue
                    if any(text.startswith(p) for p in BAD_PREFIXES):
                        continue
                    if any(b in text.lower() for b in BAD_CONTAINS):
                        continue
                    user_msgs.append(text[:200])
                    break
            if len(user_msgs) >= max_user_msgs:
                break
        except Exception:
            continue

    return ai_title, user_msgs


def format_age(mtime):
    age = time.time() - mtime
    if age < 60:       return 'now  '
    elif age < 3600:   return f"{int(age/60)}m   "[:5]
    elif age < 86400:  return f"{int(age/3600)}h   "[:5]
    else:              return f"{int(age/86400)}d   "[:5]


# ── persistence helpers ──────────────────────────────────────

def load_name(proj_folder, sid):
    try:
        with open(os.path.join(proj_folder, f"{sid}.name"), 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return ''


def save_name(proj_folder, sid, name):
    path = os.path.join(proj_folder, f"{sid}.name")
    if name:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(name)
    elif os.path.exists(path):
        os.remove(path)


def load_extra_paths(proj_folder):
    try:
        with open(os.path.join(proj_folder, 'extra-paths.txt'), 'r', encoding='utf-8') as f:
            return [l.strip() for l in f if l.strip()]
    except Exception:
        return []


def save_extra_paths(proj_folder, paths):
    with open(os.path.join(proj_folder, 'extra-paths.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(paths))


def session_changed_files(jsonl_path):
    """Files the session edited/created, derived from Edit/Write/MultiEdit/
    NotebookEdit tool calls in the transcript. Returns [(path, count)] sorted
    by edit count desc."""
    counts = {}
    try:
        with open(jsonl_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return []
    for line in lines:
        ls = line.strip()
        if not ls:
            continue
        try:
            obj = json.loads(ls)
        except Exception:
            continue
        content = (obj.get('message') or {}).get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not (isinstance(block, dict) and block.get('type') == 'tool_use'):
                continue
            if block.get('name') not in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
                continue
            fp = (block.get('input') or {}).get('file_path') \
                or (block.get('input') or {}).get('notebook_path')
            if fp:
                counts[fp] = counts.get(fp, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


# ── per-project tags (tags.json: sid -> [tags]) ──────────────

def load_tags(proj_folder):
    try:
        with open(os.path.join(proj_folder, 'tags.json'), encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_tags(proj_folder, tags):
    try:
        with open(os.path.join(proj_folder, 'tags.json'), 'w', encoding='utf-8') as f:
            json.dump(tags, f, indent=2)
        return True
    except Exception:
        return False


def load_session_agents(proj_folder):
    """session-agents.json: {session_key: [agent refs]}. Key = sid, or
    '__new__'/'__continue__' for those actions."""
    try:
        with open(os.path.join(proj_folder, 'session-agents.json'), encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_session_agents(proj_folder, key, refs):
    data = load_session_agents(proj_folder)
    if refs:
        data[key] = refs
    else:
        data.pop(key, None)
    try:
        with open(os.path.join(proj_folder, 'session-agents.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


def load_add_dirs(proj_folder):
    """Per-project --add-dir entries from add-dirs.txt."""
    if not proj_folder:
        return []
    try:
        with open(os.path.join(proj_folder, 'add-dirs.txt'), 'r', encoding='utf-8') as f:
            return [l.strip() for l in f if l.strip() and not l.startswith('#')]
    except Exception:
        return []


def save_add_dirs(proj_folder, dirs):
    with open(os.path.join(proj_folder, 'add-dirs.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(dirs))


def scan_sessions(folder):
    """List sessions in a project folder. Returns [(mtime, sid, preview, count)] newest-first."""
    sessions = []
    if not folder or not os.path.isdir(folder):
        return sessions
    for f in os.listdir(folder):
        if not f.endswith('.jsonl'):
            continue
        fpath = os.path.join(folder, f)
        try:
            mtime = os.path.getmtime(fpath)
        except OSError:
            continue
        preview, count = get_session_info(fpath)
        sessions.append((mtime, f[:-6], preview, count))
    sessions.sort(reverse=True)
    return sessions


def read_extra_paths(proj_folder):
    """Return list of extra PATH entries from extra-paths.txt, skipping blanks/comments."""
    if not proj_folder:
        return []
    ep = os.path.join(proj_folder, 'extra-paths.txt')
    if not os.path.exists(ep):
        return []
    paths = []
    try:
        with open(ep, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                p = line.strip()
                if p and not p.startswith('#') and os.path.isdir(p):
                    paths.append(p)
    except Exception:
        pass
    return paths


def load_recent_sessions(n=5):
    """Load up to n recent sessions, validate each still exists."""
    try:
        with open(last_session_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return []
    # support old single-entry format
    if isinstance(data, dict):
        data = [data]
    valid = []
    for entry in data:
        p   = entry.get('project_path', '')
        enc = entry.get('encoded_name', '')
        sid = entry.get('session_id', '')
        if not (p and enc and sid):
            continue
        if not os.path.exists(p):
            continue
        if not os.path.exists(os.path.join(projects_dir, enc, f"{sid}.jsonl")):
            continue
        valid.append(entry)
        if len(valid) >= n:
            break
    return valid


def load_last_session():
    """Compat wrapper — returns first valid recent session or None."""
    sessions = load_recent_sessions(1)
    return sessions[0] if sessions else None


def save_last_session(project_path, encoded_name, session_id, preview=''):
    try:
        new_entry = {
            'project_path': project_path,
            'encoded_name': encoded_name,
            'session_id':   session_id,
            'preview':      preview,
            'timestamp':    time.time(),
        }
        # load existing list
        try:
            with open(last_session_file, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            if isinstance(existing, dict):
                existing = [existing]
        except Exception:
            existing = []
        # dedup by session_id, newest first
        merged = [new_entry] + [e for e in existing if e.get('session_id') != session_id]
        with open(last_session_file, 'w', encoding='utf-8') as f:
            json.dump(merged[:5], f)
    except Exception:
        pass
