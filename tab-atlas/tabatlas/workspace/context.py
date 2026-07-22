from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..catalog import inventory, library_resources
from ..constants import SPACE_DEFINITIONS
from ..presentation import resource_presentation
from .directory import action_list_summaries
from .notes import active_note_text, resource_notes
from .semantics import proposal_is_current


def _note_agent_context(
    connection: sqlite3.Connection,
    resource_id: str,
    note_id: str,
) -> dict[str, Any]:
    resource = _resource_by_id(connection, resource_id)
    note_text = active_note_text(connection, note_id)
    return {
        "resource": _bounded_resource_context(resource),
        "userNote": note_text,
        "existingMemberships": [
            {
                "name": item["name"],
                "kind": item["kind"],
                "parentName": item.get("parentName") or "",
                "authority": item.get("authority") or "legacy_effective",
            }
            for item in resource.get("collections") or []
        ],
        "allowedSpaces": list(SPACE_DEFINITIONS),
        "existingCollections": _existing_collection_index(connection),
    }


def _workspace_agent_context(
    connection: sqlite3.Connection,
    selected_resource_id: str | None,
    request_text: str,
) -> dict[str, Any]:
    resources = library_resources(connection)
    selected = None
    if selected_resource_id:
        selected = next(
            (
                resource
                for resource in resources
                if resource["resourceId"] == selected_resource_id
            ),
            None,
        )
    selected_memberships = (
        {
            str(item.get("name") or "").casefold()
            for item in (selected.get("collections") or [])
        }
        if selected
        else set()
    )
    if selected:
        related = []
        for resource in resources:
            if resource["resourceId"] == selected_resource_id:
                continue
            overlap = len(
                selected_memberships
                & {
                    str(item.get("name") or "").casefold()
                    for item in (resource.get("collections") or [])
                }
            )
            if overlap:
                related.append(
                    (overlap, str(resource.get("title") or "").casefold(), resource)
                )
        related.sort(key=lambda item: (-item[0], item[1], item[2]["resourceId"]))
        candidates = [item[2] for item in related[:12]]
        retrieval = "selected_and_related"
    else:
        candidates = _rank_workspace_resources(resources, request_text, 24)
        retrieval = "request_terms"
    index = [_bounded_resource_context(resource) for resource in candidates]
    action_lists = [
        {
            "id": item["id"],
            "name": item["name"],
            "description": item["description"],
            "objective": item["objective"],
            "workflowKind": item["workflowKind"],
            "resourceCount": item["resourceCount"],
            "stateCounts": item["stateCounts"],
        }
        for item in action_list_summaries(connection)
    ]
    selected_notes = []
    selected_proposals = []
    if selected_resource_id:
        for note in resource_notes(connection, selected_resource_id)[:6]:
            transcript = note.get("processing", {}).get("transcription", {})
            interpretation = note.get("processing", {}).get("interpretation", {})
            selected_notes.append(
                {
                    "noteId": note["id"],
                    "kind": note["kind"],
                    "text": (
                        note["text"]
                        if note["kind"] == "text"
                        else str(transcript.get("outputText") or "")
                    )[:3000],
                    "interpretation": str(interpretation.get("outputText") or "")[
                        :2000
                    ],
                }
            )
        for row in connection.execute(
            """
            SELECT p.id, p.operations_json
            FROM semantic_proposals p
            LEFT JOIN semantic_decisions d ON d.proposal_id=p.id
            WHERE p.resource_id=? AND d.id IS NULL
            ORDER BY p.created_at DESC, p.rowid DESC LIMIT 5
            """,
            (selected_resource_id,),
        ):
            if not proposal_is_current(connection, row["id"]):
                continue
            try:
                operations = json.loads(row["operations_json"] or "{}")
            except json.JSONDecodeError:
                operations = {}
            selected_proposals.append(
                {
                    "proposalId": row["id"],
                    "message": str(operations.get("message") or "")[:1200],
                    "interpretation": str(operations.get("interpretation") or "")[
                        :2000
                    ],
                    "memberships": (operations.get("memberships") or [])[:12],
                    "actionItem": operations.get("actionItem"),
                }
            )
    return {
        "inventory": inventory(connection),
        "selectedResource": _bounded_resource_context(selected) if selected else None,
        "actionLists": action_lists,
        "collections": _existing_collection_index(connection),
        "resourceIndex": index,
        "resourceIndexRetrieval": retrieval,
        "resourceIndexMatched": len(index),
        "libraryResourceCount": len(resources),
        "resourceIndexTruncated": len(resources) > len(index),
        "selectedUserNotes": selected_notes,
        "selectedPendingProposals": selected_proposals,
    }


def _resource_by_id(connection: sqlite3.Connection, resource_id: str) -> dict[str, Any]:
    resource = next(
        (
            item
            for item in library_resources(connection)
            if item["resourceId"] == resource_id
        ),
        None,
    )
    if not resource:
        raise ValueError("Accepted resource was not found")
    return resource


def _bounded_resource_context(resource: dict[str, Any] | None) -> dict[str, Any] | None:
    if not resource:
        return None
    presentation = resource_presentation(resource)
    return {
        "resourceId": resource["resourceId"],
        "title": str(resource.get("title") or "")[:300],
        "brief": str(resource.get("brief") or "")[:600],
        "detail": str(resource.get("detail") or "")[:800],
        "whyKept": str(resource.get("whyKept") or "")[:500],
        "nextAction": str(resource.get("nextAction") or "")[:300],
        "host": str(resource.get("host") or "")[:200],
        "kind": str(resource.get("kind") or "")[:100],
        "source": presentation["source"],
        "format": presentation["format"],
        "noteCount": int(resource.get("noteCount") or 0),
        "memberships": [
            {"name": item["name"], "kind": item["kind"]}
            for item in (resource.get("collections") or [])[:12]
        ],
        "actionItems": (resource.get("actionItems") or [])[:6],
    }


def _rank_workspace_resources(
    resources: list[dict[str, Any]],
    request_text: str,
    limit: int,
) -> list[dict[str, Any]]:
    stop_words = {
        "about",
        "all",
        "and",
        "ask",
        "can",
        "codex",
        "find",
        "for",
        "from",
        "help",
        "library",
        "me",
        "my",
        "of",
        "on",
        "resource",
        "resources",
        "show",
        "tab",
        "tabs",
        "the",
        "this",
        "to",
        "what",
        "with",
    }
    folded_request = request_text.casefold()
    terms = {
        value
        for value in re.findall(r"[^\W_]{2,}", folded_request, flags=re.UNICODE)
        if value not in stop_words
    }
    if not terms:
        return []
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for resource in resources:
        presentation = resource_presentation(resource)
        fields = [
            str(resource.get("title") or ""),
            str(resource.get("brief") or ""),
            str(resource.get("nextAction") or ""),
            str(resource.get("host") or ""),
            str(presentation.get("source") or ""),
            str(presentation.get("format") or ""),
            " ".join(
                str(item.get("name") or "")
                for item in resource.get("collections") or []
            ),
        ]
        haystack = "\n".join(fields).casefold()
        score = sum(
            4 if term in fields[0].casefold() else 1
            for term in terms
            if term in haystack
        )
        if score:
            ranked.append(
                (score, str(resource.get("title") or "").casefold(), resource)
            )
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]["resourceId"]))
    return [item[2] for item in ranked[: max(1, min(limit, 40))]]


def _existing_collection_index(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "name": row["name"],
            "kind": row["kind"],
            "parentName": row["parentName"] or "",
            "resourceCount": row["resourceCount"],
        }
        for row in connection.execute(
            """
            SELECT c.name, c.kind, parent.name AS parentName,
                   COUNT(rc.resource_id) AS resourceCount
            FROM collections c
            LEFT JOIN collections parent ON parent.id=c.parent_id
            LEFT JOIN resource_collections rc ON rc.collection_id=c.id
            WHERE c.status='active'
            GROUP BY c.id
            ORDER BY c.kind, resourceCount DESC, c.name COLLATE NOCASE
            LIMIT 160
            """
        )
    ]


def _safe_error_code(error: BaseException) -> str:
    text = str(error).casefold()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:80] or "agent_unavailable"
