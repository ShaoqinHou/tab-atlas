from __future__ import annotations

import json
import secrets
from functools import partial
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .api_read_routes import serve_audio
from .api_routes import handle_get, handle_post
from .lease import WorkspaceLease
from .runtime import TabAtlasWorkspaceServer
from .server_constants import (
    COOKIE_MAX_AGE_SECONDS,
    COOKIE_NAME,
    DEFAULT_WORKSPACE_PORT,
    MAX_JSON_BYTES,
    WORKSPACE_HOST,
)


class TabAtlasWorkspaceHandler(SimpleHTTPRequestHandler):
    server_version = "TabAtlasWorkspace/1"

    @property
    def workspace_server(self) -> TabAtlasWorkspaceServer:
        return self.server  # type: ignore[return-value]

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Permissions-Policy", "camera=(), microphone=(self), geolocation=()"
        )
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https://i.ytimg.com; "
            "media-src 'self' https://video.twimg.com; "
            "frame-src 'self' https://www.youtube-nocookie.com; "
            "script-src 'self' 'unsafe-inline'; style-src 'self'; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        asset_path = urlsplit(self.path).path
        if (
            asset_path in {"/", "/index.html", "/app.js", "/app.css"}
            or asset_path.startswith("/modules/")
            or asset_path.startswith("/styles/")
        ):
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "private, max-age=3600")
        super().end_headers()

    def do_GET(self) -> None:
        if not self._valid_host():
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/" and self._accept_bootstrap(parse_qs(parsed.query)):
            return
        if not self._authenticated():
            if parsed.path in {"/", "/index.html"}:
                self._send_browser_access_required()
            else:
                self.send_error(HTTPStatus.UNAUTHORIZED)
            return
        if handle_get(self, parsed.path):
            return
        super().do_GET()

    def do_POST(self) -> None:
        if not self._valid_write_request():
            return
        handle_post(self, urlsplit(self.path).path)

    def do_OPTIONS(self) -> None:
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    do_DELETE = do_OPTIONS
    do_PATCH = do_OPTIONS
    do_PUT = do_OPTIONS

    def list_directory(self, path: str) -> Any:
        self.send_error(HTTPStatus.NOT_FOUND)
        return None

    def log_message(self, format_value: str, *args: Any) -> None:
        return

    def _accept_bootstrap(self, query: dict[str, list[str]]) -> bool:
        candidates = query.get("bootstrap") or []
        if len(candidates) != 1 or not secrets.compare_digest(
            candidates[0], self.workspace_server.session_token
        ):
            return False
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}={self.workspace_server.session_token}; Path=/; "
            f"Max-Age={COOKIE_MAX_AGE_SECONDS}; HttpOnly; SameSite=Strict",
        )
        self.end_headers()
        return True

    def _send_browser_access_required(self) -> None:
        payload = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect this browser to TabAtlas</title></head><body>
<main><h1>Connect this browser to TabAtlas</h1>
<p>This browser has not been authorized for the local workspace yet.</p>
<p>Ask Codex to open TabAtlas in this browser once, or start the workspace with <code>workspace --open</code>.</p>
<p>After that first connection, this address will open normally in this browser.</p></main>
</body></html>"""
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _valid_host(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("Host", ""), self.workspace_server.expected_host
        )

    def _authenticated(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        morsel = cookie.get(COOKIE_NAME)
        return bool(
            morsel
            and secrets.compare_digest(
                morsel.value, self.workspace_server.session_token
            )
        )

    def _valid_write_request(self) -> bool:
        if not self._valid_host():
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        if not self._authenticated():
            self.send_error(HTTPStatus.UNAUTHORIZED)
            return False
        if not secrets.compare_digest(
            self.headers.get("Origin", ""), self.workspace_server.origin
        ):
            self.send_error(HTTPStatus.FORBIDDEN)
            return False
        if not secrets.compare_digest(
            self.headers.get("X-TabAtlas-CSRF", ""), self.workspace_server.csrf_token
        ):
            self.send_error(HTTPStatus.FORBIDDEN)
            return False
        return True

    def _idempotency_key(self) -> str:
        return self.headers.get("Idempotency-Key", "")

    def _read_json_body(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        payload = self._read_raw_body(MAX_JSON_BYTES)
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _read_empty_body(self) -> None:
        length = self._content_length(0)
        if length:
            raise ValueError("This request must not include a body")

    def _read_raw_body(self, maximum: int) -> bytes:
        length = self._content_length(maximum)
        payload = self.rfile.read(length)
        if len(payload) != length:
            raise ValueError("Request body ended early")
        return payload

    def _content_length(self, maximum: int) -> int:
        value = self.headers.get("Content-Length")
        if value is None or not value.isdigit():
            raise ValueError("Content-Length is required")
        length = int(value)
        if length < 0 or length > maximum:
            raise ValueError("Request body is too large")
        return length

    def _serve_audio(self, note_id: str) -> None:
        serve_audio(self, note_id)

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def create_workspace_server(
    workspace_root: Path,
    state_dir: Path,
    database_path: Path,
    report_dir: Path,
    report_assets: Path,
    port: int = DEFAULT_WORKSPACE_PORT,
) -> tuple[TabAtlasWorkspaceServer, str]:
    root = report_dir.resolve()
    required = (root / "index.html", root / "app.js", root / "app.css")
    if not all(path.is_file() for path in required):
        raise ValueError("Generate the TabAtlas report before starting the workspace")
    if not 0 <= port <= 65_535:
        raise ValueError("Workspace port must be between 0 and 65535")
    lease = WorkspaceLease(state_dir.resolve() / "workspace.lock")
    try:
        handler = partial(TabAtlasWorkspaceHandler, directory=str(root))
        server = TabAtlasWorkspaceServer(
            (WORKSPACE_HOST, port),
            handler,
            workspace_root=workspace_root,
            state_dir=state_dir,
            database_path=database_path,
            report_dir=report_dir,
            report_assets=report_assets,
            lease=lease,
        )
    except Exception:
        lease.close()
        raise
    url = f"http://{WORKSPACE_HOST}:{server.server_port}/?bootstrap={server.session_token}"
    return server, url
