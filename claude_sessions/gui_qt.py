"""Native desktop shell for the claudectl GUI — a PyQt6 window hosting the
local web app (like Claude Desktop: web UI in a native frame). PyQt6 is an
OPTIONAL dependency: gui.run_gui() only calls run_desktop() when the import
succeeds, falling back to an Edge app-mode window, then the default browser.
"""

import os
import sys
import threading


def _icon_path():
    # GUI-specific icon first, TUI icon as fallback; repo root (dev checkout)
    # or alongside the package
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pkg = os.path.dirname(os.path.abspath(__file__))
    for name in ('claudectl-gui.ico', 'claudectl.ico'):
        for cand in (os.path.join(here, name), os.path.join(pkg, name)):
            if os.path.isfile(cand):
                return cand
    return ''


def run_desktop():
    """Serve the GUI and show it in a native Qt window. Blocks until the
    window closes. Raises ImportError if PyQt6/WebEngine is unavailable —
    caller falls back."""
    # QtWebEngine composites via a GPU hardware surface on Windows; on any
    # continuously-animating content (the job-modal spinner) that surface
    # swap tears/flickers — a plain Chromium tab doesn't, which is why the
    # browser shell is fine and only the Qt shell flickers. Disabling GPU
    # compositing routes rendering through the CPU compositor and stops it.
    # Must be set before QtWebEngine initializes (i.e. before QApplication).
    flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
    if '--disable-gpu' not in flags:
        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (
            flags + ' --disable-gpu-compositing').strip()

    from PyQt6.QtWidgets import QApplication, QMainWindow
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtGui import QIcon, QDesktopServices, QColor
    from PyQt6.QtCore import QUrl

    from .gui import make_server

    srv = make_server()
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # QtWebEngine's Chromium layer needs argv[0] (the program name) — an
    # empty list crashes it with STATUS_STACK_BUFFER_OVERRUN on load.
    app = QApplication(sys.argv[:1] or ['claudectl'])
    app.setApplicationName('claudectl')
    win = QMainWindow()
    win.setWindowTitle('claudectl')
    ico = _icon_path()
    if ico:
        win.setWindowIcon(QIcon(ico))
    view = QWebEngineView()
    # QWebEngineView's page defaults to a white backing surface; every repaint
    # (e.g. the job-progress modal's per-second text update) briefly shows that
    # white surface through before Chromium composites the dark page over it,
    # reading as a flicker. app.css's --bg is always #0d1117 (GUI has no light
    # theme), so matching it here removes the flash entirely.
    view.page().setBackgroundColor(QColor('#0d1117'))
    # window.open (graph tab) is silently dropped by QWebEngineView unless
    # new-window requests are handled — route them to the system browser
    view.page().newWindowRequested.connect(
        lambda req: QDesktopServices.openUrl(req.requestedUrl()))
    view.load(QUrl(f'http://127.0.0.1:{port}/'))
    win.setCentralWidget(view)
    win.resize(1280, 840)
    win.show()
    try:
        app.exec()
    finally:
        srv.shutdown()
        srv.server_close()
