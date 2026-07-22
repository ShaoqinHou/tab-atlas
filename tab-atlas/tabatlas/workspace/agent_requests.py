from __future__ import annotations

import sqlite3
from typing import Any

from ..database import utc_now
from .constants import COLLECTION_KINDS, PRIMARY_COLLECTION_KINDS, PROMPT_VERSION
from .notes import (
    _append_processing_event,
    _latest_active_note_id,
    _note_input_hash,
    active_note_text,
    note_detail,
)
from .support import (
    _bounded_int,
    _bounded_label,
    _bounded_text,
    _canonical_json,
    _collection_name,
    _insert_agent_request,
    _json_or_none,
    _new_id,
    _optional_collection_name,
    _require_resource,
    _sha256_text,
    _validate_note_text,
)


def queued_agent_requests(
    connection: sqlite3.Connection, limit: int = 20
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM agent_requests
        WHERE status='queued' ORDER BY created_at, rowid LIMIT ?
        """,
        (max(1, min(limit, 100)),),
    ).fetchall()
    return [dict(row) for row in rows]


def recover_agent_requests(
    connection: sqlite3.Connection, limit: int = 100
) -> list[dict[str, Any]]:
    """Return unfinished work to the durable queue after an unclean shutdown."""
    with connection:
        connection.execute(
            """
            UPDATE agent_requests
            SET status='queued', started_at=NULL, error_code='recovered_after_restart'
            WHERE status='running'
            """
        )
    return queued_agent_requests(connection, limit)


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
            (
                _bounded_label(error_code, 80, "agent_unavailable"),
                utc_now(),
                request_id,
            ),
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
            proposal_note_id = request["note_id"] or _latest_active_note_id(
                connection,
                request["resource_id"],
            )
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
                    proposal_note_id or None,
                    request_id,
                    revision,
                    model,
                    thread_id,
                    normalized["message"],
                    _canonical_json(normalized),
                    _canonical_json(
                        [{"kind": "user_note", "id": proposal_note_id}]
                        if proposal_note_id
                        else []
                    ),
                    _sha256_text(
                        active_note_text(connection, proposal_note_id)
                        if proposal_note_id
                        else str(request["request_text"] or "")
                    ),
                    PROMPT_VERSION,
                    utc_now(),
                ),
            )
    result = agent_request_detail(connection, request_id)
    result["proposalId"] = proposal_id
    return result


def agent_request_detail(
    connection: sqlite3.Connection, request_id: str
) -> dict[str, Any]:
    from .semantics import proposal_is_current

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
        "proposalCurrent": proposal_is_current(connection, proposal["id"])
        if proposal
        else False,
        "decision": decision["decision"] if decision else "",
        "auditId": decision["audit_id"] if decision and decision["audit_id"] else "",
        "createdAt": row["created_at"],
        "completedAt": row["completed_at"] or "",
    }


def normalize_agent_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Codex response must be an object")
    message = _bounded_text(
        value.get("message"), 1200, "Codex interpreted the request."
    )
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
    if command not in {
        "none",
        "open_resource",
        "show_collection",
        "set_filter",
        "browser_sync",
    }:
        command = "none"
    raw_browsers = navigation.get("browsers")
    browsers = []
    if isinstance(raw_browsers, list):
        browsers = sorted(
            {
                str(browser).strip().casefold()
                for browser in raw_browsers
                if str(browser).strip().casefold() in {"chrome", "edge"}
            }
        )
    normalized_navigation = {
        "command": command,
        "resourceId": _bounded_label(navigation.get("resourceId"), 100, ""),
        "collectionName": _bounded_label(navigation.get("collectionName"), 100, ""),
        "filter": _bounded_label(navigation.get("filter"), 100, ""),
        "browsers": browsers,
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


def _response_has_semantic_operations(response: dict[str, Any]) -> bool:
    return bool(response["memberships"] or response["actionItem"])
