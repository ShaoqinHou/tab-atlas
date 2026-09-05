from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Iterable

from .common import _chunks
from .constants import RESOURCE_STATUSES, TASK_STATUSES, TOPIC_PARENT_DEFAULTS
from .database import utc_now
from .organization import record_direct_semantic_change, semantic_snapshot
from .read_models import (
    _collection_authority_rank as _collection_authority_rank,
    _duplicate_keeper_order as _duplicate_keeper_order,
    _resources_for_capture_ids as _resources_for_capture_ids,
    annotation_batch as annotation_batch,
    current_resources as current_resources,
    discovery_batch as discovery_batch,
    discovery_resources as discovery_resources,
    display_url as display_url,
    exact_duplicate_summary as exact_duplicate_summary,
    inventory as inventory,
    library_resources as library_resources,
    query_resources as query_resources,
)


def set_discovery_state(
    connection: sqlite3.Connection,
    target_state: str,
    resource_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    if target_state not in {"accepted", "dismissed"}:
        raise ValueError("target_state must be accepted or dismissed")
    requested = {
        str(value).strip() for value in (resource_ids or []) if str(value).strip()
    }
    if len(requested) > 1000:
        raise ValueError("Discovery decision limit exceeded")
    if requested:
        placeholders = ",".join("?" for _ in requested)
        allowed_source_states = (
            {"candidate", "dismissed"} if target_state == "accepted" else {"candidate"}
        )
        state_placeholders = ",".join("?" for _ in allowed_source_states)
        rows = connection.execute(
            f"SELECT id FROM resources WHERE library_state IN ({state_placeholders}) "
            f"AND id IN ({placeholders})",
            [*sorted(allowed_source_states), *sorted(requested)],
        ).fetchall()
        candidates = {row["id"] for row in rows}
        invalid = sorted(requested - candidates)
        if invalid:
            raise ValueError(
                "Resources are not eligible for this discovery decision: "
                + ", ".join(invalid[:10])
            )
    else:
        candidates = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM resources WHERE library_state='candidate'"
            )
        }

    now = utc_now()
    with connection:
        for batch in _chunks(sorted(candidates), 400):
            placeholders = ",".join("?" for _ in batch)
            if target_state == "accepted":
                connection.execute(
                    f"UPDATE resources SET library_state='accepted', accepted_at=?, dismissed_at=NULL "
                    f"WHERE library_state IN ('candidate', 'dismissed') AND id IN ({placeholders})",
                    [now, *batch],
                )
                connection.execute(
                    f"""
                    UPDATE resource_collections
                    SET accepted=1,
                        authority=CASE
                          WHEN authority IN ('user_locked', 'user_note') THEN authority
                          ELSE 'accepted_stable'
                        END
                    WHERE resource_id IN ({placeholders})
                    """,
                    batch,
                )
            else:
                connection.execute(
                    f"UPDATE resources SET library_state='dismissed', dismissed_at=? "
                    f"WHERE library_state='candidate' AND id IN ({placeholders})",
                    [now, *batch],
                )
    return {
        "state": target_state,
        "updated": len(candidates),
        "resourceIds": sorted(candidates),
    }


def remove_library_resources(
    connection: sqlite3.Connection,
    resource_ids: Iterable[str],
) -> dict[str, Any]:
    requested = {str(value).strip() for value in resource_ids if str(value).strip()}
    if not requested:
        raise ValueError("At least one resource ID is required")
    if len(requested) > 1000:
        raise ValueError("Library removal limit exceeded")

    placeholders = ",".join("?" for _ in requested)
    eligible = {
        row["id"]
        for row in connection.execute(
            f"SELECT id FROM resources WHERE library_state='accepted' "
            f"AND id IN ({placeholders})",
            sorted(requested),
        )
    }
    invalid = sorted(requested - eligible)
    if invalid:
        raise ValueError(
            "Resources are not accepted library entries: " + ", ".join(invalid[:10])
        )

    now = utc_now()
    with connection:
        for batch in _chunks(sorted(eligible), 400):
            batch_placeholders = ",".join("?" for _ in batch)
            connection.execute(
                f"UPDATE resources SET library_state='dismissed', dismissed_at=? "
                f"WHERE library_state='accepted' AND id IN ({batch_placeholders})",
                [now, *batch],
            )
    return {
        "state": "dismissed",
        "updated": len(eligible),
        "resourceIds": sorted(eligible),
        "recoverable": True,
    }


def apply_annotations(connection: sqlite3.Connection, document: Any) -> dict[str, int]:
    if isinstance(document, list):
        entries = document
    elif isinstance(document, dict) and isinstance(document.get("resources"), list):
        entries = document["resources"]
    else:
        raise ValueError(
            "Annotation file must be an array or contain a resources array"
        )
    if len(entries) > 1000:
        raise ValueError("Annotation batch limit exceeded")

    updated = 0
    membership_count = 0
    task_count = 0
    now = utc_now()
    with connection:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            current_resource_id = str(entry.get("resourceId") or "")
            if not connection.execute(
                "SELECT 1 FROM resources WHERE id=?", (current_resource_id,)
            ).fetchone():
                raise ValueError(f"Unknown resource ID: {current_resource_id}")
            tracks_semantics = bool(
                entry.get("replaceCollections") or entry.get("collections")
            )
            semantic_before = (
                semantic_snapshot(connection, current_resource_id)
                if tracks_semantics
                else None
            )
            fields = {
                "brief": _optional_text(entry, "brief", 1000),
                "detail": _optional_text(entry, "detail", 10000),
                "why_kept": _optional_text(entry, "whyKept", 3000),
                "next_action": _optional_text(entry, "nextAction", 2000),
            }
            assignments = []
            parameters: list[Any] = []
            for column, value in fields.items():
                if value is not None:
                    assignments.append(f"{column}=?")
                    parameters.append(value)
            if "status" in entry:
                status = str(entry["status"])
                if status not in RESOURCE_STATUSES:
                    raise ValueError(f"Invalid resource status: {status}")
                assignments.append("status=?")
                parameters.append(status)
            if "confidence" in entry:
                assignments.append("confidence=?")
                parameters.append(_confidence(entry["confidence"]))
            if assignments:
                parameters.append(current_resource_id)
                connection.execute(
                    f"UPDATE resources SET {', '.join(assignments)} WHERE id=?",
                    parameters,
                )
                updated += 1

            if entry.get("replaceCollections"):
                connection.execute(
                    "DELETE FROM resource_collections WHERE resource_id=?",
                    (current_resource_id,),
                )
            for collection_value in entry.get("collections") or []:
                collection = _normalize_collection_input(collection_value)
                collection_id = _collection_id(collection["name"])
                parent_id = collection["parentId"]
                if collection["parentName"]:
                    proposed_parent_id = _collection_id(collection["parentName"])
                    parent_kind = {
                        "focus": "topic",
                        "topic": "space",
                    }.get(collection["kind"], "theme")
                    connection.execute(
                        """
                        INSERT INTO collections(
                          id, name, kind, description, objective, parent_id,
                          status, created_at, updated_at
                        ) VALUES(?, ?, ?, NULL, NULL, NULL, 'active', ?, ?)
                        ON CONFLICT(name) DO UPDATE SET updated_at=excluded.updated_at
                        """,
                        (
                            proposed_parent_id,
                            collection["parentName"],
                            parent_kind,
                            now,
                            now,
                        ),
                    )
                    parent_id = connection.execute(
                        "SELECT id FROM collections WHERE name=? COLLATE NOCASE",
                        (collection["parentName"],),
                    ).fetchone()["id"]
                if (
                    parent_id
                    and not connection.execute(
                        "SELECT 1 FROM collections WHERE id=?", (parent_id,)
                    ).fetchone()
                ):
                    raise ValueError(f"Unknown parent collection ID: {parent_id}")
                existing_collection = connection.execute(
                    "SELECT id, kind, parent_id FROM collections WHERE name=? COLLATE NOCASE",
                    (collection["name"],),
                ).fetchone()
                if existing_collection:
                    existing_kind = str(existing_collection["kind"] or "")
                    existing_parent = str(existing_collection["parent_id"] or "")
                    if existing_kind != collection["kind"] and existing_kind != "theme":
                        raise ValueError(
                            f"Collection name already has kind {existing_kind}: {collection['name']}"
                        )
                    if existing_parent and parent_id and existing_parent != parent_id:
                        raise ValueError(
                            f"Collection name already belongs to another parent: {collection['name']}"
                        )
                connection.execute(
                    """
                    INSERT INTO collections(
                      id, name, kind, description, objective, parent_id,
                      status, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                      kind=excluded.kind,
                      description=COALESCE(excluded.description, collections.description),
                      objective=COALESCE(excluded.objective, collections.objective),
                      parent_id=COALESCE(excluded.parent_id, collections.parent_id),
                      updated_at=excluded.updated_at
                    """,
                    (
                        collection_id,
                        collection["name"],
                        collection["kind"],
                        collection["description"],
                        collection["objective"],
                        parent_id or None,
                        now,
                        now,
                    ),
                )
                actual_collection = connection.execute(
                    "SELECT id FROM collections WHERE name=? COLLATE NOCASE",
                    (collection["name"],),
                ).fetchone()["id"]
                connection.execute(
                    """
                    INSERT INTO resource_collections(
                      resource_id, collection_id, role, reason, confidence, origin, accepted, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(resource_id, collection_id) DO UPDATE SET
                      role=excluded.role,
                      reason=excluded.reason,
                      confidence=excluded.confidence,
                      origin=excluded.origin,
                      accepted=excluded.accepted
                    """,
                    (
                        current_resource_id,
                        actual_collection,
                        collection["role"],
                        collection["reason"],
                        collection["confidence"],
                        collection["origin"],
                        int(collection["accepted"]),
                        now,
                    ),
                )
                membership_count += 1

            if semantic_before is not None:
                record_direct_semantic_change(connection, semantic_before)

            for task_value in entry.get("tasks") or []:
                if not isinstance(task_value, dict):
                    continue
                title = str(task_value.get("title") or "").strip()[:1000]
                if not title:
                    continue
                status = str(task_value.get("status") or "open")
                if status not in TASK_STATUSES:
                    raise ValueError(f"Invalid task status: {status}")
                task_id = str(task_value.get("taskId") or "")
                if not task_id:
                    seed = f"{current_resource_id}|{title.casefold()}"
                    task_id = (
                        "task_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
                    )
                connection.execute(
                    """
                    INSERT INTO tasks(
                      id, resource_id, collection_id, title, status, notes, due_at, created_at, updated_at
                    ) VALUES(?, ?, NULL, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      title=excluded.title,
                      status=excluded.status,
                      notes=excluded.notes,
                      due_at=excluded.due_at,
                      updated_at=excluded.updated_at
                    """,
                    (
                        task_id,
                        current_resource_id,
                        title,
                        status,
                        _optional_text(task_value, "notes", 5000),
                        _optional_text(task_value, "dueAt", 100),
                        now,
                        now,
                    ),
                )
                task_count += 1
    return {
        "resourcesUpdated": updated,
        "membershipsApplied": membership_count,
        "tasksApplied": task_count,
    }


def _optional_text(item: dict[str, Any], key: str, maximum: int) -> str | None:
    if key not in item or item[key] is None:
        return None
    return str(item[key]).strip()[:maximum]


def _confidence(value: Any) -> float:
    number = float(value)
    return max(0.0, min(1.0, number))


def _normalize_collection_input(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        item = {"name": value}
    elif isinstance(value, dict):
        item = value
    else:
        raise ValueError("Collection must be a name or object")
    name = str(item.get("name") or "").strip()[:200]
    if not name:
        raise ValueError("Collection name is required")
    kind = str(item.get("kind") or "theme")[:50]
    parent_name = str(
        item.get("parent")
        or item.get("parentName")
        or TOPIC_PARENT_DEFAULTS.get(name, "")
    ).strip()[:200]
    parent_id = str(item.get("parentId") or "").strip()[:100]
    if parent_name and parent_name.casefold() == name.casefold():
        raise ValueError("Collection cannot be its own parent")
    return {
        "name": name,
        "kind": kind,
        "description": _optional_text(item, "description", 2000),
        "objective": _optional_text(item, "objective", 2000),
        "parentName": parent_name,
        "parentId": parent_id,
        "role": str(item.get("role") or "reference")[:100],
        "reason": _optional_text(item, "reason", 3000),
        "confidence": _confidence(item.get("confidence", 0.5)),
        "origin": str(item.get("origin") or "codex")[:50],
        "accepted": bool(item.get("accepted", False)),
    }


def _collection_id(name: str) -> str:
    return "col_" + hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:24]
