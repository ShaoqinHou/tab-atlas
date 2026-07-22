from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..database import utc_now
from ..organization import semantic_snapshot
from .agent_requests import normalize_agent_response
from .constants import PRIMARY_COLLECTION_KINDS
from .notes import _latest_active_note_id, active_note_text
from .support import (
    ConflictError,
    _bounded_label,
    _canonical_json,
    _new_id,
    _sha256_text,
    _validate_idempotency_key,
)


def proposal_is_current(connection: sqlite3.Connection, proposal_id: str) -> bool:
    row = _proposal_row(connection, proposal_id)
    resource = connection.execute(
        "SELECT semantic_revision FROM resources WHERE id=?",
        (row["resource_id"],),
    ).fetchone()
    if not resource or resource["semantic_revision"] != row["base_revision"]:
        return False
    if row["note_id"]:
        if _latest_active_note_id(connection, row["resource_id"]) != row["note_id"]:
            return False
        try:
            return row["input_hash"] == _sha256_text(
                active_note_text(connection, row["note_id"])
            )
        except ValueError:
            return False
    return True


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
    if (
        proposal["note_id"]
        and _latest_active_note_id(connection, proposal["resource_id"])
        != proposal["note_id"]
    ):
        raise ConflictError("A newer user note superseded this proposal")
    if proposal["note_id"] and proposal["input_hash"] != _sha256_text(
        active_note_text(connection, proposal["note_id"])
    ):
        raise ConflictError(
            "The note or transcript changed after this proposal was created"
        )

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
        connection.execute(
            "SELECT * FROM semantic_decisions WHERE id=?", (decision_id,)
        ).fetchone(),
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
            (
                _new_id("decision"),
                proposal_id,
                _bounded_label(actor, 60, "user"),
                request_key,
                utc_now(),
            ),
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
        raise ConflictError(
            "The resource changed after this audit; Undo would overwrite newer work"
        )
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
        key=lambda item: {
            "space": 0,
            "topic": 1,
            "focus": 2,
            "project": 3,
            "action_list": 4,
        }[item["kind"]],
    )
    for item in ordered:
        parent_id = None
        if item["parentName"]:
            parent = connection.execute(
                "SELECT id FROM collections WHERE name=? COLLATE NOCASE",
                (item["parentName"],),
            ).fetchone()
            parent_id = (
                parent["id"] if parent else created.get(item["parentName"].casefold())
            )
            if not parent_id:
                raise ValueError(
                    f"Parent collection does not exist: {item['parentName']}"
                )
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
            raise ValueError(
                f"Collection name already belongs to {existing['kind']}: {item['name']}"
            )
        if parent_id:
            connection.execute(
                "UPDATE collections SET parent_id=COALESCE(parent_id, ?), updated_at=? WHERE id=?",
                (parent_id, utc_now(), existing["id"]),
            )
        return str(existing["id"])
    collection_id = (
        "col_"
        + hashlib.sha256(item["name"].casefold().encode("utf-8")).hexdigest()[:24]
    )
    workflow_kind = (
        "watch_queue"
        if item["kind"] == "action_list" and "watch" in item["name"].casefold()
        else (
            "reading_queue"
            if item["kind"] == "action_list" and "read" in item["name"].casefold()
            else "none"
        )
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


def _decision_result(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> dict[str, Any]:
    proposal = _proposal_row(connection, row["proposal_id"])
    return {
        "decisionId": row["id"],
        "proposalId": row["proposal_id"],
        "resourceId": proposal["resource_id"],
        "decision": row["decision"],
        "semanticRevision": row["applied_revision"],
        "auditId": row["audit_id"] or "",
    }
