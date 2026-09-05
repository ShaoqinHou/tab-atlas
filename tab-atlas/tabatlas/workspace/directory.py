from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..database import utc_now
from .semantics import semantic_snapshot
from .support import (
    ConflictError,
    _bounded_label,
    _canonical_json,
    _count_values,
    _json_or_none,
    _require_resource,
    _sha256_text,
    _validate_idempotency_key,
)


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
                "stateCounts": _count_values(
                    item.get("state") or "queued" for item in items
                ),
            }
        )
    return result


def workspace_directory_summaries(
    connection: sqlite3.Connection,
) -> dict[str, list[dict[str, Any]]]:
    summaries: list[dict[str, Any]] = []
    for collection in connection.execute(
        """
        SELECT c.*, parent.name AS parent_name
        FROM collections c
        LEFT JOIN collections parent ON parent.id=c.parent_id
        WHERE c.status='active' AND c.kind IN ('space', 'project')
          AND EXISTS(
            SELECT 1 FROM resource_collections rc WHERE rc.collection_id=c.id
          )
        ORDER BY c.name COLLATE NOCASE
        """
    ):
        resource_ids = [
            row["resource_id"]
            for row in connection.execute(
                """
                SELECT rc.resource_id
                FROM resource_collections rc
                JOIN resources r ON r.id=rc.resource_id
                WHERE rc.collection_id=? AND r.library_state='accepted'
                ORDER BY r.title COLLATE NOCASE, r.id
                """,
                (collection["id"],),
            )
        ]
        if not resource_ids:
            continue
        top_topics = []
        if collection["kind"] == "space":
            top_topics = [
                {"name": row["name"], "count": int(row["resource_count"])}
                for row in connection.execute(
                    """
                    SELECT topic.name, COUNT(DISTINCT base.resource_id) AS resource_count
                    FROM resource_collections base
                    JOIN resources r ON r.id=base.resource_id AND r.library_state='accepted'
                    JOIN resource_collections topic_rc ON topic_rc.resource_id=base.resource_id
                    JOIN collections topic ON topic.id=topic_rc.collection_id AND topic.kind='topic'
                    WHERE base.collection_id=?
                    GROUP BY topic.id, topic.name
                    ORDER BY resource_count DESC, topic.name COLLATE NOCASE
                    LIMIT 5
                    """,
                    (collection["id"],),
                )
            ]
        summaries.append(
            {
                "id": collection["id"],
                "name": collection["name"],
                "kind": collection["kind"],
                "parentId": collection["parent_id"] or "",
                "parentName": collection["parent_name"] or "",
                "description": collection["description"]
                or "Resources organized around this purpose.",
                "objective": collection["objective"] or "",
                "resourceCount": len(resource_ids),
                "resourceIds": resource_ids,
                "previewResourceIds": resource_ids[:3],
                "topTopics": top_topics,
            }
        )
    return {
        "spaces": sorted(
            (item for item in summaries if item["kind"] == "space"),
            key=lambda item: (-item["resourceCount"], item["name"].casefold()),
        ),
        "projects": sorted(
            (item for item in summaries if item["kind"] == "project"),
            key=lambda item: (-item["resourceCount"], item["name"].casefold()),
        ),
    }


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
            raise ConflictError(
                "Idempotency key was already used for another Action List"
            )
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
    if normalized_state not in {
        "queued",
        "in_progress",
        "completed",
        "snoozed",
        "skipped",
    }:
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
