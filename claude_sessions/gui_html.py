"""The claudectl GUI single-page app. Pure static markup — all data arrives
via fetch() from gui.py / gui_api.py endpoints. Self-contained: no CDN, no
external fonts, works offline. Full TUI parity: sessions, transcript,
memory suite, CLAUDE.md ops (job + diff-approve), usage, search, managers."""

from pathlib import Path

_WEB = Path(__file__).parent / "web"
_cache = {}

def _read(name):
    if name not in _cache:
        _cache[name] = (_WEB / name).read_text(encoding="utf-8")
    return _cache[name]

def _build_html():
    html = _read("index.html")
    html = html.replace("<!--CSS-->", _read("app.css"))
    html = html.replace("<!--JS-->", _read("app.js"))
    return html

PAGE = _build_html()
