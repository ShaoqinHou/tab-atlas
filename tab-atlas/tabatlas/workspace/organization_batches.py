from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..database import utc_now
from .agent_requests import normalize_agent_response
from .semantics import apply_semantic_proposal, reject_semantic_proposal
from .support import (
    ConflictError,
    _bounded_label,
    _bounded_text,
    _canonical_json,
    _collection_name,
    _new_id,
    _sha256_text,
    _validate_idempotency_key,
)


ANALYSIS_STATES = {"proposed", "needs_context", "unchanged"}
BATCH_SCOPES = {"unorganized", "library"}


def stage_organization_batch(
    connection: sqlite3.Connection,
    document: dict[str, Any],
    *,
    source: str = "codex_delegated",
) -> dict[str, Any]:
    normalized = _normalize_batch_document(connection, document, source)
    existing = connection.execute(
        "SELECT id FROM organization_batches WHERE plan_hash=?",
        (normalized["planHash"],),
    ).fetchone()
    if existing:
        return organization_batch_detail(connection, existing["id"])

    batch_id = _new_id("orgbatch")
    now = utc_now()
    with connection:
        connection.execute(
            """
            INSERT INTO organization_batches(
              id, scope, source, catalog_revision, plan_hash, strategy_json,
              target_count, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                normalized["scope"],
                normalized["source"],
                normalized["catalogRevision"],
                normalized["planHash"],
                _canonical_json(normalized["strategy"]),
                len(normalized["items"]),
                now,
            ),
        )
        for item in normalized["items"]:
            proposal_id = None
            if item["analysisState"] == "proposed":
                proposal_id = _new_id("proposal")
                response = item["response"]
                connection.execute(
                    """
                    INSERT INTO semantic_proposals(
                      id, resource_id, note_id, request_id, base_revision, proposer,
                      model, thread_id, rationale, operations_json, evidence_json,
                      input_hash, prompt_version, created_at
                    ) VALUES(?, ?, NULL, NULL, ?, 'codex_delegated', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal_id,
                        item["resourceId"],
                        item["baseRevision"],
                        normalized["model"],
                        normalized["threadId"],
                        item["rationale"],
                        _canonical_json(response),
                        _canonical_json(
                            [
                                {
                                    "kind": "organization_batch",
                                    "id": batch_id,
                                    "evidenceClass": item["evidenceClass"],
                                }
                            ]
                        ),
                        _sha256_text(
                            _canonical_json(
                                {
                                    "catalogRevision": normalized["catalogRevision"],
                                    "item": item,
                                }
                            )
                        ),
                        "organization-batch-v1",
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO organization_batch_items(
                  batch_id, resource_id, proposal_id, analysis_state,
                  base_revision, confidence, evidence_class,
                  proposed_path_json, rationale, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    item["resourceId"],
                    proposal_id,
                    item["analysisState"],
                    item["baseRevision"],
                    item["confidence"],
                    item["evidenceClass"],
                    _canonical_json(item["proposedPath"]),
                    item["rationale"],
                    now,
                ),
            )
    return organization_batch_detail(connection, batch_id)


def organization_batch_summaries(
    connection: sqlite3.Connection,
    limit: int = 5,
    *,
    include_items: bool = True,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id FROM organization_batches
        ORDER BY created_at DESC, rowid DESC LIMIT ?
        """,
        (max(1, min(int(limit), 20)),),
    ).fetchall()
    batches = [organization_batch_detail(connection, row["id"]) for row in rows]
    if include_items:
        return batches
    return [
        {key: value for key, value in batch.items() if key != "items"}
        for batch in batches
    ]


def organization_batch_detail(
    connection: sqlite3.Connection,
    batch_id: str,
) -> dict[str, Any]:
    batch = connection.execute(
        "SELECT * FROM organization_batches WHERE id=?", (batch_id,)
    ).fetchone()
    if not batch:
        raise ValueError("Organization batch was not found")
    try:
        strategy = json.loads(batch["strategy_json"] or "{}")
    except json.JSONDecodeError:
        strategy = {}

    items = []
    counts = {
        "proposed": 0,
        "needsContext": 0,
        "unchanged": 0,
        "accepted": 0,
        "rejected": 0,
        "stale": 0,
        "superseded": 0,
    }
    for row in connection.execute(
        """
        SELECT obi.*, r.semantic_revision, sd.decision,
          EXISTS(
            SELECT 1 FROM resource_collections rc
            JOIN collections c ON c.id=rc.collection_id
            WHERE rc.resource_id=obi.resource_id AND c.kind='space'
          ) AS has_space
        FROM organization_batch_items obi
        JOIN resources r ON r.id=obi.resource_id
        LEFT JOIN semantic_decisions sd ON sd.proposal_id=obi.proposal_id
        WHERE obi.batch_id=?
        ORDER BY obi.analysis_state, obi.confidence DESC, obi.resource_id
        """,
        (batch_id,),
    ):
        state = row["analysis_state"]
        if row["decision"] == "accepted":
            effective_state = "accepted"
        elif row["decision"] == "rejected":
            effective_state = "rejected"
        elif (
            state in {"proposed", "needs_context"}
            and row["semantic_revision"] != row["base_revision"]
            and row["has_space"]
        ):
            effective_state = "superseded"
        elif state == "proposed" and row["semantic_revision"] != row["base_revision"]:
            effective_state = "stale"
        elif state == "needs_context":
            effective_state = "needsContext"
        else:
            effective_state = state
        counts[effective_state] += 1
        try:
            proposed_path = json.loads(row["proposed_path_json"] or "[]")
        except json.JSONDecodeError:
            proposed_path = []
        items.append(
            {
                "resourceId": row["resource_id"],
                "proposalId": row["proposal_id"] or "",
                "analysisState": state,
                "effectiveState": effective_state,
                "baseRevision": int(row["base_revision"]),
                "currentRevision": int(row["semantic_revision"]),
                "confidence": float(row["confidence"]),
                "evidenceClass": row["evidence_class"],
                "proposedPath": proposed_path,
                "rationale": row["rationale"],
            }
        )
    pending = counts["proposed"]
    if pending:
        status = "ready"
    elif (counts["accepted"] or counts["superseded"]) and counts["needsContext"]:
        status = "applied_with_context_remaining"
    elif counts["accepted"] or counts["superseded"]:
        status = "applied"
    elif counts["rejected"]:
        status = "reviewed"
    else:
        status = "analyzed"
    return {
        "id": batch["id"],
        "scope": batch["scope"],
        "source": batch["source"],
        "catalogRevision": batch["catalog_revision"],
        "planHash": batch["plan_hash"],
        "targetCount": int(batch["target_count"]),
        "analyzedCount": len(items),
        "status": status,
        "counts": counts,
        "strategy": strategy,
        "items": items,
        "createdAt": batch["created_at"],
    }


def decide_organization_batch(
    connection: sqlite3.Connection,
    batch_id: str,
    decision: str,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    if decision not in {"accept", "reject"}:
        raise ValueError("Organization batch decision is invalid")
    request_key = _validate_idempotency_key(idempotency_key)
    before = organization_batch_detail(connection, batch_id)
    candidates = [
        item for item in before["items"] if item["effectiveState"] == "proposed"
    ]
    applied = 0
    skipped = 0
    conflicts = []
    for index, item in enumerate(candidates):
        item_key = f"{request_key}:{index:04d}"
        try:
            if decision == "accept":
                apply_semantic_proposal(
                    connection,
                    item["proposalId"],
                    actor,
                    item_key,
                )
            else:
                reject_semantic_proposal(
                    connection,
                    item["proposalId"],
                    actor,
                    item_key,
                )
            applied += 1
        except (ConflictError, ValueError) as error:
            skipped += 1
            conflicts.append(
                {
                    "resourceId": item["resourceId"],
                    "code": "organization_conflict",
                    "message": str(error),
                }
            )
            if decision == "accept":
                with connection:
                    connection.execute(
                        """
                        UPDATE organization_batch_items
                        SET analysis_state='needs_context', rationale=?
                        WHERE batch_id=? AND resource_id=?
                        """,
                        (
                            _bounded_text(
                                "Existing organization safeguards blocked automatic "
                                f"application: {error}",
                                1000,
                                "Automatic application needs user context",
                            ),
                            batch_id,
                            item["resourceId"],
                        ),
                    )
    return {
        "batch": organization_batch_detail(connection, batch_id),
        "decision": decision,
        "decided": applied,
        "skipped": skipped,
        "conflicts": conflicts,
    }


def defer_organization_batch(
    connection: sqlite3.Connection,
    batch_id: str,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    request_key = _validate_idempotency_key(idempotency_key)
    existing = connection.execute(
        "SELECT * FROM organization_batch_actions WHERE idempotency_key=?",
        (request_key,),
    ).fetchone()
    if existing:
        if existing["batch_id"] != batch_id or existing["action"] != "defer_context":
            raise ConflictError("Idempotency key was already used for another action")
        return {
            "batch": organization_batch_detail(connection, batch_id),
            "decision": "defer",
            "decided": int(existing["affected_count"]),
            "skipped": 0,
        }

    before = organization_batch_detail(connection, batch_id)
    resource_ids = [
        item["resourceId"]
        for item in before["items"]
        if item["effectiveState"] == "needsContext"
    ]
    rationale = (
        "Deliberately left in its current organization; remains saved and searchable."
        if before["scope"] == "library"
        else "Deliberately left unorganized; remains saved and searchable."
    )
    now = utc_now()
    with connection:
        if resource_ids:
            placeholders = ",".join("?" for _ in resource_ids)
            connection.execute(
                f"""
                UPDATE organization_batch_items
                SET analysis_state='unchanged', rationale=?
                WHERE analysis_state='needs_context'
                  AND resource_id IN ({placeholders})
                """,
                [rationale, *resource_ids],
            )
        connection.execute(
            """
            INSERT INTO organization_batch_actions(
              id, batch_id, action, actor, idempotency_key, affected_count, created_at
            ) VALUES(?, ?, 'defer_context', ?, ?, ?, ?)
            """,
            (
                _new_id("orgaction"),
                batch_id,
                _bounded_label(actor, 60, "user"),
                request_key,
                len(resource_ids),
                now,
            ),
        )
    return {
        "batch": organization_batch_detail(connection, batch_id),
        "decision": "defer",
        "decided": len(resource_ids),
        "skipped": 0,
    }


def _normalize_batch_document(
    connection: sqlite3.Connection,
    document: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise ValueError("Organization plan schemaVersion must be 1")
    scope = str(document.get("scope") or "").strip().casefold()
    if scope not in BATCH_SCOPES:
        raise ValueError("Organization plan scope is invalid")
    raw_items = document.get("items")
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 5000:
        raise ValueError("Organization plan items must contain 1 to 5000 resources")
    target_ids = document.get("targetResourceIds")
    if target_ids is None:
        target_ids = [
            item.get("resourceId") for item in raw_items if isinstance(item, dict)
        ]
    if not isinstance(target_ids, list) or not target_ids:
        raise ValueError("Organization plan targetResourceIds must be an array")
    normalized_target_ids = [str(value or "").strip() for value in target_ids]
    if any(not value for value in normalized_target_ids):
        raise ValueError("Organization plan targetResourceIds must be non-empty")
    if len(normalized_target_ids) != len(raw_items):
        raise ValueError("Organization plan must contain one item per target resource")
    if len(set(normalized_target_ids)) != len(normalized_target_ids):
        raise ValueError("Organization plan targetResourceIds contain duplicates")

    resource_rows = {
        row["id"]: row
        for row in connection.execute(
            """
            SELECT id, library_state, semantic_revision
            FROM resources WHERE id IN (%s)
            """
            % ",".join("?" for _ in normalized_target_ids),
            normalized_target_ids,
        )
    }
    if set(resource_rows) != set(normalized_target_ids):
        raise ValueError("Organization plan references an unknown resource")
    if any(row["library_state"] != "accepted" for row in resource_rows.values()):
        raise ValueError("Organization plans may target only accepted resources")

    normalized_items = []
    seen_items: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Organization plan item must be an object")
        resource_id = str(raw.get("resourceId") or "").strip()
        if resource_id in seen_items:
            raise ValueError("Organization plan contains a duplicate resource")
        if resource_id not in resource_rows:
            raise ValueError("Organization plan item is outside the target cohort")
        seen_items.add(resource_id)
        state = str(raw.get("analysisState") or "").strip().casefold()
        if state not in ANALYSIS_STATES:
            raise ValueError("Organization plan analysisState is invalid")
        try:
            base_revision = int(raw.get("baseRevision"))
        except (TypeError, ValueError) as error:
            raise ValueError("Organization plan baseRevision is invalid") from error
        if base_revision != int(resource_rows[resource_id]["semantic_revision"]):
            raise ConflictError("A targeted resource changed after analysis")
        try:
            confidence = float(raw.get("confidence", 0))
        except (TypeError, ValueError) as error:
            raise ValueError("Organization plan confidence is invalid") from error
        if not 0 <= confidence <= 1:
            raise ValueError("Organization plan confidence must be between 0 and 1")
        rationale = _bounded_text(raw.get("rationale"), 1000, "Batch analysis")
        evidence_class = _bounded_label(raw.get("evidenceClass"), 60, "metadata")
        response = None
        proposed_path: list[str] = []
        if state == "proposed":
            response = normalize_agent_response(
                {
                    "message": rationale,
                    "interpretation": rationale,
                    "confidence": confidence,
                    "needsReview": bool(raw.get("needsReview", False)),
                    "memberships": raw.get("memberships") or [],
                    "actionItem": None,
                    "navigation": {"command": "none"},
                }
            )
            primary = {
                item["kind"]: item
                for item in response["memberships"]
                if item["kind"] in {"space", "topic", "focus"}
            }
            if "space" not in primary:
                raise ValueError("A proposed organization path requires a Space")
            if "topic" in primary and (
                primary["topic"]["parentName"].casefold()
                != primary["space"]["name"].casefold()
            ):
                raise ValueError("A proposed Topic must belong to the proposed Space")
            if "focus" in primary and (
                "topic" not in primary
                or primary["focus"]["parentName"].casefold()
                != primary["topic"]["name"].casefold()
            ):
                raise ValueError("A proposed Focus must belong to the proposed Topic")
            proposed_path = [
                primary[kind]["name"]
                for kind in ("space", "topic", "focus")
                if kind in primary
            ]
        elif raw.get("memberships"):
            raise ValueError("Only proposed items may include memberships")
        normalized_items.append(
            {
                "resourceId": resource_id,
                "analysisState": state,
                "baseRevision": base_revision,
                "confidence": confidence,
                "evidenceClass": evidence_class,
                "rationale": rationale,
                "proposedPath": proposed_path,
                "response": response,
            }
        )
    if seen_items != set(normalized_target_ids):
        raise ValueError("Organization plan items do not cover the target cohort")

    strategy = _normalize_strategy(document.get("strategy"))
    normalized = {
        "scope": scope,
        "source": _bounded_label(source, 60, "codex_delegated"),
        "catalogRevision": _bounded_label(
            document.get("catalogRevision"), 100, "unknown"
        ),
        "model": _bounded_label(document.get("model"), 80, ""),
        "threadId": _bounded_label(document.get("threadId"), 100, ""),
        "strategy": strategy,
        "items": sorted(normalized_items, key=lambda item: item["resourceId"]),
    }
    normalized["planHash"] = hashlib.sha256(
        _canonical_json(normalized).encode("utf-8")
    ).hexdigest()
    return normalized


def _normalize_strategy(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    principles = (
        raw.get("principles") if isinstance(raw.get("principles"), list) else []
    )
    groups = raw.get("groups") if isinstance(raw.get("groups"), list) else []
    normalized_groups = []
    for group in groups[:100]:
        if not isinstance(group, dict):
            continue
        path = group.get("path") if isinstance(group.get("path"), list) else []
        normalized_path = [_collection_name(item) for item in path[:3]]
        try:
            count = max(0, int(group.get("count", 0)))
        except (TypeError, ValueError):
            count = 0
        normalized_groups.append(
            {
                "path": normalized_path,
                "count": count,
                "reason": _bounded_text(group.get("reason"), 600, "Global pattern"),
            }
        )
    return {
        "mode": (
            "best_effort"
            if str(raw.get("mode") or "").strip().casefold() == "best_effort"
            else "conservative"
        ),
        "summary": _bounded_text(
            raw.get("summary"), 1600, "Whole-cohort organization analysis"
        ),
        "principles": [
            _bounded_text(item, 300, "")
            for item in principles[:12]
            if str(item).strip()
        ],
        "groups": normalized_groups,
    }
