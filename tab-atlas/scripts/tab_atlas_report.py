from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPORT_HOST = "127.0.0.1"
DEFAULT_REPORT_PORT = 8790


class TabAtlasReportHandler(SimpleHTTPRequestHandler):
    server_version = "TabAtlasReport/1"

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self.path in {"/", "/index.html", "/app.js", "/app.css"}:
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "private, max-age=3600")
        super().end_headers()

    def list_directory(self, path: str) -> Any:
        self.send_error(404)
        return None

    def do_POST(self) -> None:
        self.send_error(405)

    do_DELETE = do_POST
    do_PATCH = do_POST
    do_PUT = do_POST

    def log_message(self, format_value: str, *args: Any) -> None:
        return


def create_report_server(
    report_dir: Path,
    port: int = DEFAULT_REPORT_PORT,
) -> tuple[ThreadingHTTPServer, str]:
    root = report_dir.resolve()
    required = (root / "index.html", root / "app.js", root / "app.css")
    if not all(path.is_file() for path in required):
        raise ValueError("Generate the TabAtlas report before serving it")
    if not 0 <= port <= 65_535:
        raise ValueError("Report port must be between 0 and 65535")
    handler = partial(TabAtlasReportHandler, directory=str(root))
    server = ThreadingHTTPServer((REPORT_HOST, port), handler)
    server.daemon_threads = True
    return server, f"http://{REPORT_HOST}:{server.server_port}/"
