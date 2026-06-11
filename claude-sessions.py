# claudectl — launcher stub
# Called by: Open Repo cmd.bat → py "%~dp0claude-sessions.py"
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_sessions.main import run
run()
