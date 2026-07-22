from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from ..database import utc_now
from .constants import (
    AUDIO_SIGNATURES,
    AUDIO_SUFFIXES,
    MAX_AUDIO_BYTES,
    MAX_AUDIO_DURATION_MS,
    PROMPT_VERSION,
)
from .notes import _append_processing_event, _cancel_pending_note_analysis, note_detail
from .support import (
    ConflictError,
    _atomic_private_write,
    _bounded_label,
    _new_id,
    _require_resource,
    _sha256_text,
    _validate_idempotency_key,
    _validate_note_text,
)


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
            raise ConflictError(
                "Idempotency key was already used for another voice note"
            )
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
                engine="local_whisper",
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
    run_id = f"transcript:{request_key}"
    existing = connection.execute(
        """
        SELECT input_hash, output_text FROM note_processing_events
        WHERE note_id=? AND run_id=? AND stage='transcription'
        ORDER BY sequence DESC, rowid DESC LIMIT 1
        """,
        (note_id, run_id),
    ).fetchone()
    if existing:
        if (
            existing["input_hash"] != input_hash
            or existing["output_text"] != exact_text
        ):
            raise ConflictError(
                "Idempotency key was already used for another transcript"
            )
        return note_detail(connection, note_id)
    with connection:
        _cancel_pending_note_analysis(connection, note_id, "transcript_changed")
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
            "not_required",
            input_hash,
            engine="awaiting_user_request",
            prompt_version=PROMPT_VERSION,
        )
    return note_detail(connection, note_id)


def queued_audio_transcriptions(
    connection: sqlite3.Connection,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT n.id
        FROM resource_notes n
        WHERE n.kind='audio'
          AND COALESCE((
            SELECT e.state FROM note_processing_events e
            WHERE e.note_id=n.id AND e.stage='transcription'
            ORDER BY e.created_at DESC, e.sequence DESC, e.rowid DESC LIMIT 1
          ), '') IN ('queued', 'running')
        ORDER BY n.created_at, n.rowid
        LIMIT ?
        """,
        (max(1, min(limit, 100)),),
    ).fetchall()
    result = [note_detail(connection, row["id"]) for row in rows]
    return [note for note in result if note["active"]]


def audio_note_source(
    connection: sqlite3.Connection,
    state_dir: Path,
    note_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM resource_notes WHERE id=? AND kind='audio'",
        (note_id,),
    ).fetchone()
    if not row:
        raise ValueError("Voice note was not found")
    root = state_dir.resolve()
    path = (root / str(row["audio_relpath"])).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Voice note audio file is unavailable")
    return {
        "id": row["id"],
        "resourceId": row["resource_id"],
        "path": path,
        "mimeType": row["audio_mime"],
        "sha256": row["audio_sha256"],
    }


def mark_audio_transcription_running(
    connection: sqlite3.Connection,
    note_id: str,
) -> dict[str, Any]:
    note = note_detail(connection, note_id)
    latest = note.get("processing", {}).get("transcription", {})
    if not note["active"] or note["kind"] != "audio":
        raise ValueError("Active voice note was not found")
    if latest.get("state") not in {"queued", "running"}:
        return note
    with connection:
        _append_processing_event(
            connection,
            note_id,
            _new_id("run"),
            "transcription",
            "running",
            str(note["audioSha256"]),
            engine="local_whisper",
        )
    return note_detail(connection, note_id)


def complete_audio_transcription(
    connection: sqlite3.Connection,
    note_id: str,
    transcript: str,
    model: str,
) -> dict[str, Any]:
    exact_text = _validate_note_text(transcript)
    note = note_detail(connection, note_id)
    latest = note.get("processing", {}).get("transcription", {})
    if latest.get("state") == "succeeded":
        return note
    if latest.get("state") not in {"queued", "running"}:
        raise ValueError("Voice note is not awaiting transcription")
    input_hash = _sha256_text(exact_text)
    run_id = _new_id("run")
    with connection:
        _append_processing_event(
            connection,
            note_id,
            run_id,
            "transcription",
            "succeeded",
            str(note["audioSha256"]),
            output_text=exact_text,
            engine="local_whisper",
            model=_bounded_label(model, 160, "local_whisper"),
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


def fail_audio_transcription(
    connection: sqlite3.Connection,
    note_id: str,
    error_code: str,
) -> dict[str, Any]:
    note = note_detail(connection, note_id)
    latest = note.get("processing", {}).get("transcription", {})
    if latest.get("state") not in {"queued", "running"}:
        return note
    with connection:
        _append_processing_event(
            connection,
            note_id,
            _new_id("run"),
            "transcription",
            "failed",
            str(note["audioSha256"]),
            engine="local_whisper",
            error_code=_bounded_label(error_code, 80, "local_transcription_failed"),
        )
    return note_detail(connection, note_id)
