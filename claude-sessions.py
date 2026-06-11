# claudectl — launcher stub
# Called by: Open Repo cmd.bat → py "%~dp0claude-sessions.py"
import sys, os, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from claude_sessions.main import run
    run()
except SystemExit:
    raise   # normal exit — let bat file handle choice_file check
except BaseException:
    try:
        os.system('cls')
    except Exception:
        pass
    tb = traceback.format_exc()
    print("\n" + "=" * 60)
    print("  CLAUDECTL CRASH")
    print("=" * 60)
    print(tb)   # stdout avoids encoding issues with stderr
    print("=" * 60)
    input("\n  Press Enter to close...")
    sys.exit(1)
