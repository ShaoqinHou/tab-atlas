from __future__ import annotations

from collections import Counter
from typing import Any

from ..constants import SPACE_DEFINITIONS


def report_collection_summaries(
    resources: list[dict[str, Any]],
    collections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for collection in collections:
        members = [
            item
            for item in resources
            if any(value["id"] == collection["id"] for value in item["collections"])
        ]
        format_counts = Counter(item["presentation"]["format"] for item in members)
        intent_counts = Counter(item["presentation"]["intent"] for item in members)
        queue_counts = Counter(item["presentation"]["queue"] for item in members)
        topic_counts = Counter(
            value["name"]
            for item in members
            for value in item["collections"]
            if value["kind"] == "topic"
        )
        focus_counts = Counter(
            value["name"]
            for item in members
            for value in item["collections"]
            if value["kind"] == "focus"
        )
        top_formats = [
            {"name": name, "count": count}
            for name, count in format_counts.most_common(3)
        ]
        top_intents = [
            {"name": name, "count": count}
            for name, count in intent_counts.most_common(3)
        ]
        description = str(collection.get("description") or "").strip()
        if collection["kind"] == "space" and collection["name"] in SPACE_DEFINITIONS:
            description = SPACE_DEFINITIONS[collection["name"]]
        if not description and top_formats:
            description = f"Mostly {top_formats[0]['name'].casefold()} resources"
            if top_intents:
                description += f" for {top_intents[0]['name'].casefold()} decisions"
            description += "."
        result.append(
            {
                "id": collection["id"],
                "name": collection["name"],
                "kind": collection["kind"],
                "parentId": str(collection.get("parent_id") or ""),
                "parentName": str(collection.get("parentName") or ""),
                "description": description or "No resources assigned yet.",
                "objective": str(collection.get("objective") or ""),
                "resourceCount": len(members),
                "resourceIds": [item["resourceId"] for item in members],
                "previewResourceIds": [
                    item["resourceId"]
                    for item in sorted(
                        members,
                        key=lambda value: (
                            not bool(value["presentation"]["preview"]["localImage"]),
                            value["title"].casefold(),
                        ),
                    )[:3]
                ],
                "topTopics": [
                    {"name": name, "count": count}
                    for name, count in topic_counts.most_common(5)
                ],
                "topFocuses": [
                    {"name": name, "count": count}
                    for name, count in focus_counts.most_common(5)
                ],
                "topFormats": top_formats,
                "topIntents": top_intents,
                "queueCounts": dict(queue_counts),
            }
        )
    return sorted(
        result, key=lambda item: (-item["resourceCount"], item["name"].casefold())
    )
