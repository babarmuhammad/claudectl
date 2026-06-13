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
    tb = traceback.format_exc()
    banner = "\n" + "=" * 60 + "\n  CLAUDECTL CRASH\n" + "=" * 60 + "\n" + tb + "=" * 60

    # Always persist the traceback — console may be closed/redirected
    try:
        import tempfile
        log_path = os.path.join(tempfile.gettempdir(), 'claudectl_crash.log')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(banner + "\n")
    except Exception:
        log_path = None

    try:
        os.system('cls')
        print(banner)   # stdout avoids encoding issues with stderr
        if log_path:
            print(f"\n  Saved to {log_path}")
        input("\n  Press Enter to close...")
    except BaseException:
        # stdout/stdin may be None/closed/redirected (GUI launch) → any error
        # is possible (ValueError, AttributeError, OSError, EOFError). The log
        # file already has the traceback; never raise from the crash handler.
        pass
    sys.exit(1)
