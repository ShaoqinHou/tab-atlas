from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from functools import partial
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlsplit

from tab_atlas_agent import (
    AgentBusy,
    AgentUnavailable,
    CodexAppServer,
    build_note_prompt,
    build_workspace_prompt,
)
from tab_atlas_core import (
    SPACE_DEFINITIONS,
    connect,
    generate_report,
    inventory,
    library_resources,
    remove_library_resources,
    resource_presentation,
)
from tab_atlas_workspace import (
    ConflictError,
    action_list_summaries,
    active_note_text,
    add_audio_transcript,
    agent_request_detail,
    apply_semantic_proposal,
    complete_agent_request,
    create_audio_note,
    create_text_note,
    create_workspace_chat_request,
    defer_agent_request,
    mark_agent_request_failed,
    mark_agent_request_running,
    note_counts,
    note_detail,
    proposal_can_auto_apply,
    queued_agent_requests,
    reject_semantic_proposal,
    resource_notes,
    set_note_active,
    undo_semantic_audit,
    update_action_progress,
)


WORKSPACE_HOST = "127.0.0.1"
DEFAULT_WORKSPACE_PORT = 8790
MAX_JSON_BYTES = 64 * 1024
AGENT_IDLE_SECONDS = 120
COOKIE_NAME = "tabatlas_workspace"
RESOURCE_ID_PATTERN = r"res_[a-f0-9]{24}"
OPAQUE_ID_PATTERN = r"[a-z]+_[a-f0-9]{24}"


class TabAtlasWorkspaceServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        handler: Any,
        *,
        workspace_root: Path,
        state_dir: Path,
        database_path: Path,
        report_dir: Path,
        report_assets: Path,
        lease: "WorkspaceLease",
    ) -> None:
        super().__init__(address, handler)
        self.workspace_root = workspace_root.resolve()
        self.state_dir = state_dir.resolve()
        self.database_path = database_path.resolve()
        self.report_dir = report_dir.resolve()
        self.report_assets = report_assets.resolve()
        self.lease = lease
        self.session_token = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        self.expected_host = f"{WORKSPACE_HOST}:{self.server_port}"
        self.origin = f"http://{self.expected_host}"
        self.agent = CodexAppServer(self.workspace_root, self.state_dir)
        self.agent_handed_off = False
        self._ownership_lock = threading.Lock()
        self._agent_idle_lock = threading.Lock()
        self._agent_idle_timer: threading.Timer | None = None
        self._report_lock = threading.Lock()
        self._request_queue: queue.Queue[str | None] = queue.Queue()
        self._queued_ids: set[str] = set()
        self._queued_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker = threading.Thread(
            target=self._agent_worker,
            name="tabatlas-agent-worker",
            daemon=True,
        )
        self._worker.start()
        with self.database() as connection:
            for request in queued_agent_requests(connection, 100):
                self.enqueue_agent_request(request["id"])

    @contextmanager
    def database(self) -> Iterator[sqlite3.Connection]:
        connection = connect(self.database_path)
        try:
            yield connection
        finally:
            connection.close()

    def enqueue_agent_request(self, request_id: str) -> None:
        self._cancel_agent_idle_stop()
        with self._queued_lock:
            if request_id in self._queued_ids:
                return
            self._queued_ids.add(request_id)
        self._request_queue.put(request_id)

    def regenerate_report(self) -> None:
        with self._report_lock, self.database() as connection:
            generate_report(connection, self.report_dir, self.report_assets, self.state_dir)

    def server_close(self) -> None:
        self._stop_event.set()
        self._cancel_agent_idle_stop()
        self._request_queue.put(None)
        self.agent.stop()
        super().server_close()
        self.lease.close()

    def session_status(self) -> dict[str, Any]:
        with self.database() as connection:
            counts = note_counts(connection)
            pending = connection.execute(
                "SELECT COUNT(*) AS count FROM agent_requests WHERE status IN ('queued', 'running')"
            ).fetchone()["count"]
            failed = connection.execute(
                "SELECT COUNT(*) AS count FROM agent_requests WHERE status='failed'"
            ).fetchone()["count"]
            pending_requests = [
                {
                    "id": row["id"],
                    "resourceId": row["resource_id"] or "",
                    "kind": row["kind"],
                    "errorCode": row["error_code"] or "",
                }
                for row in connection.execute(
                    """
                    SELECT id, resource_id, kind, error_code
                    FROM agent_requests
                    WHERE status IN ('queued', 'running')
                    ORDER BY created_at, rowid LIMIT 100
                    """
                )
            ]
            action_lists = action_list_summaries(connection)
            recent_audits = [
                {
                    "id": row["id"],
                    "resourceId": row["resource_id"],
                    "createdAt": row["created_at"],
                    "undoneAt": row["undone_at"] or "",
                }
                for row in connection.execute(
                    """
                    SELECT id, resource_id, created_at, undone_at
                    FROM semantic_audits ORDER BY created_at DESC, rowid DESC LIMIT 50
                    """
                )
            ]
            pending_proposals = []
            for row in connection.execute(
                """
                SELECT p.id, p.resource_id, ar.response_json
                FROM semantic_proposals p
                LEFT JOIN semantic_decisions d ON d.proposal_id=p.id
                LEFT JOIN agent_requests ar ON ar.id=p.request_id
                WHERE d.id IS NULL
                ORDER BY p.created_at DESC, p.rowid DESC LIMIT 50
                """
            ):
                try:
                    response = json.loads(row["response_json"] or "{}")
                except json.JSONDecodeError:
                    response = {}
                pending_proposals.append(
                    {
                        "id": row["id"],
                        "resourceId": row["resource_id"],
                        "message": str(
                            response.get("message")
                            or response.get("interpretation")
                            or "Codex proposed an organization change."
                        )[:1200],
                    }
                )
        agent = self.agent.status()
        return {
            "interactive": True,
            "csrfToken": self.csrf_token,
            "agent": {
                **agent,
                "ownership": "codex_desktop" if self.agent_handed_off else "workspace",
                "experimentalHost": True,
            },
            "pendingAgentRequests": pending,
            "pendingRequests": pending_requests,
            "failedAgentRequests": failed,
            "resourceNoteCounts": counts,
            "actionLists": action_lists,
            "recentAudits": recent_audits,
            "pendingProposals": pending_proposals,
            "voice": {
                "recording": True,
                "automaticTranscription": False,
                "editableTranscript": True,
            },
        }

    def handoff_agent(self) -> dict[str, Any]:
        self._cancel_agent_idle_stop()
        if not self._ownership_lock.acquire(blocking=False):
            raise ConflictError("Wait for the current Codex turn before opening it in desktop")
        try:
            if not self.agent.thread_id:
                self.agent.start()
            if self.agent.busy:
                raise ConflictError("Wait for the current Codex turn before opening it in desktop")
            deep_link = self.agent.deep_link
            self.agent.stop()
            self.agent_handed_off = True
            return {"ownership": "codex_desktop", "deepLink": deep_link}
        finally:
            self._ownership_lock.release()

    def reclaim_agent(self) -> dict[str, Any]:
        with self._ownership_lock:
            self.agent_handed_off = False
            status = self.agent.start()
        with self.database() as connection:
            for request in queued_agent_requests(connection, 100):
                self.enqueue_agent_request(request["id"])
        self._schedule_agent_idle_stop()
        return {**status, "ownership": "workspace"}

    def _agent_worker(self) -> None:
        while not self._stop_event.is_set():
            request_id = self._request_queue.get()
            if request_id is None:
                return
            try:
                self._process_agent_request(request_id)
            finally:
                with self._queued_lock:
                    self._queued_ids.discard(request_id)

    def _process_agent_request(self, request_id: str) -> None:
        with self._ownership_lock:
            if self.agent_handed_off:
                return
            self._process_agent_request_owned(request_id)
            self._schedule_agent_idle_stop()

    def _cancel_agent_idle_stop(self) -> None:
        with self._agent_idle_lock:
            timer = self._agent_idle_timer
            self._agent_idle_timer = None
        if timer:
            timer.cancel()

    def _schedule_agent_idle_stop(self) -> None:
        self._cancel_agent_idle_stop()
        if self._stop_event.is_set():
            return
        timer = threading.Timer(AGENT_IDLE_SECONDS, self._stop_idle_agent)
        timer.daemon = True
        with self._agent_idle_lock:
            self._agent_idle_timer = timer
        timer.start()

    def _stop_idle_agent(self) -> None:
        with self._agent_idle_lock:
            self._agent_idle_timer = None
        if not self._ownership_lock.acquire(blocking=False):
            return
        try:
            if not self.agent_handed_off and not self.agent.busy and self._request_queue.empty():
                self.agent.stop()
        finally:
            self._ownership_lock.release()

    def _process_agent_request_owned(self, request_id: str) -> None:
        request = None
        with self.database() as connection:
            try:
                request = connection.execute(
                    "SELECT * FROM agent_requests WHERE id=?",
                    (request_id,),
                ).fetchone()
                if not request or request["status"] != "queued":
                    return
                mark_agent_request_running(connection, request_id)
                if request["kind"] == "interpret_note":
                    context = _note_agent_context(
                        connection,
                        request["resource_id"],
                        request["note_id"],
                    )
                    prompt = build_note_prompt(context)
                else:
                    context = _workspace_agent_context(
                        connection,
                        request["resource_id"],
                        str(request["request_text"] or ""),
                    )
                    prompt = build_workspace_prompt(context, str(request["request_text"] or ""))
            except Exception as error:
                if request and request["status"] == "queued":
                    try:
                        mark_agent_request_failed(connection, request_id, _safe_error_code(error))
                    except ValueError:
                        pass
                return

        try:
            response = self.agent.turn(prompt)
            with self.database() as connection:
                result = complete_agent_request(
                    connection,
                    request_id,
                    response,
                    self.agent.thread_id,
                    self.agent.model,
                )
                proposal_id = result.get("proposalId") or ""
                if proposal_id and request["kind"] == "interpret_note":
                    if proposal_can_auto_apply(connection, proposal_id):
                        apply_semantic_proposal(
                            connection,
                            proposal_id,
                            "delegated_user_note",
                            f"auto:{request_id}",
                        )
            self.regenerate_report()
        except (AgentUnavailable, AgentBusy) as error:
            self.agent.stop()
            with self.database() as connection:
                defer_agent_request(connection, request_id, _safe_error_code(error))
        except (ValueError, json.JSONDecodeError) as error:
            with self.database() as connection:
                mark_agent_request_failed(connection, request_id, _safe_error_code(error))


class TabAtlasWorkspaceHandler(SimpleHTTPRequestHandler):
    server_version = "TabAtlasWorkspace/1"

    @property
    def workspace_server(self) -> TabAtlasWorkspaceServer:
        return self.server  # type: ignore[return-value]

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
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
        if urlsplit(self.path).path in {"/", "/index.html", "/app.js", "/app.css"}:
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
        match = re.fullmatch(rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/notes", parsed.path)
        if match:
            with self.workspace_server.database() as connection:
                notes = resource_notes(connection, match.group(1))
            self._send_json({"resourceId": match.group(1), "notes": notes})
            return
        match = re.fullmatch(rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/workspace-state", parsed.path)
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
        match = re.fullmatch(rf"/api/v1/agent-requests/({OPAQUE_ID_PATTERN})", parsed.path)
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
            match = re.fullmatch(rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/notes", path)
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
                self.workspace_server.enqueue_agent_request(result["agentRequestId"])
                self._send_json(result, HTTPStatus.CREATED)
                return
            match = re.fullmatch(rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/voice-notes", path)
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
                self._send_json(result, HTTPStatus.CREATED)
                return
            match = re.fullmatch(rf"/api/v1/notes/({OPAQUE_ID_PATTERN})/transcript", path)
            if match:
                document = self._read_json_body()
                with self.workspace_server.database() as connection:
                    result = add_audio_transcript(
                        connection,
                        match.group(1),
                        document.get("text"),
                        self._idempotency_key(),
                    )
                self.workspace_server.enqueue_agent_request(result["agentRequestId"])
                self._send_json(result)
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
            match = re.fullmatch(rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/remove", path)
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
            match = re.fullmatch(rf"/api/v1/proposals/({OPAQUE_ID_PATTERN})/(accept|reject)", path)
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
            self._send_json({"error": str(error), "code": "conflict"}, HTTPStatus.CONFLICT)
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            self._send_json({"error": str(error), "code": "invalid_request"}, HTTPStatus.BAD_REQUEST)
        except AgentUnavailable as error:
            self._send_json(
                {"error": "Codex is unavailable; saved work remains local.", "code": _safe_error_code(error)},
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
        return secrets.compare_digest(self.headers.get("Host", ""), self.workspace_server.expected_host)

    def _authenticated(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        morsel = cookie.get(COOKIE_NAME)
        return bool(
            morsel
            and secrets.compare_digest(morsel.value, self.workspace_server.session_token)
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
        self.send_header("Content-Type", row["audio_mime"] or mimetypes.guess_type(source.name)[0] or "application/octet-stream")
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
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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


def _note_agent_context(
    connection: sqlite3.Connection,
    resource_id: str,
    note_id: str,
) -> dict[str, Any]:
    resource = _resource_by_id(connection, resource_id)
    note_text = active_note_text(connection, note_id)
    return {
        "resource": _bounded_resource_context(resource),
        "userNote": note_text,
        "existingMemberships": [
            {
                "name": item["name"],
                "kind": item["kind"],
                "parentName": item.get("parentName") or "",
                "authority": item.get("authority") or "legacy_effective",
            }
            for item in resource.get("collections") or []
        ],
        "allowedSpaces": list(SPACE_DEFINITIONS),
        "existingCollections": _existing_collection_index(connection),
    }


def _workspace_agent_context(
    connection: sqlite3.Connection,
    selected_resource_id: str | None,
    request_text: str,
) -> dict[str, Any]:
    resources = library_resources(connection)
    selected = None
    if selected_resource_id:
        selected = next(
            (resource for resource in resources if resource["resourceId"] == selected_resource_id),
            None,
        )
    selected_memberships = {
        str(item.get("name") or "").casefold()
        for item in (selected.get("collections") or [])
    } if selected else set()
    if selected:
        related = []
        for resource in resources:
            if resource["resourceId"] == selected_resource_id:
                continue
            overlap = len(
                selected_memberships
                & {
                    str(item.get("name") or "").casefold()
                    for item in (resource.get("collections") or [])
                }
            )
            if overlap:
                related.append((overlap, str(resource.get("title") or "").casefold(), resource))
        related.sort(key=lambda item: (-item[0], item[1], item[2]["resourceId"]))
        candidates = [item[2] for item in related[:12]]
        retrieval = "selected_and_related"
    else:
        candidates = _rank_workspace_resources(resources, request_text, 24)
        retrieval = "request_terms"
    index = [_bounded_resource_context(resource) for resource in candidates]
    action_lists = [
        {
            "id": item["id"],
            "name": item["name"],
            "description": item["description"],
            "objective": item["objective"],
            "workflowKind": item["workflowKind"],
            "resourceCount": item["resourceCount"],
            "stateCounts": item["stateCounts"],
        }
        for item in action_list_summaries(connection)
    ]
    return {
        "inventory": inventory(connection),
        "selectedResource": _bounded_resource_context(selected) if selected else None,
        "actionLists": action_lists,
        "collections": _existing_collection_index(connection),
        "resourceIndex": index,
        "resourceIndexRetrieval": retrieval,
        "resourceIndexMatched": len(index),
        "libraryResourceCount": len(resources),
        "resourceIndexTruncated": len(resources) > len(index),
    }


def _resource_by_id(connection: sqlite3.Connection, resource_id: str) -> dict[str, Any]:
    resource = next(
        (item for item in library_resources(connection) if item["resourceId"] == resource_id),
        None,
    )
    if not resource:
        raise ValueError("Accepted resource was not found")
    return resource


def _bounded_resource_context(resource: dict[str, Any] | None) -> dict[str, Any] | None:
    if not resource:
        return None
    presentation = resource_presentation(resource)
    return {
        "resourceId": resource["resourceId"],
        "title": str(resource.get("title") or "")[:300],
        "brief": str(resource.get("brief") or "")[:600],
        "detail": str(resource.get("detail") or "")[:800],
        "whyKept": str(resource.get("whyKept") or "")[:500],
        "nextAction": str(resource.get("nextAction") or "")[:300],
        "host": str(resource.get("host") or "")[:200],
        "kind": str(resource.get("kind") or "")[:100],
        "source": presentation["source"],
        "format": presentation["format"],
        "noteCount": int(resource.get("noteCount") or 0),
        "memberships": [
            {"name": item["name"], "kind": item["kind"]}
            for item in (resource.get("collections") or [])[:12]
        ],
        "actionItems": (resource.get("actionItems") or [])[:6],
    }


def _rank_workspace_resources(
    resources: list[dict[str, Any]],
    request_text: str,
    limit: int,
) -> list[dict[str, Any]]:
    stop_words = {
        "about", "all", "and", "ask", "can", "codex", "find", "for", "from",
        "help", "library", "me", "my", "of", "on", "resource", "resources",
        "show", "tab", "tabs", "the", "this", "to", "what", "with",
    }
    folded_request = request_text.casefold()
    terms = {
        value
        for value in re.findall(r"[^\W_]{2,}", folded_request, flags=re.UNICODE)
        if value not in stop_words
    }
    if not terms:
        return []
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for resource in resources:
        presentation = resource_presentation(resource)
        fields = [
            str(resource.get("title") or ""),
            str(resource.get("brief") or ""),
            str(resource.get("nextAction") or ""),
            str(resource.get("host") or ""),
            str(presentation.get("source") or ""),
            str(presentation.get("format") or ""),
            " ".join(str(item.get("name") or "") for item in resource.get("collections") or []),
        ]
        haystack = "\n".join(fields).casefold()
        score = sum(4 if term in fields[0].casefold() else 1 for term in terms if term in haystack)
        if score:
            ranked.append((score, str(resource.get("title") or "").casefold(), resource))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]["resourceId"]))
    return [item[2] for item in ranked[: max(1, min(limit, 40))]]


def _existing_collection_index(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "name": row["name"],
            "kind": row["kind"],
            "parentName": row["parentName"] or "",
            "resourceCount": row["resourceCount"],
        }
        for row in connection.execute(
            """
            SELECT c.name, c.kind, parent.name AS parentName,
                   COUNT(rc.resource_id) AS resourceCount
            FROM collections c
            LEFT JOIN collections parent ON parent.id=c.parent_id
            LEFT JOIN resource_collections rc ON rc.collection_id=c.id
            WHERE c.status='active'
            GROUP BY c.id
            ORDER BY c.kind, resourceCount DESC, c.name COLLATE NOCASE
            LIMIT 160
            """
        )
    ]


def _safe_error_code(error: BaseException) -> str:
    text = str(error).casefold()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:80] or "agent_unavailable"


class WorkspaceLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+b")
        self.handle.seek(0)
        if not self.handle.read(1):
            self.handle.seek(0)
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            raise ValueError("Another TabAtlas workspace session is already running") from error
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()).encode("ascii"))
        self.handle.flush()

    def close(self) -> None:
        if self.handle.closed:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            try:
                self.path.unlink()
            except OSError:
                pass
