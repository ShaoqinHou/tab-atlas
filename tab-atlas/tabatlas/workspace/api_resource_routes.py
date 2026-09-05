from __future__ import annotations

import re
from http import HTTPStatus

from ..catalog import remove_library_resources
from .api_contract import ApiRequest
from .directory import update_action_progress
from .notes import (
    create_note_analysis_request,
    create_text_note,
    set_note_active,
)
from .server_constants import OPAQUE_ID_PATTERN, RESOURCE_ID_PATTERN
from .transcription import add_audio_transcript, create_audio_note


def handle_resource_post(request: ApiRequest, path: str) -> bool:
    server = request.workspace_server
    match = re.fullmatch(rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/notes", path)
    if match:
        document = request._read_json_body()
        with server.database() as connection:
            result = create_text_note(
                connection,
                match.group(1),
                document.get("text"),
                request._idempotency_key(),
                supersedes_note_id=document.get("supersedesNoteId") or None,
            )
        request._send_json(result, HTTPStatus.CREATED)
        return True
    match = re.fullmatch(
        rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/voice-notes", path
    )
    if match:
        payload = request._read_raw_body(25 * 1024 * 1024)
        duration_header = request.headers.get("X-TabAtlas-Audio-Duration-Ms")
        duration = int(duration_header) if duration_header else None
        with server.database() as connection:
            result = create_audio_note(
                connection,
                server.state_dir,
                match.group(1),
                payload,
                request.headers.get_content_type(),
                duration,
                request._idempotency_key(),
            )
        server.enqueue_audio_transcription(result["id"])
        request._send_json(result, HTTPStatus.CREATED)
        return True
    match = re.fullmatch(rf"/api/v1/notes/({OPAQUE_ID_PATTERN})/transcript", path)
    if match:
        document = request._read_json_body()
        with server.database() as connection:
            result = add_audio_transcript(
                connection,
                match.group(1),
                document.get("text"),
                request._idempotency_key(),
            )
        request._send_json(result)
        return True
    match = re.fullmatch(rf"/api/v1/notes/({OPAQUE_ID_PATTERN})/analyze", path)
    if match:
        request._read_empty_body()
        with server.database() as connection:
            result = create_note_analysis_request(
                connection,
                match.group(1),
                request._idempotency_key(),
            )
        server.enqueue_agent_request(result["id"])
        request._send_json(result, HTTPStatus.ACCEPTED)
        return True
    match = re.fullmatch(rf"/api/v1/notes/({OPAQUE_ID_PATTERN})/retract", path)
    if match:
        request._read_empty_body()
        with server.database() as connection:
            result = set_note_active(
                connection,
                match.group(1),
                False,
                request._idempotency_key(),
            )
        server.regenerate_report()
        request._send_json(result)
        return True
    match = re.fullmatch(
        rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/action-lists/({OPAQUE_ID_PATTERN})/progress",
        path,
    )
    if match:
        document = request._read_json_body()
        with server.database() as connection:
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
                request_id=request._idempotency_key(),
            )
        server.regenerate_report()
        request._send_json(result)
        return True
    match = re.fullmatch(rf"/api/v1/resources/({RESOURCE_ID_PATTERN})/remove", path)
    if not match:
        return False
    request._read_empty_body()
    with server.database() as connection:
        result = remove_library_resources(connection, [match.group(1)])
    server.regenerate_report()
    request._send_json(result)
    return True
