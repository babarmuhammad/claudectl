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
    from PyQt6.QtWidgets import QApplication, QMainWindow
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtGui import QIcon, QDesktopServices
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
