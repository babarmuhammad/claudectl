"""`python -m claude_sessions [...]` — same dispatch as claude-sessions.py.

Exists so the detached background memory worker (memory.spawn_background_worker)
can re-invoke claudectl without depending on the repo-root launcher script.
"""
from .main import run

run()
