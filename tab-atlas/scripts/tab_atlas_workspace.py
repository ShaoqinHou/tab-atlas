from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from tab_atlas_core import utc_now


MAX_NOTE_BYTES = 32 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_AUDIO_DURATION_MS = 10 * 60 * 1000
PROMPT_VERSION = "note-interpretation-v1"
PRIMARY_COLLECTION_KINDS = {"space", "topic", "focus"}
OVERLAY_COLLECTION_KINDS = {"project", "action_list"}
COLLECTION_KINDS = PRIMARY_COLLECTION_KINDS | OVERLAY_COLLECTION_KINDS
AUDIO_SIGNATURES = {
    "audio/wav": lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE",
    "audio/webm": lambda data: data.startswith(b"\x1aE\xdf\xa3"),
    "audio/ogg": lambda data: data.startswith(b"OggS"),
    "audio/mp4": lambda data: len(data) >= 12 and data[4:8] == b"ftyp",
}
AUDIO_SUFFIXES = {
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
}


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
            "queued",
            input_hash,
            engine="codex_app_server",
            prompt_version=PROMPT_VERSION,
        )
        request_id = _insert_agent_request(
            connection,
            "interpret_note",
            resource_id=resource_id,
            note_id=note_id,
            request_text=None,
        )
    result = note_detail(connection, note_id)
    result["agentRequestId"] = request_id
    return result


def create_audio_note(
    connection: sqlite3.Connection,
    state_dir: Path,
    resource_id: str,
    payload: bytes,
    mime_type: str,
    duration_ms: int | None,
    idempotency_key: str,
    source_surface: str = "workspace",
) -> dict[str, Any]:
    _require_resource(connection, resource_id)
    request_key = _validate_idempotency_key(idempotency_key)
    canonical_mime = str(mime_type or "").split(";", 1)[0].strip().casefold()
    if canonical_mime not in AUDIO_SIGNATURES:
        raise ValueError("Voice note must be WAV, WebM, Ogg, or M4A audio")
    if not 0 < len(payload) <= MAX_AUDIO_BYTES:
        raise ValueError("Voice note must be between 1 byte and 25 MB")
    if not AUDIO_SIGNATURES[canonical_mime](payload):
        raise ValueError("Voice note bytes do not match the declared audio type")
    if duration_ms is not None and not 0 <= duration_ms <= MAX_AUDIO_DURATION_MS:
        raise ValueError("Voice note duration must not exceed 10 minutes")
    digest = hashlib.sha256(payload).hexdigest()
    existing = connection.execute(
        """
        SELECT id, resource_id, kind, audio_mime, audio_sha256
        FROM resource_notes WHERE idempotency_key=?
        """,
        (request_key,),
    ).fetchone()
    if existing:
        if (
            existing["resource_id"] != resource_id
            or existing["kind"] != "audio"
            or existing["audio_mime"] != canonical_mime
            or existing["audio_sha256"] != digest
        ):
            raise ConflictError("Idempotency key was already used for another voice note")
        return note_detail(connection, existing["id"])

    note_id = _new_id("note")
    suffix = AUDIO_SUFFIXES[canonical_mime]
    audio_root = state_dir.resolve() / "notes" / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    target = audio_root / f"{note_id}{suffix}"
    _atomic_private_write(target, payload)
    relative_path = str(target.relative_to(state_dir.resolve()))
    now = utc_now()
    run_id = _new_id("run")
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO resource_notes(
                  id, resource_id, kind, audio_relpath, audio_mime, audio_sha256,
                  audio_bytes, audio_duration_ms, source_surface,
                  idempotency_key, created_at
                ) VALUES(?, ?, 'audio', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    resource_id,
                    relative_path,
                    canonical_mime,
                    digest,
                    len(payload),
                    duration_ms,
                    _bounded_label(source_surface, 40, "workspace"),
                    request_key,
                    now,
                ),
            )
            _append_processing_event(
                connection,
                note_id,
                run_id,
                "transcription",
                "queued",
                digest,
                engine="awaiting_transcript",
            )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return note_detail(connection, note_id)


def add_audio_transcript(
    connection: sqlite3.Connection,
    note_id: str,
    transcript: str,
    idempotency_key: str,
) -> dict[str, Any]:
    note = connection.execute(
        "SELECT * FROM resource_notes WHERE id=?",
        (note_id,),
    ).fetchone()
    if not note or note["kind"] != "audio":
        raise ValueError("Voice note was not found")
    exact_text = _validate_note_text(transcript)
    request_key = _validate_idempotency_key(idempotency_key)
    input_hash = _sha256_text(exact_text)
    request_marker = f"transcript:{request_key}:{input_hash}"
    existing = connection.execute(
        """
        SELECT id, request_text FROM agent_requests
        WHERE note_id=? AND (request_text=? OR request_text LIKE ?)
        """,
        (note_id, f"transcript:{request_key}", f"transcript:{request_key}:%"),
    ).fetchone()
    if existing:
        if existing["request_text"] not in {f"transcript:{request_key}", request_marker}:
            raise ConflictError("Idempotency key was already used for another transcript")
        result = note_detail(connection, note_id)
        result["agentRequestId"] = existing["id"]
        return result
    run_id = _new_id("run")
    with connection:
        _append_processing_event(
            connection,
            note_id,
            run_id,
            "transcription",
            "succeeded",
            str(note["audio_sha256"]),
            output_text=exact_text,
            engine="user_edited_transcript",
        )
        _append_processing_event(
            connection,
            note_id,
            run_id,
            "interpretation",
            "queued",
            input_hash,
            engine="codex_app_server",
            prompt_version=PROMPT_VERSION,
        )
        request_id = _insert_agent_request(
            connection,
            "interpret_note",
            resource_id=str(note["resource_id"]),
            note_id=note_id,
            request_text=request_marker,
        )
    result = note_detail(connection, note_id)
    result["agentRequestId"] = request_id
    return result


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
            raise ConflictError("Idempotency key was already used for another note event")
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
        raise ValueError("Voice note needs an editable transcript before interpretation")
    return str(transcript["outputText"])


def note_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    rows = connection.execute(
        "SELECT resource_id, id FROM resource_notes ORDER BY created_at, rowid"
    ).fetchall()
    for row in rows:
        if note_detail(connection, row["id"])["active"]:
            counts[row["resource_id"]] = counts.get(row["resource_id"], 0) + 1
    return counts


def queued_agent_requests(connection: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM agent_requests
        WHERE status='queued' ORDER BY created_at, rowid LIMIT ?
        """,
        (max(1, min(limit, 100)),),
    ).fetchall()
    return [dict(row) for row in rows]


def create_workspace_chat_request(
    connection: sqlite3.Connection,
    text: str,
    resource_id: str | None,
) -> dict[str, Any]:
    message = _validate_note_text(text)
    if resource_id:
        _require_resource(connection, resource_id)
    with connection:
        request_id = _insert_agent_request(
            connection,
            "workspace_chat",
            resource_id=resource_id,
            note_id=None,
            request_text=message,
        )
    return agent_request_detail(connection, request_id)


def mark_agent_request_running(connection: sqlite3.Connection, request_id: str) -> None:
    with connection:
        changed = connection.execute(
            """
            UPDATE agent_requests SET status='running', started_at=?, error_code=NULL
            WHERE id=? AND status='queued'
            """,
            (utc_now(), request_id),
        ).rowcount
    if not changed:
        raise ValueError("Agent request is not queued")


def mark_agent_request_failed(
    connection: sqlite3.Connection,
    request_id: str,
    error_code: str,
) -> None:
    row = connection.execute(
        "SELECT note_id, status FROM agent_requests WHERE id=?",
        (request_id,),
    ).fetchone()
    if not row:
        raise ValueError("Agent request was not found")
    if row["status"] not in {"queued", "running"}:
        return
    with connection:
        changed = connection.execute(
            """
            UPDATE agent_requests
            SET status='failed', error_code=?, completed_at=?
            WHERE id=? AND status IN ('queued', 'running')
            """,
            (_bounded_label(error_code, 80, "agent_unavailable"), utc_now(), request_id),
        ).rowcount
        if changed and row["note_id"]:
            note = note_detail(connection, row["note_id"])
            _append_processing_event(
                connection,
                row["note_id"],
                _new_id("run"),
                "interpretation",
                "failed",
                _note_input_hash(note),
                engine="codex_app_server",
                prompt_version=PROMPT_VERSION,
                error_code=_bounded_label(error_code, 80, "agent_unavailable"),
            )


def defer_agent_request(
    connection: sqlite3.Connection,
    request_id: str,
    error_code: str,
) -> None:
    with connection:
        changed = connection.execute(
            """
            UPDATE agent_requests
            SET status='queued', error_code=?, started_at=NULL
            WHERE id=? AND status='running'
            """,
            (_bounded_label(error_code, 80, "agent_unavailable"), request_id),
        ).rowcount
    if not changed:
        raise ValueError("Agent request is not running")


def complete_agent_request(
    connection: sqlite3.Connection,
    request_id: str,
    response: dict[str, Any],
    thread_id: str,
    model: str,
) -> dict[str, Any]:
    request = connection.execute(
        "SELECT * FROM agent_requests WHERE id=?",
        (request_id,),
    ).fetchone()
    if not request or request["status"] != "running":
        raise ValueError("Agent request is not running")
    normalized = normalize_agent_response(response)
    proposal_id = ""
    interpretation = normalized["interpretation"]
    with connection:
        connection.execute(
            """
            UPDATE agent_requests
            SET status='completed', response_json=?, thread_id=?, completed_at=?
            WHERE id=?
            """,
            (_canonical_json(normalized), thread_id, utc_now(), request_id),
        )
        if request["note_id"]:
            note_text = active_note_text(connection, request["note_id"])
            _append_processing_event(
                connection,
                request["note_id"],
                _new_id("run"),
                "interpretation",
                "succeeded",
                _sha256_text(note_text),
                output_text=interpretation,
                output_json=_canonical_json(normalized),
                engine="codex_app_server",
                model=model,
                prompt_version=PROMPT_VERSION,
            )
        if request["resource_id"] and _response_has_semantic_operations(normalized):
            revision = connection.execute(
                "SELECT semantic_revision FROM resources WHERE id=?",
                (request["resource_id"],),
            ).fetchone()["semantic_revision"]
            proposal_id = _new_id("proposal")
            connection.execute(
                """
                INSERT INTO semantic_proposals(
                  id, resource_id, note_id, request_id, base_revision, proposer,
                  model, thread_id, rationale, operations_json, evidence_json,
                  input_hash, prompt_version, created_at
                ) VALUES(?, ?, ?, ?, ?, 'codex', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    request["resource_id"],
                    request["note_id"],
                    request_id,
                    revision,
                    model,
                    thread_id,
                    normalized["message"],
                    _canonical_json(normalized),
                    _canonical_json(
                        [{"kind": "user_note", "id": request["note_id"]}]
                        if request["note_id"] else []
                    ),
                    _sha256_text(
                        active_note_text(connection, request["note_id"])
                        if request["note_id"] else str(request["request_text"] or "")
                    ),
                    PROMPT_VERSION,
                    utc_now(),
                ),
            )
    result = agent_request_detail(connection, request_id)
    result["proposalId"] = proposal_id
    return result


def agent_request_detail(connection: sqlite3.Connection, request_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM agent_requests WHERE id=?",
        (request_id,),
    ).fetchone()
    if not row:
        raise ValueError("Agent request was not found")
    proposal = connection.execute(
        "SELECT id FROM semantic_proposals WHERE request_id=?",
        (request_id,),
    ).fetchone()
    decision = None
    if proposal:
        decision = connection.execute(
            "SELECT * FROM semantic_decisions WHERE proposal_id=?",
            (proposal["id"],),
        ).fetchone()
    return {
        "id": row["id"],
        "kind": row["kind"],
        "resourceId": row["resource_id"] or "",
        "noteId": row["note_id"] or "",
        "status": row["status"],
        "response": _json_or_none(row["response_json"]),
        "errorCode": row["error_code"] or "",
        "threadId": row["thread_id"] or "",
        "proposalId": proposal["id"] if proposal else "",
        "decision": decision["decision"] if decision else "",
        "auditId": decision["audit_id"] if decision and decision["audit_id"] else "",
        "createdAt": row["created_at"],
        "completedAt": row["completed_at"] or "",
    }


def normalize_agent_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Codex response must be an object")
    message = _bounded_text(value.get("message"), 1200, "Codex interpreted the request.")
    interpretation = _bounded_text(value.get("interpretation"), 3000, message)
    confidence = value.get("confidence", 0.5)
    try:
        confidence_number = float(confidence)
    except (TypeError, ValueError):
        confidence_number = 0.5
    confidence_number = min(1.0, max(0.0, confidence_number))

    memberships = []
    seen: set[tuple[str, str]] = set()
    primary_counts: dict[str, int] = {}
    for raw in value.get("memberships") or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip().casefold()
        if kind not in COLLECTION_KINDS:
            continue
        name = _collection_name(raw.get("name"))
        key = (kind, name.casefold())
        if key in seen:
            continue
        seen.add(key)
        if kind in PRIMARY_COLLECTION_KINDS:
            primary_counts[kind] = primary_counts.get(kind, 0) + 1
            if primary_counts[kind] > 1:
                raise ValueError(f"Codex proposed more than one primary {kind}")
        memberships.append(
            {
                "name": name,
                "kind": kind,
                "parentName": _optional_collection_name(raw.get("parentName")),
                "role": _bounded_label(raw.get("role"), 50, "reference"),
                "reason": _bounded_text(raw.get("reason"), 1000, interpretation),
            }
        )
        if len(memberships) >= 12:
            break

    action_item = None
    raw_action = value.get("actionItem")
    if isinstance(raw_action, dict) and raw_action.get("listName"):
        list_name = _collection_name(raw_action.get("listName"))
        state = str(raw_action.get("state") or "queued").casefold()
        if state not in {"queued", "in_progress", "completed", "snoozed", "skipped"}:
            state = "queued"
        priority = _bounded_int(raw_action.get("priority"), 1, 5, 3)
        total_units = raw_action.get("estimatedMinutes")
        if total_units is not None:
            total_units = _bounded_int(total_units, 0, 100_000, 0) or None
        action_item = {
            "listName": list_name,
            "state": state,
            "priority": priority,
            "estimatedMinutes": total_units,
            "dueAt": _bounded_label(raw_action.get("dueAt"), 40, ""),
        }
        if ("action_list", list_name.casefold()) not in seen:
            memberships.append(
                {
                    "name": list_name,
                    "kind": "action_list",
                    "parentName": "",
                    "role": "queue",
                    "reason": interpretation,
                }
            )

    navigation = value.get("navigation")
    if not isinstance(navigation, dict):
        navigation = {"command": "none"}
    command = str(navigation.get("command") or "none")
    if command not in {"none", "open_resource", "show_collection", "set_filter"}:
        command = "none"
    normalized_navigation = {
        "command": command,
        "resourceId": _bounded_label(navigation.get("resourceId"), 100, ""),
        "collectionName": _bounded_label(navigation.get("collectionName"), 100, ""),
        "filter": _bounded_label(navigation.get("filter"), 100, ""),
    }
    return {
        "message": message,
        "interpretation": interpretation,
        "confidence": confidence_number,
        "needsReview": bool(value.get("needsReview", False)),
        "memberships": memberships,
        "actionItem": action_item,
        "navigation": normalized_navigation,
    }


def proposal_can_auto_apply(connection: sqlite3.Connection, proposal_id: str) -> bool:
    row = _proposal_row(connection, proposal_id)
    operations = normalize_agent_response(json.loads(row["operations_json"]))
    if (
        operations["needsReview"]
        or operations["confidence"] < 0.75
        or not operations["memberships"]
        or not row["note_id"]
    ):
        return False
    if _latest_active_note_id(connection, row["resource_id"]) != row["note_id"]:
        return False
    locked_kinds = {
        membership["kind"]
        for membership in connection.execute(
            """
            SELECT c.kind FROM resource_collections rc
            JOIN collections c ON c.id=rc.collection_id
            WHERE rc.resource_id=? AND rc.authority='user_locked'
            """,
            (row["resource_id"],),
        )
    }
    proposed_primary = {
        membership["kind"]
        for membership in operations["memberships"]
        if membership["kind"] in PRIMARY_COLLECTION_KINDS
    }
    return not bool(locked_kinds & proposed_primary)


def apply_semantic_proposal(
    connection: sqlite3.Connection,
    proposal_id: str,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    proposal = _proposal_row(connection, proposal_id)
    existing = connection.execute(
        "SELECT * FROM semantic_decisions WHERE proposal_id=? OR idempotency_key=?",
        (proposal_id, _validate_idempotency_key(idempotency_key)),
    ).fetchone()
    if existing:
        if existing["proposal_id"] != proposal_id:
            raise ConflictError("Idempotency key was already used for another proposal")
        return _decision_result(connection, existing)
    resource = connection.execute(
        "SELECT semantic_revision FROM resources WHERE id=?",
        (proposal["resource_id"],),
    ).fetchone()
    if resource["semantic_revision"] != proposal["base_revision"]:
        raise ConflictError("The resource changed after this proposal was created")
    if proposal["note_id"] and _latest_active_note_id(connection, proposal["resource_id"]) != proposal["note_id"]:
        raise ConflictError("A newer user note superseded this proposal")

    operations = normalize_agent_response(json.loads(proposal["operations_json"]))
    locked_primary = {
        row["kind"]: str(row["name"])
        for row in connection.execute(
            """
            SELECT c.kind, c.name FROM resource_collections rc
            JOIN collections c ON c.id=rc.collection_id
            WHERE rc.resource_id=? AND rc.authority='user_locked'
              AND c.kind IN ('space', 'topic', 'focus')
            """,
            (proposal["resource_id"],),
        )
    }
    for membership in operations["memberships"]:
        locked_name = locked_primary.get(membership["kind"])
        if locked_name and locked_name.casefold() != membership["name"].casefold():
            raise ConflictError(
                f"The {membership['kind']} '{locked_name}' is locked by the user"
            )
    decision_id = _new_id("decision")
    audit_id = _new_id("audit")
    now = utc_now()
    with connection:
        before = semantic_snapshot(connection, proposal["resource_id"])
        new_revision = int(resource["semantic_revision"]) + 1
        connection.execute(
            """
            INSERT INTO semantic_decisions(
              id, proposal_id, decision, accepted_operations_json, actor,
              idempotency_key, applied_revision, created_at
            ) VALUES(?, ?, 'accepted', ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                proposal_id,
                _canonical_json(list(range(len(operations["memberships"])))),
                _bounded_label(actor, 60, "user"),
                _validate_idempotency_key(idempotency_key),
                new_revision,
                now,
            ),
        )
        _apply_memberships(
            connection,
            proposal["resource_id"],
            operations,
            decision_id,
            proposal["note_id"],
        )
        connection.execute(
            "UPDATE resources SET semantic_revision=? WHERE id=?",
            (new_revision, proposal["resource_id"]),
        )
        after = semantic_snapshot(connection, proposal["resource_id"])
        before_json = _canonical_json(before)
        after_json = _canonical_json(after)
        connection.execute(
            """
            INSERT INTO semantic_audits(
              id, resource_id, proposal_id, before_json, after_json,
              before_hash, after_hash, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                proposal["resource_id"],
                proposal_id,
                before_json,
                after_json,
                _sha256_text(before_json),
                _sha256_text(after_json),
                now,
            ),
        )
        connection.execute(
            "UPDATE semantic_decisions SET audit_id=? WHERE id=?",
            (audit_id, decision_id),
        )
    return _decision_result(
        connection,
        connection.execute("SELECT * FROM semantic_decisions WHERE id=?", (decision_id,)).fetchone(),
    )


def reject_semantic_proposal(
    connection: sqlite3.Connection,
    proposal_id: str,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _proposal_row(connection, proposal_id)
    request_key = _validate_idempotency_key(idempotency_key)
    existing = connection.execute(
        "SELECT * FROM semantic_decisions WHERE proposal_id=? OR idempotency_key=?",
        (proposal_id, request_key),
    ).fetchone()
    if existing:
        if existing["proposal_id"] != proposal_id:
            raise ConflictError("Idempotency key was already used for another proposal")
        return _decision_result(connection, existing)
    with connection:
        connection.execute(
            """
            INSERT INTO semantic_decisions(
              id, proposal_id, decision, actor, idempotency_key, created_at
            ) VALUES(?, ?, 'rejected', ?, ?, ?)
            """,
            (_new_id("decision"), proposal_id, _bounded_label(actor, 60, "user"), request_key, utc_now()),
        )
    row = connection.execute(
        "SELECT * FROM semantic_decisions WHERE proposal_id=?",
        (proposal_id,),
    ).fetchone()
    return _decision_result(connection, row)


def undo_semantic_audit(
    connection: sqlite3.Connection,
    audit_id: str,
) -> dict[str, Any]:
    audit = connection.execute(
        "SELECT * FROM semantic_audits WHERE id=?",
        (audit_id,),
    ).fetchone()
    if not audit:
        raise ValueError("Semantic audit was not found")
    if audit["undone_at"]:
        raise ConflictError("This semantic change was already undone")
    current = semantic_snapshot(connection, audit["resource_id"])
    current_json = _canonical_json(current)
    if _sha256_text(current_json) != audit["after_hash"]:
        raise ConflictError("The resource changed after this audit; Undo would overwrite newer work")
    before = json.loads(audit["before_json"])
    with connection:
        connection.execute(
            "DELETE FROM resource_collections WHERE resource_id=?",
            (audit["resource_id"],),
        )
        for membership in before["memberships"]:
            connection.execute(
                """
                INSERT INTO resource_collections(
                  resource_id, collection_id, role, reason, confidence, origin,
                  accepted, authority, applied_decision_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit["resource_id"],
                    membership["collectionId"],
                    membership["role"],
                    membership["reason"],
                    membership["confidence"],
                    membership["origin"],
                    membership["accepted"],
                    membership["authority"],
                    membership["appliedDecisionId"] or None,
                    membership["createdAt"],
                ),
            )
        for progress in before["progress"]:
            connection.execute(
                """
                INSERT INTO resource_collection_progress(
                  resource_id, collection_id, state, priority, completed_units,
                  total_units, due_at, revision, applied_decision_id, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit["resource_id"],
                    progress["collectionId"],
                    progress["state"],
                    progress["priority"],
                    progress["completedUnits"],
                    progress["totalUnits"],
                    progress["dueAt"] or None,
                    progress["revision"],
                    progress["appliedDecisionId"] or None,
                    progress["updatedAt"],
                ),
            )
        new_revision = int(current["revision"]) + 1
        connection.execute(
            "UPDATE resources SET semantic_revision=? WHERE id=?",
            (new_revision, audit["resource_id"]),
        )
        connection.execute(
            "UPDATE semantic_audits SET undone_at=? WHERE id=?",
            (utc_now(), audit_id),
        )
    return {
        "auditId": audit_id,
        "resourceId": audit["resource_id"],
        "undone": True,
        "semanticRevision": new_revision,
    }


def semantic_snapshot(connection: sqlite3.Connection, resource_id: str) -> dict[str, Any]:
    resource = connection.execute(
        "SELECT semantic_revision FROM resources WHERE id=?",
        (resource_id,),
    ).fetchone()
    if not resource:
        raise ValueError("Resource was not found")
    memberships = [
        {
            "collectionId": row["collection_id"],
            "role": row["role"],
            "reason": row["reason"] or "",
            "confidence": row["confidence"],
            "origin": row["origin"],
            "accepted": int(row["accepted"]),
            "authority": row["authority"],
            "appliedDecisionId": row["applied_decision_id"] or "",
            "createdAt": row["created_at"],
        }
        for row in connection.execute(
            """
            SELECT * FROM resource_collections
            WHERE resource_id=? ORDER BY collection_id
            """,
            (resource_id,),
        )
    ]
    progress = [
        {
            "collectionId": row["collection_id"],
            "state": row["state"],
            "priority": row["priority"],
            "completedUnits": row["completed_units"],
            "totalUnits": row["total_units"],
            "dueAt": row["due_at"] or "",
            "revision": row["revision"],
            "appliedDecisionId": row["applied_decision_id"] or "",
            "updatedAt": row["updated_at"],
        }
        for row in connection.execute(
            """
            SELECT * FROM resource_collection_progress
            WHERE resource_id=? ORDER BY collection_id
            """,
            (resource_id,),
        )
    ]
    return {
        "resourceId": resource_id,
        "revision": int(resource["semantic_revision"]),
        "memberships": memberships,
        "progress": progress,
    }


def action_list_summaries(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    result = []
    for collection in connection.execute(
        """
        SELECT * FROM collections
        WHERE kind='action_list' AND status='active' ORDER BY name COLLATE NOCASE
        """
    ):
        items = [
            dict(row)
            for row in connection.execute(
                """
                SELECT rc.resource_id, p.state, p.priority, p.completed_units,
                       p.total_units, p.due_at, p.revision, p.updated_at
                FROM resource_collections rc
                LEFT JOIN resource_collection_progress p
                  ON p.resource_id=rc.resource_id AND p.collection_id=rc.collection_id
                WHERE rc.collection_id=?
                ORDER BY COALESCE(p.priority, 3), rc.created_at
                """,
                (collection["id"],),
            )
        ]
        result.append(
            {
                "id": collection["id"],
                "name": collection["name"],
                "description": collection["description"] or "",
                "objective": collection["objective"] or "",
                "workflowKind": collection["workflow_kind"],
                "config": _json_or_none(collection["config_json"]) or {},
                "resourceCount": len(items),
                "resourceIds": [item["resource_id"] for item in items],
                "stateCounts": _count_values(item.get("state") or "queued" for item in items),
            }
        )
    return result


def update_action_progress(
    connection: sqlite3.Connection,
    resource_id: str,
    collection_id: str,
    *,
    state: str,
    priority: int,
    completed_units: int,
    total_units: int | None,
    due_at: str | None,
    expected_revision: int,
    request_id: str,
) -> dict[str, Any]:
    _require_resource(connection, resource_id)
    request_key = _validate_idempotency_key(request_id)
    audit_id = "audit_" + hashlib.sha256(request_key.encode("utf-8")).hexdigest()[:24]
    existing_audit = connection.execute(
        "SELECT resource_id, before_json, after_json, undone_at FROM semantic_audits WHERE id=?",
        (audit_id,),
    ).fetchone()
    if existing_audit:
        if existing_audit["resource_id"] != resource_id:
            raise ConflictError("Idempotency key was already used for another resource")
        before_progress = {
            item["collectionId"]: item
            for item in json.loads(existing_audit["before_json"])["progress"]
        }
        after_progress = {
            item["collectionId"]: item
            for item in json.loads(existing_audit["after_json"])["progress"]
        }
        changed_collections = {
            value
            for value in before_progress.keys() | after_progress.keys()
            if before_progress.get(value) != after_progress.get(value)
        }
        if collection_id not in changed_collections:
            raise ConflictError("Idempotency key was already used for another Action List")
        return {
            "auditId": audit_id,
            "resourceId": resource_id,
            "undone": bool(existing_audit["undone_at"]),
            "actionItem": _action_progress_item(connection, resource_id, collection_id),
        }

    row = connection.execute(
        """
        SELECT c.kind, c.name, c.workflow_kind, p.state, p.priority,
               p.completed_units, p.total_units, p.due_at, p.revision
        FROM resource_collections rc
        JOIN collections c ON c.id=rc.collection_id
        LEFT JOIN resource_collection_progress p
          ON p.resource_id=rc.resource_id AND p.collection_id=rc.collection_id
        WHERE rc.resource_id=? AND rc.collection_id=?
        """,
        (resource_id, collection_id),
    ).fetchone()
    if not row or row["kind"] != "action_list":
        raise ValueError("Action List membership was not found")
    current_revision = int(row["revision"] or 0)
    try:
        supplied_revision = int(expected_revision)
        normalized_priority = int(priority)
        normalized_completed = int(completed_units)
        normalized_total = None if total_units is None else int(total_units)
    except (TypeError, ValueError) as error:
        raise ValueError("Action List progress values must be integers") from error
    if supplied_revision != current_revision:
        raise ConflictError("Action List progress changed after it was opened")

    normalized_state = str(state or "queued").casefold()
    if normalized_state not in {"queued", "in_progress", "completed", "snoozed", "skipped"}:
        raise ValueError("Action List state is invalid")
    if not 1 <= normalized_priority <= 5:
        raise ValueError("Action List priority must be between 1 and 5")
    if normalized_completed < 0:
        raise ValueError("Completed units cannot be negative")
    if normalized_total is not None and normalized_total < 0:
        raise ValueError("Total units cannot be negative")
    if normalized_total is not None and normalized_completed > normalized_total:
        raise ValueError("Completed units cannot exceed total units")
    normalized_due = _bounded_label(due_at, 40, "") or None

    before = semantic_snapshot(connection, resource_id)
    resource_revision = int(before["revision"])
    now = utc_now()
    with connection:
        connection.execute(
            """
            INSERT INTO resource_collection_progress(
              resource_id, collection_id, state, priority, completed_units,
              total_units, due_at, revision, applied_decision_id, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(resource_id, collection_id) DO UPDATE SET
              state=excluded.state,
              priority=excluded.priority,
              completed_units=excluded.completed_units,
              total_units=excluded.total_units,
              due_at=excluded.due_at,
              revision=excluded.revision,
              applied_decision_id=NULL,
              updated_at=excluded.updated_at
            """,
            (
                resource_id,
                collection_id,
                normalized_state,
                normalized_priority,
                normalized_completed,
                normalized_total,
                normalized_due,
                current_revision + 1,
                now,
            ),
        )
        connection.execute(
            "UPDATE resources SET semantic_revision=? WHERE id=?",
            (resource_revision + 1, resource_id),
        )
        after = semantic_snapshot(connection, resource_id)
        before_json = _canonical_json(before)
        after_json = _canonical_json(after)
        connection.execute(
            """
            INSERT INTO semantic_audits(
              id, resource_id, proposal_id, before_json, after_json,
              before_hash, after_hash, created_at
            ) VALUES(?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                resource_id,
                before_json,
                after_json,
                _sha256_text(before_json),
                _sha256_text(after_json),
                now,
            ),
        )
    return {
        "auditId": audit_id,
        "resourceId": resource_id,
        "semanticRevision": resource_revision + 1,
        "actionItem": _action_progress_item(connection, resource_id, collection_id),
    }


def _apply_memberships(
    connection: sqlite3.Connection,
    resource_id: str,
    operations: dict[str, Any],
    decision_id: str,
    note_id: str | None,
) -> None:
    memberships = operations["memberships"]
    membership_origin = "user_note" if note_id else "codex_workspace"
    membership_authority = "user_note" if note_id else "accepted_stable"
    proposed_primary = {
        item["kind"] for item in memberships if item["kind"] in PRIMARY_COLLECTION_KINDS
    }
    for kind in proposed_primary:
        connection.execute(
            """
            DELETE FROM resource_collections
            WHERE resource_id=?
              AND collection_id IN (SELECT id FROM collections WHERE kind=?)
              AND authority!='user_locked'
            """,
            (resource_id, kind),
        )

    created: dict[str, str] = {}
    ordered = sorted(
        memberships,
        key=lambda item: {"space": 0, "topic": 1, "focus": 2, "project": 3, "action_list": 4}[item["kind"]],
    )
    for item in ordered:
        parent_id = None
        if item["parentName"]:
            parent = connection.execute(
                "SELECT id FROM collections WHERE name=? COLLATE NOCASE",
                (item["parentName"],),
            ).fetchone()
            parent_id = parent["id"] if parent else created.get(item["parentName"].casefold())
            if not parent_id:
                raise ValueError(f"Parent collection does not exist: {item['parentName']}")
        collection_id = _ensure_collection(connection, item, parent_id)
        created[item["name"].casefold()] = collection_id
        connection.execute(
            """
            INSERT INTO resource_collections(
              resource_id, collection_id, role, reason, confidence, origin,
              accepted, authority, applied_decision_id, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(resource_id, collection_id) DO UPDATE SET
              role=excluded.role,
              reason=excluded.reason,
              confidence=excluded.confidence,
              origin=excluded.origin,
              accepted=1,
              authority=excluded.authority,
              applied_decision_id=excluded.applied_decision_id
            WHERE resource_collections.authority!='user_locked'
            """,
            (
                resource_id,
                collection_id,
                item["role"],
                item["reason"],
                operations["confidence"],
                membership_origin,
                membership_authority,
                decision_id,
                utc_now(),
            ),
        )

    action = operations.get("actionItem")
    if action:
        collection = connection.execute(
            "SELECT id FROM collections WHERE name=? COLLATE NOCASE AND kind='action_list'",
            (action["listName"],),
        ).fetchone()
        if not collection:
            raise ValueError("Action List membership was not created")
        connection.execute(
            """
            INSERT INTO resource_collection_progress(
              resource_id, collection_id, state, priority, completed_units,
              total_units, due_at, revision, applied_decision_id, updated_at
            ) VALUES(?, ?, ?, ?, 0, ?, ?, 0, ?, ?)
            ON CONFLICT(resource_id, collection_id) DO UPDATE SET
              state=excluded.state,
              priority=excluded.priority,
              total_units=COALESCE(excluded.total_units, resource_collection_progress.total_units),
              due_at=excluded.due_at,
              revision=resource_collection_progress.revision + 1,
              applied_decision_id=excluded.applied_decision_id,
              updated_at=excluded.updated_at
            """,
            (
                resource_id,
                collection["id"],
                action["state"],
                action["priority"],
                action["estimatedMinutes"],
                action["dueAt"] or None,
                decision_id,
                utc_now(),
            ),
        )


def _ensure_collection(
    connection: sqlite3.Connection,
    item: dict[str, Any],
    parent_id: str | None,
) -> str:
    existing = connection.execute(
        "SELECT id, kind FROM collections WHERE name=? COLLATE NOCASE",
        (item["name"],),
    ).fetchone()
    if existing:
        if existing["kind"] != item["kind"]:
            raise ValueError(f"Collection name already belongs to {existing['kind']}: {item['name']}")
        if parent_id:
            connection.execute(
                "UPDATE collections SET parent_id=COALESCE(parent_id, ?), updated_at=? WHERE id=?",
                (parent_id, utc_now(), existing["id"]),
            )
        return str(existing["id"])
    collection_id = "col_" + hashlib.sha256(item["name"].casefold().encode("utf-8")).hexdigest()[:24]
    workflow_kind = "watch_queue" if item["kind"] == "action_list" and "watch" in item["name"].casefold() else (
        "reading_queue" if item["kind"] == "action_list" and "read" in item["name"].casefold() else "none"
    )
    now = utc_now()
    connection.execute(
        """
        INSERT INTO collections(
          id, name, kind, description, objective, parent_id, status,
          workflow_kind, config_json, created_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, 'active', ?, '{}', ?, ?)
        """,
        (
            collection_id,
            item["name"],
            item["kind"],
            item["reason"],
            "",
            parent_id,
            workflow_kind,
            now,
            now,
        ),
    )
    return collection_id


def _proposal_row(connection: sqlite3.Connection, proposal_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM semantic_proposals WHERE id=?",
        (proposal_id,),
    ).fetchone()
    if not row:
        raise ValueError("Semantic proposal was not found")
    return row


def _decision_result(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    proposal = _proposal_row(connection, row["proposal_id"])
    return {
        "decisionId": row["id"],
        "proposalId": row["proposal_id"],
        "resourceId": proposal["resource_id"],
        "decision": row["decision"],
        "semanticRevision": row["applied_revision"],
        "auditId": row["audit_id"] or "",
    }


def _action_progress_item(
    connection: sqlite3.Connection,
    resource_id: str,
    collection_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT p.collection_id AS collectionId, c.name AS collectionName,
               c.workflow_kind AS workflowKind, p.state, p.priority,
               p.completed_units AS completedUnits, p.total_units AS totalUnits,
               p.due_at AS dueAt, p.revision, p.updated_at AS updatedAt
        FROM resource_collection_progress p
        JOIN collections c ON c.id=p.collection_id
        WHERE p.resource_id=? AND p.collection_id=?
        """,
        (resource_id, collection_id),
    ).fetchone()
    return dict(row) if row else {}


def _note_input_hash(note: dict[str, Any]) -> str:
    if note["kind"] == "text":
        return _sha256_text(str(note.get("text") or ""))
    transcript = note.get("processing", {}).get("transcription", {})
    if transcript.get("outputText"):
        return _sha256_text(str(transcript["outputText"]))
    return str(note.get("audioSha256") or _sha256_text(note["id"]))


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


def _insert_agent_request(
    connection: sqlite3.Connection,
    kind: str,
    *,
    resource_id: str | None,
    note_id: str | None,
    request_text: str | None,
) -> str:
    request_id = _new_id("agent")
    connection.execute(
        """
        INSERT INTO agent_requests(
          id, kind, resource_id, note_id, request_text, status, created_at
        ) VALUES(?, ?, ?, ?, ?, 'queued', ?)
        """,
        (request_id, kind, resource_id, note_id, request_text, utc_now()),
    )
    return request_id


def _response_has_semantic_operations(response: dict[str, Any]) -> bool:
    return bool(response["memberships"] or response["actionItem"])


def _require_resource(connection: sqlite3.Connection, resource_id: str) -> None:
    if not re.fullmatch(r"res_[a-f0-9]{24}", str(resource_id or "")):
        raise ValueError("Resource ID is invalid")
    row = connection.execute(
        "SELECT 1 FROM resources WHERE id=? AND library_state='accepted'",
        (resource_id,),
    ).fetchone()
    if not row:
        raise ValueError("Accepted resource was not found")


def _validate_note_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Note text must be a string")
    if "\x00" in value:
        raise ValueError("Note text contains a NUL character")
    encoded = value.encode("utf-8")
    if not value.strip() or len(encoded) > MAX_NOTE_BYTES:
        raise ValueError("Note text must be between 1 and 32768 UTF-8 bytes")
    return value


def _validate_idempotency_key(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{12,160}", text):
        raise ValueError("Idempotency key is invalid")
    return text


def _collection_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 100 or any(ord(character) < 32 for character in name):
        raise ValueError("Collection name is invalid")
    return name


def _optional_collection_name(value: Any) -> str:
    if value in {None, ""}:
        return ""
    return _collection_name(value)


def _bounded_text(value: Any, limit: int, default: str) -> str:
    text = str(value or "").strip()
    return (text or default)[:limit]


def _bounded_label(value: Any, limit: int, default: str) -> str:
    text = str(value or "").strip()
    text = "".join(character for character in text if ord(character) >= 32)
    return (text or default)[:limit]


def _bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(maximum, max(minimum, number))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_or_none(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return None


def _atomic_private_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _count_values(values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[str(value)] = result.get(str(value), 0) + 1
    return result


class ConflictError(ValueError):
    pass
