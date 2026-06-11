import os
import json
import time
import re

from .config import BAD_PREFIXES, BAD_CONTAINS, last_session_file, projects_dir


# ── session parsing ──────────────────────────────────────────

def get_session_info(jsonl_path):
    """Returns (last_user_preview: str, msg_count: int). Single file read."""
    try:
        with open(jsonl_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return '', 0

    count = 0
    for line in lines:
        ls = line.strip()
        if not ls:
            continue
        try:
            obj = json.loads(ls)
            role = obj.get('role') or obj.get('message', {}).get('role', '')
            if role in ('user', 'assistant'):
                count += 1
        except Exception:
            pass

    preview = ''
    for line in reversed(lines):
        ls = line.strip()
        if not ls:
            continue
        try:
            obj = json.loads(ls)
            role = obj.get('role') or obj.get('message', {}).get('role', '')
            if role != 'user':
                continue
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
                preview = text[:65].replace('\n', ' ')
                break
            if preview:
                break
        except Exception:
            continue

    return preview, count


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
