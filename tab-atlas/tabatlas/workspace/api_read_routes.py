from __future__ import annotations

import mimetypes
import re
from http import HTTPStatus

from .agent_requests import agent_request_detail
from .api_contract import ApiRequest
from .context import _resource_by_id
from .notes import resource_notes
from .server_constants import OPAQUE_ID_PATTERN, RESOURCE_ID_PATTERN


def handle_read_get(request: ApiRequest, path: str) -> bool:
    server = request.workspace_server
    if path == "/api/v1/session":
        request._send_json(server.session_status())
        return True
    if path == "/api/v1/catalog":
        request._send_json(server.catalog_snapshot())
        return True
    if path == "/api/v1/browser-sync":
        request._send_json(server.browser_sync.status())
        return True
    match = re.fullmatch(rf"/api/v1/tab-closures/({OPAQUE_ID_PATTERN})", path)
    if match:
        request._send_json(server.tab_closure.status(match.group(1)))
        return True
    match = re.fullmatch(rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/notes", path)
    if match:
        with server.database() as connection:
            notes = resource_notes(connection, match.group(1))
        request._send_json({"resourceId": match.group(1), "notes": notes})
        return True
    match = re.fullmatch(
        rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/workspace-state", path
    )
    if match:
        with server.database() as connection:
            resource = _resource_by_id(connection, match.group(1))
            notes = resource_notes(connection, match.group(1))
        request._send_json(
            {
                "resourceId": match.group(1),
                "semanticRevision": resource.get("semanticRevision", 0),
                "noteCount": resource.get("noteCount", 0),
                "collections": resource.get("collections") or [],
                "actionItems": resource.get("actionItems") or [],
                "notes": notes,
            }
        )
        return True
    match = re.fullmatch(rf"/api/v1/agent-requests/({OPAQUE_ID_PATTERN})", path)
    if match:
        with server.database() as connection:
            result = agent_request_detail(connection, match.group(1))
        request._send_json(result)
        return True
    match = re.fullmatch(rf"/api/v1/notes/({OPAQUE_ID_PATTERN})/audio", path)
    if match:
        request._serve_audio(match.group(1))
        return True
    return False


def serve_audio(request: ApiRequest, note_id: str) -> None:
    server = request.workspace_server
    with server.database() as connection:
        row = connection.execute(
            "SELECT audio_relpath, audio_mime, audio_sha256 FROM resource_notes WHERE id=? AND kind='audio'",
            (note_id,),
        ).fetchone()
    if not row:
        request.send_error(HTTPStatus.NOT_FOUND)
        return
    source = (server.state_dir / row["audio_relpath"]).resolve()
    try:
        source.relative_to(server.state_dir)
    except ValueError:
        request.send_error(HTTPStatus.NOT_FOUND)
        return
    if not source.is_file():
        request.send_error(HTTPStatus.NOT_FOUND)
        return
    size = source.stat().st_size
    start, end = 0, size - 1
    status = HTTPStatus.OK
    range_header = request.headers.get("Range", "")
    if range_header:
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
        if not match:
            request.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else end
        if start > end or start >= size or end >= size:
            request.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return
        status = HTTPStatus.PARTIAL_CONTENT
    request.send_response(status)
    request.send_header(
        "Content-Type",
        row["audio_mime"]
        or mimetypes.guess_type(source.name)[0]
        or "application/octet-stream",
    )
    request.send_header("Accept-Ranges", "bytes")
    request.send_header("Content-Length", str(end - start + 1))
    if status == HTTPStatus.PARTIAL_CONTENT:
        request.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    request.end_headers()
    with source.open("rb") as stream:
        stream.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = stream.read(min(64 * 1024, remaining))
            if not chunk:
                break
            request.wfile.write(chunk)
            remaining -= len(chunk)
