"""Loopback server for the live 3D viewer (v15.35).

The live viewer page is served from this machine only (127.0.0.1, an
ephemeral port) so it can poll for fresh tag meshes as the user edits —
a file:// page cannot fetch local files, and the embedded WebView2 path
crashed three times with no diagnostic data, so the live viewer runs
here instead of inside tkinter. The server serves one temp directory
(the page, state.json, stl/*.stl), never lists directories, and dies
with the app (daemon thread); nothing leaves this PC.
"""

import functools
import http.server
import threading
from pathlib import Path


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass  # polling every 600ms must not spam the app log

    def list_directory(self, path):
        self.send_error(404, "No listing")


# v15.37: a STABLE loopback port, so a live tab survives an app restart
# (the old tab's polls hit the new run's server and self-heal). If the
# port is truly occupied (a second app instance), fall back to ephemeral.
LIVE_PORT = 18741


class LiveServer:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.httpd = None
        self.thread = None
        self.port = None

    def start(self):
        """Bind 127.0.0.1 (stable port, ephemeral fallback) and serve."""
        handler = functools.partial(_Handler, directory=str(self.directory))
        try:
            self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", LIVE_PORT), handler)
        except OSError:
            self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self.port

    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def stop(self):
        try:
            if self.httpd is not None:
                self.httpd.shutdown()
                self.httpd.server_close()
        except Exception:
            pass
        self.httpd = None
