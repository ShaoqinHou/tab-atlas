from __future__ import annotations

import sqlite3
from typing import Any

from ..database import utc_now
from .constants import PROMPT_VERSION
from .support import (
    ConflictError,
    _bounded_label,
    _insert_agent_request,
    _json_or_none,
    _new_id,
    _require_resource,
    _sha256_text,
    _validate_idempotency_key,
    _validate_note_text,
)


def create_text_note(
    connection: sqlite3.Connection,
    resource_id: str,
    text: str,
    idempotency_key: str,
    source_surface: str = "workspace",
    supersedes_note_id: str | None = None,
) -> dict[str, Any]:
    _require_resource(connection, resource_id)
    exact_text = _validate_note_text(text)
    request_key = _validate_idempotency_key(idempotency_key)
    existing = connection.execute(
        """
        SELECT id, resource_id, kind, text_body, supersedes_note_id
        FROM resource_notes WHERE idempotency_key=?
        """,
        (request_key,),
    ).fetchone()
    if existing:
        if (
            existing["resource_id"] != resource_id
            or existing["kind"] != "text"
            or existing["text_body"] != exact_text
            or (existing["supersedes_note_id"] or None) != supersedes_note_id
        ):
            raise ConflictError("Idempotency key was already used for another note")
        return note_detail(connection, existing["id"])
    if supersedes_note_id:
        row = connection.execute(
            "SELECT resource_id FROM resource_notes WHERE id=?",
            (supersedes_note_id,),
        ).fetchone()
        if not row or row["resource_id"] != resource_id:
            raise ValueError("Superseded note does not belong to this resource")

    note_id = _new_id("note")
    run_id = _new_id("run")
    now = utc_now()
    input_hash = _sha256_text(exact_text)
    with connection:
        connection.execute(
            """
            INSERT INTO resource_notes(
              id, resource_id, kind, text_body, source_surface,
              supersedes_note_id, idempotency_key, created_at
            ) VALUES(?, ?, 'text', ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                resource_id,
                exact_text,
                _bounded_label(source_surface, 40, "workspace"),
                supersedes_note_id,
                request_key,
                now,
            ),
        )
        _append_processing_event(
            connection,
            note_id,
            run_id,
            "transcription",
            "not_required",
            input_hash,
            engine="user_text",
        )
        _append_processing_event(
            connection,
            note_id,
            run_id,
            "interpretation",
            "not_required",
            input_hash,
            engine="awaiting_user_request",
            prompt_version=PROMPT_VERSION,
        )
    return note_detail(connection, note_id)


def create_note_analysis_request(
    connection: sqlite3.Connection,
    note_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    from .agent_requests import agent_request_detail

    note = note_detail(connection, note_id)
    note_text = active_note_text(connection, note_id)
    request_key = _validate_idempotency_key(idempotency_key)
    input_hash = _sha256_text(note_text)
    marker_prefix = f"analyze:{request_key}:"
    marker = f"{marker_prefix}{input_hash}"
    existing = connection.execute(
        """
        SELECT id, request_text FROM agent_requests
        WHERE note_id=? AND substr(request_text, 1, ?)=?
        ORDER BY created_at DESC, rowid DESC LIMIT 1
        """,
        (note_id, len(marker_prefix), marker_prefix),
    ).fetchone()
    if existing:
        if existing["request_text"] != marker:
            raise ConflictError(
                "Idempotency key was already used for another note analysis"
            )
        return agent_request_detail(connection, existing["id"])
    active = connection.execute(
        """
        SELECT id, request_text FROM agent_requests
        WHERE note_id=? AND status IN ('queued', 'running')
        ORDER BY created_at DESC, rowid DESC LIMIT 1
        """,
        (note_id,),
    ).fetchone()
    if active and str(active["request_text"] or "").endswith(f":{input_hash}"):
        return agent_request_detail(connection, active["id"])
    with connection:
        _append_processing_event(
            connection,
            note_id,
            _new_id("run"),
            "interpretation",
            "queued",
            input_hash,
            engine="codex_app_server",
            prompt_version=PROMPT_VERSION,
        )
        request_id = _insert_agent_request(
            connection,
            "interpret_note",
            resource_id=note["resourceId"],
            note_id=note_id,
            request_text=marker,
        )
    return agent_request_detail(connection, request_id)


def note_detail(connection: sqlite3.Connection, note_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM resource_notes WHERE id=?",
        (note_id,),
    ).fetchone()
    if not row:
        raise ValueError("Note was not found")
    events = connection.execute(
        "SELECT * FROM note_events WHERE note_id=? ORDER BY created_at, rowid",
        (note_id,),
    ).fetchall()
    active = not events or events[-1]["event"] == "restore"
    processing: dict[str, dict[str, Any]] = {}
    for event in connection.execute(
        """
        SELECT * FROM note_processing_events
        WHERE note_id=? ORDER BY created_at, sequence, rowid
        """,
        (note_id,),
    ):
        processing[event["stage"]] = {
            "state": event["state"],
            "outputText": event["output_text"] or "",
            "output": _json_or_none(event["output_json"]),
            "engine": event["engine"] or "",
            "model": event["model"] or "",
            "errorCode": event["error_code"] or "",
            "createdAt": event["created_at"],
        }
    analysis_row = connection.execute(
        """
        SELECT * FROM agent_requests
        WHERE note_id=?
        ORDER BY created_at DESC, rowid DESC LIMIT 1
        """,
        (note_id,),
    ).fetchone()
    analysis: dict[str, Any] | None = None
    if analysis_row:
        proposal = connection.execute(
            "SELECT id, input_hash, base_revision FROM semantic_proposals WHERE request_id=?",
            (analysis_row["id"],),
        ).fetchone()
        decision = None
        if proposal:
            decision = connection.execute(
                "SELECT decision, audit_id FROM semantic_decisions WHERE proposal_id=?",
                (proposal["id"],),
            ).fetchone()
        current_text = (
            str(row["text_body"] or "")
            if row["kind"] == "text"
            else str(processing.get("transcription", {}).get("outputText") or "")
        )
        current_hash = _sha256_text(current_text) if current_text else ""
        request_marker = str(analysis_row["request_text"] or "")
        resource_revision = connection.execute(
            "SELECT semantic_revision FROM resources WHERE id=?",
            (row["resource_id"],),
        ).fetchone()["semantic_revision"]
        analysis = {
            "requestId": analysis_row["id"],
            "status": analysis_row["status"],
            "response": _json_or_none(analysis_row["response_json"]),
            "errorCode": analysis_row["error_code"] or "",
            "proposalId": proposal["id"] if proposal else "",
            "decision": decision["decision"] if decision else "",
            "auditId": decision["audit_id"]
            if decision and decision["audit_id"]
            else "",
            "inputCurrent": bool(
                current_hash
                and (
                    request_marker.endswith(f":{current_hash}")
                    or (proposal and proposal["input_hash"] == current_hash)
                )
                and (not proposal or proposal["base_revision"] == resource_revision)
            ),
        }
    return {
        "id": row["id"],
        "resourceId": row["resource_id"],
        "kind": row["kind"],
        "text": row["text_body"] or "",
        "hasAudio": bool(row["audio_relpath"]),
        "audioMime": row["audio_mime"] or "",
        "audioBytes": row["audio_bytes"],
        "audioDurationMs": row["audio_duration_ms"],
        "audioSha256": row["audio_sha256"] or "",
        "supersedesNoteId": row["supersedes_note_id"] or "",
        "createdAt": row["created_at"],
        "active": active,
        "processing": processing,
        "analysis": analysis,
    }


def resource_notes(
    connection: sqlite3.Connection,
    resource_id: str,
    include_retracted: bool = False,
) -> list[dict[str, Any]]:
    _require_resource(connection, resource_id)
    result = [
        note_detail(connection, row["id"])
        for row in connection.execute(
            "SELECT id FROM resource_notes WHERE resource_id=? ORDER BY created_at DESC, rowid DESC",
            (resource_id,),
        )
    ]
    return result if include_retracted else [note for note in result if note["active"]]


def set_note_active(
    connection: sqlite3.Connection,
    note_id: str,
    active: bool,
    request_id: str,
    actor: str = "workspace_user",
) -> dict[str, Any]:
    request_key = _validate_idempotency_key(request_id)
    existing = connection.execute(
        "SELECT note_id FROM note_events WHERE request_id=?",
        (request_key,),
    ).fetchone()
    if existing:
        if existing["note_id"] != note_id:
            raise ConflictError(
                "Idempotency key was already used for another note event"
            )
        return note_detail(connection, existing["note_id"])
    note = note_detail(connection, note_id)
    if note["active"] == bool(active):
        return note
    event = "restore" if active else "retract"
    now = utc_now()
    with connection:
        connection.execute(
            """
            INSERT INTO note_events(id, note_id, event, actor, request_id, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("noteevent"),
                note_id,
                event,
                _bounded_label(actor, 60, "workspace_user"),
                request_key,
                now,
            ),
        )
        if not active:
            connection.execute(
                """
                UPDATE agent_requests
                SET status='failed', error_code='note_retracted', completed_at=?
                WHERE note_id=? AND status IN ('queued', 'running')
                """,
                (now, note_id),
            )
            _append_processing_event(
                connection,
                note_id,
                _new_id("run"),
                "interpretation",
                "stale",
                _note_input_hash(note),
                engine="workspace_user",
                prompt_version=PROMPT_VERSION,
                error_code="note_retracted",
            )
    return note_detail(connection, note_id)


def active_note_text(connection: sqlite3.Connection, note_id: str) -> str:
    note = note_detail(connection, note_id)
    if not note["active"]:
        raise ValueError("Note has been retracted")
    if note["kind"] == "text":
        return str(note["text"])
    transcript = note.get("processing", {}).get("transcription", {})
    if transcript.get("state") != "succeeded" or not transcript.get("outputText"):
        raise ValueError(
            "Voice note needs an editable transcript before interpretation"
        )
    return str(transcript["outputText"])


def note_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        row["resource_id"]: int(row["note_count"])
        for row in connection.execute(
            """
            SELECT n.resource_id, COUNT(*) AS note_count
            FROM resource_notes n
            WHERE COALESCE((
              SELECT e.event FROM note_events e
              WHERE e.note_id=n.id
              ORDER BY e.created_at DESC, e.rowid DESC LIMIT 1
            ), 'restore')='restore'
            GROUP BY n.resource_id
            """
        )
    }


def _note_input_hash(note: dict[str, Any]) -> str:
    if note["kind"] == "text":
        return _sha256_text(str(note.get("text") or ""))
    transcript = note.get("processing", {}).get("transcription", {})
    if transcript.get("outputText"):
        return _sha256_text(str(transcript["outputText"]))
    return str(note.get("audioSha256") or _sha256_text(note["id"]))


def _cancel_pending_note_analysis(
    connection: sqlite3.Connection,
    note_id: str,
    error_code: str,
) -> None:
    note = note_detail(connection, note_id)
    changed = connection.execute(
        """
        UPDATE agent_requests
        SET status='failed', error_code=?, completed_at=?
        WHERE note_id=? AND status IN ('queued', 'running')
        """,
        (_bounded_label(error_code, 80, "note_changed"), utc_now(), note_id),
    ).rowcount
    if changed:
        _append_processing_event(
            connection,
            note_id,
            _new_id("run"),
            "interpretation",
            "stale",
            _note_input_hash(note),
            engine="workspace_user",
            prompt_version=PROMPT_VERSION,
            error_code=_bounded_label(error_code, 80, "note_changed"),
        )


def _latest_active_note_id(connection: sqlite3.Connection, resource_id: str) -> str:
    for row in connection.execute(
        "SELECT id FROM resource_notes WHERE resource_id=? ORDER BY created_at DESC, rowid DESC",
        (resource_id,),
    ):
        if note_detail(connection, row["id"])["active"]:
            return str(row["id"])
    return ""


def _append_processing_event(
    connection: sqlite3.Connection,
    note_id: str,
    run_id: str,
    stage: str,
    state: str,
    input_hash: str,
    *,
    output_text: str | None = None,
    output_json: str | None = None,
    engine: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    error_code: str | None = None,
) -> None:
    sequence = connection.execute(
        "SELECT COALESCE(MAX(sequence), -1) + 1 AS value FROM note_processing_events WHERE note_id=? AND run_id=? AND stage=?",
        (note_id, run_id, stage),
    ).fetchone()["value"]
    connection.execute(
        """
        INSERT INTO note_processing_events(
          id, note_id, run_id, stage, state, sequence, input_hash,
          output_text, output_json, engine, model, prompt_version,
          error_code, created_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _new_id("processing"),
            note_id,
            run_id,
            stage,
            state,
            sequence,
            input_hash,
            output_text,
            output_json,
            engine,
            model,
            prompt_version,
            error_code,
            utc_now(),
        ),
    )
