from __future__ import annotations

import json
import mimetypes
import re
import secrets
from functools import partial
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..catalog import inventory, remove_library_resources, set_discovery_state
from .agent import AgentUnavailable
from .agent_requests import agent_request_detail, create_workspace_chat_request
from .context import _resource_by_id, _safe_error_code
from .directory import update_action_progress
from .lease import WorkspaceLease
from .notes import (
    create_note_analysis_request,
    create_text_note,
    resource_notes,
    set_note_active,
)
from .transcription import add_audio_transcript, create_audio_note
from .runtime import TabAtlasWorkspaceServer
from .semantics import (
    apply_semantic_proposal,
    reject_semantic_proposal,
    undo_semantic_audit,
)
from .server_constants import (
    COOKIE_NAME,
    DEFAULT_WORKSPACE_PORT,
    MAX_JSON_BYTES,
    OPAQUE_ID_PATTERN,
    RESOURCE_ID_PATTERN,
    WORKSPACE_HOST,
)
from .support import ConflictError


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
            self.send_error(HTTPStatus.UNAUTHORIZED)
            return
        if parsed.path == "/api/v1/session":
            self._send_json(self.workspace_server.session_status())
            return
        if parsed.path == "/api/v1/catalog":
            self._send_json(self.workspace_server.catalog_snapshot())
            return
        if parsed.path == "/api/v1/browser-sync":
            self._send_json(self.workspace_server.browser_sync.status())
            return
        match = re.fullmatch(
            rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/notes", parsed.path
        )
        if match:
            with self.workspace_server.database() as connection:
                notes = resource_notes(connection, match.group(1))
            self._send_json({"resourceId": match.group(1), "notes": notes})
            return
        match = re.fullmatch(
            rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/workspace-state", parsed.path
        )
        if match:
            with self.workspace_server.database() as connection:
                resource = _resource_by_id(connection, match.group(1))
                notes = resource_notes(connection, match.group(1))
            self._send_json(
                {
                    "resourceId": match.group(1),
                    "semanticRevision": resource.get("semanticRevision", 0),
                    "noteCount": resource.get("noteCount", 0),
                    "collections": resource.get("collections") or [],
                    "actionItems": resource.get("actionItems") or [],
                    "notes": notes,
                }
            )
            return
        match = re.fullmatch(
            rf"/api/v1/agent-requests/({OPAQUE_ID_PATTERN})", parsed.path
        )
        if match:
            with self.workspace_server.database() as connection:
                result = agent_request_detail(connection, match.group(1))
            self._send_json(result)
            return
        match = re.fullmatch(rf"/api/v1/notes/({OPAQUE_ID_PATTERN})/audio", parsed.path)
        if match:
            self._serve_audio(match.group(1))
            return
        super().do_GET()

    def do_POST(self) -> None:
        if not self._valid_write_request():
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/v1/browser-sync":
                document = self._read_json_body()
                raw_browsers = document.get("browsers", ["chrome", "edge"])
                if not isinstance(raw_browsers, list):
                    raise ValueError("browsers must be a list")
                browsers = tuple(str(browser) for browser in raw_browsers)
                result = self.workspace_server.start_browser_sync(
                    browsers, trigger="user"
                )
                self._send_json(
                    result,
                    HTTPStatus.OK if result.get("reused") else HTTPStatus.ACCEPTED,
                )
                return
            match = re.fullmatch(r"/api/v1/discoveries/(accept|dismiss)", path)
            if match:
                document = self._read_json_body()
                resource_ids = document.get("resourceIds")
                if not isinstance(resource_ids, list) or not resource_ids:
                    raise ValueError("resourceIds must be a non-empty list")
                target_state = "accepted" if match.group(1) == "accept" else "dismissed"
                with self.workspace_server.database() as connection:
                    result = set_discovery_state(connection, target_state, resource_ids)
                    result["inventory"] = inventory(connection)
                self.workspace_server.regenerate_report()
                self._send_json(result)
                return
            match = re.fullmatch(
                rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/notes", path
            )
            if match:
                document = self._read_json_body()
                with self.workspace_server.database() as connection:
                    result = create_text_note(
                        connection,
                        match.group(1),
                        document.get("text"),
                        self._idempotency_key(),
                        supersedes_note_id=document.get("supersedesNoteId") or None,
                    )
                self._send_json(result, HTTPStatus.CREATED)
                return
            match = re.fullmatch(
                rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/voice-notes", path
            )
            if match:
                payload = self._read_raw_body(25 * 1024 * 1024)
                duration_header = self.headers.get("X-TabAtlas-Audio-Duration-Ms")
                duration = int(duration_header) if duration_header else None
                with self.workspace_server.database() as connection:
                    result = create_audio_note(
                        connection,
                        self.workspace_server.state_dir,
                        match.group(1),
                        payload,
                        self.headers.get_content_type(),
                        duration,
                        self._idempotency_key(),
                    )
                self.workspace_server.enqueue_audio_transcription(result["id"])
                self._send_json(result, HTTPStatus.CREATED)
                return
            match = re.fullmatch(
                rf"/api/v1/notes/({OPAQUE_ID_PATTERN})/transcript", path
            )
            if match:
                document = self._read_json_body()
                with self.workspace_server.database() as connection:
                    result = add_audio_transcript(
                        connection,
                        match.group(1),
                        document.get("text"),
                        self._idempotency_key(),
                    )
                self._send_json(result)
                return
            match = re.fullmatch(rf"/api/v1/notes/({OPAQUE_ID_PATTERN})/analyze", path)
            if match:
                self._read_empty_body()
                with self.workspace_server.database() as connection:
                    result = create_note_analysis_request(
                        connection,
                        match.group(1),
                        self._idempotency_key(),
                    )
                self.workspace_server.enqueue_agent_request(result["id"])
                self._send_json(result, HTTPStatus.ACCEPTED)
                return
            match = re.fullmatch(rf"/api/v1/notes/({OPAQUE_ID_PATTERN})/retract", path)
            if match:
                self._read_empty_body()
                with self.workspace_server.database() as connection:
                    result = set_note_active(
                        connection,
                        match.group(1),
                        False,
                        self._idempotency_key(),
                    )
                self.workspace_server.regenerate_report()
                self._send_json(result)
                return
            match = re.fullmatch(
                rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/action-lists/({OPAQUE_ID_PATTERN})/progress",
                path,
            )
            if match:
                document = self._read_json_body()
                with self.workspace_server.database() as connection:
                    result = update_action_progress(
                        connection,
                        match.group(1),
                        match.group(2),
                        state=document.get("state"),
                        priority=document.get("priority", 3),
                        completed_units=document.get("completedUnits", 0),
                        total_units=document.get("totalUnits"),
                        due_at=document.get("dueAt"),
                        expected_revision=document.get("expectedRevision", 0),
                        request_id=self._idempotency_key(),
                    )
                self.workspace_server.regenerate_report()
                self._send_json(result)
                return
            match = re.fullmatch(
                rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/remove", path
            )
            if match:
                self._read_empty_body()
                with self.workspace_server.database() as connection:
                    result = remove_library_resources(connection, [match.group(1)])
                self.workspace_server.regenerate_report()
                self._send_json(result)
                return
            if path == "/api/v1/agent-requests":
                document = self._read_json_body()
                with self.workspace_server.database() as connection:
                    result = create_workspace_chat_request(
                        connection,
                        document.get("message"),
                        document.get("resourceId") or None,
                    )
                self.workspace_server.enqueue_agent_request(result["id"])
                self._send_json(result, HTTPStatus.ACCEPTED)
                return
            match = re.fullmatch(
                rf"/api/v1/proposals/({OPAQUE_ID_PATTERN})/(accept|reject)", path
            )
            if match:
                with self.workspace_server.database() as connection:
                    if match.group(2) == "accept":
                        result = apply_semantic_proposal(
                            connection,
                            match.group(1),
                            "workspace_user",
                            self._idempotency_key(),
                        )
                    else:
                        result = reject_semantic_proposal(
                            connection,
                            match.group(1),
                            "workspace_user",
                            self._idempotency_key(),
                        )
                self.workspace_server.regenerate_report()
                self._send_json(result)
                return
            match = re.fullmatch(rf"/api/v1/audits/({OPAQUE_ID_PATTERN})/undo", path)
            if match:
                with self.workspace_server.database() as connection:
                    result = undo_semantic_audit(connection, match.group(1))
                self.workspace_server.regenerate_report()
                self._send_json(result)
                return
            if path == "/api/v1/agent/handoff":
                self._read_empty_body()
                self._send_json(self.workspace_server.handoff_agent())
                return
            if path == "/api/v1/agent/reclaim":
                self._read_empty_body()
                self._send_json(self.workspace_server.reclaim_agent())
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except ConflictError as error:
            self._send_json(
                {"error": str(error), "code": "conflict"}, HTTPStatus.CONFLICT
            )
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            self._send_json(
                {"error": str(error), "code": "invalid_request"}, HTTPStatus.BAD_REQUEST
            )
        except AgentUnavailable as error:
            self._send_json(
                {
                    "error": "Codex is unavailable; saved work remains local.",
                    "code": _safe_error_code(error),
                },
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

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
            f"{COOKIE_NAME}={self.workspace_server.session_token}; Path=/; HttpOnly; SameSite=Strict",
        )
        self.end_headers()
        return True

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
        with self.workspace_server.database() as connection:
            row = connection.execute(
                "SELECT audio_relpath, audio_mime, audio_sha256 FROM resource_notes WHERE id=? AND kind='audio'",
                (note_id,),
            ).fetchone()
        if not row:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        source = (self.workspace_server.state_dir / row["audio_relpath"]).resolve()
        try:
            source.relative_to(self.workspace_server.state_dir)
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not source.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = source.stat().st_size
        start, end = 0, size - 1
        status = HTTPStatus.OK
        range_header = self.headers.get("Range", "")
        if range_header:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
            if not match:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else end
            if start > end or start >= size or end >= size:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            status = HTTPStatus.PARTIAL_CONTENT
        self.send_response(status)
        self.send_header(
            "Content-Type",
            row["audio_mime"]
            or mimetypes.guess_type(source.name)[0]
            or "application/octet-stream",
        )
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with source.open("rb") as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

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
