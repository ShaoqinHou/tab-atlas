from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any

from .database import utc_now


def semantic_snapshot(
    connection: sqlite3.Connection, resource_id: str
) -> dict[str, Any]:
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
            "SELECT * FROM resource_collections WHERE resource_id=? ORDER BY collection_id",
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
            "SELECT * FROM resource_collection_progress WHERE resource_id=? ORDER BY collection_id",
            (resource_id,),
        )
    ]
    return {
        "resourceId": resource_id,
        "revision": int(resource["semantic_revision"]),
        "memberships": memberships,
        "progress": progress,
    }


def record_direct_semantic_change(
    connection: sqlite3.Connection,
    before: dict[str, Any],
) -> str | None:
    """Advance revision and create an undoable audit when a direct writer changed organization."""
    resource_id = str(before["resourceId"])
    current = semantic_snapshot(connection, resource_id)
    if _semantic_content(before) == _semantic_content(current):
        return None

    connection.execute(
        "UPDATE resources SET semantic_revision=semantic_revision + 1 WHERE id=?",
        (resource_id,),
    )
    after = semantic_snapshot(connection, resource_id)
    before_json = _canonical_json(before)
    after_json = _canonical_json(after)
    audit_id = f"audit_{uuid.uuid4().hex[:24]}"
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
            _sha256(before_json),
            _sha256(after_json),
            utc_now(),
        ),
    )
    return audit_id


def _semantic_content(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "memberships": snapshot.get("memberships") or [],
        "progress": snapshot.get("progress") or [],
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
