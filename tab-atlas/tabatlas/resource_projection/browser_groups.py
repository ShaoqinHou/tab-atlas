from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any


def report_group_summaries(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    resources_by_id = {item["resourceId"]: item for item in resources}
    for resource in resources:
        for tab in resource["tabs"]:
            if tab["groupId"] in {None, "", "-1"}:
                continue
            identifier = group_identifier(tab)
            group = groups.setdefault(
                identifier,
                {
                    "id": identifier,
                    "browser": tab["browser"],
                    "windowId": str(tab.get("windowId") or ""),
                    "groupId": str(tab.get("groupId") or ""),
                    "title": tab.get("groupTitle")
                    or f"Unnamed group {tab.get('groupId')}",
                    "color": tab.get("groupColor") or "grey",
                    "collapsed": bool(tab.get("groupCollapsed")),
                    "tabCount": 0,
                    "resourceIds": [],
                },
            )
            group["tabCount"] += 1
            if resource["resourceId"] not in group["resourceIds"]:
                group["resourceIds"].append(resource["resourceId"])

    title_counts = Counter(
        (group["browser"], group["windowId"], str(group["title"]).casefold())
        for group in groups.values()
    )
    result = []
    for group in groups.values():

        def group_position(resource_id: str) -> tuple[int, str]:
            resource = resources_by_id[resource_id]
            positions = [
                int(tab["position"])
                for tab in resource.get("contexts") or resource["tabs"]
                if group_identifier(tab) == group["id"]
                and tab.get("position") is not None
            ]
            return (
                min(positions) if positions else 1_000_000_000,
                resource["title"].casefold(),
            )

        group["resourceIds"].sort(key=group_position)
        members = [resources_by_id[resource_id] for resource_id in group["resourceIds"]]
        collection_counts = Counter(
            collection["name"] for item in members for collection in item["collections"]
        )
        format_counts = Counter(item["presentation"]["format"] for item in members)
        intent_counts = Counter(item["presentation"]["intent"] for item in members)
        queue_counts = Counter(item["presentation"]["queue"] for item in members)
        duplicate_resources = sum(
            1
            for item in members
            if item["presentation"]["duplicates"]["safeCloseCandidates"]
        )
        top_collections = [name for name, _count in collection_counts.most_common(3)]
        top_formats = [
            {"name": name, "count": count}
            for name, count in format_counts.most_common(3)
        ]
        top_intents = [
            {"name": name, "count": count}
            for name, count in intent_counts.most_common(3)
        ]
        display_title = group["title"]
        title_key = (
            group["browser"],
            group["windowId"],
            str(group["title"]).casefold(),
        )
        if title_counts[title_key] > 1:
            display_title = f"{display_title} ({group['color']})"
        summary_bits = []
        if top_collections:
            summary_bits.append(f"Mostly {top_collections[0]}.")
        elif top_formats:
            summary_bits.append(
                f"Mostly {top_formats[0]['name'].casefold()} resources."
            )
        review_count = queue_counts.get("needs_context", 0)
        if review_count:
            verb = "needs" if review_count == 1 else "need"
            summary_bits.append(f"{review_count} {verb} context.")
        if duplicate_resources:
            verb = "has" if duplicate_resources == 1 else "have"
            summary_bits.append(f"{duplicate_resources} {verb} multiple open copies.")
        group.update(
            {
                "displayTitle": display_title,
                "resourceCount": len(members),
                "summary": " ".join(summary_bits) or "A captured browser group.",
                "topCollections": top_collections,
                "topFormats": top_formats,
                "topIntents": top_intents,
                "queueCounts": dict(queue_counts),
                "duplicateResources": duplicate_resources,
            }
        )
        result.append(group)
    return sorted(
        result,
        key=lambda item: (
            -item["resourceCount"],
            item["browser"],
            item["displayTitle"].casefold(),
        ),
    )


def group_identifier(tab: dict[str, Any]) -> str:
    seed = "|".join(
        (
            str(tab.get("browser") or ""),
            str(tab.get("windowId") or ""),
            str(tab.get("groupId") or ""),
        )
    )
    return "group_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
