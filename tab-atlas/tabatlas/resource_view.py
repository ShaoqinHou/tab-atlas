from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .catalog import _collection_authority_rank, exact_duplicate_summary
from .constants import SPACE_DEFINITIONS
from .media import _x_motion_url_allowed, _youtube_video_id


def resource_presentation(resource: dict[str, Any]) -> dict[str, Any]:
    url = str(resource.get("openUrl") or resource.get("canonicalUrl") or "")
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = urlsplit("")
    host = str(resource.get("host") or parts.hostname or "").casefold()
    kind = str(resource.get("kind") or "web_page")
    title = str(resource.get("title") or "")
    source = _source_label(host, kind, parts.scheme)
    source_group = _source_group(source, parts.scheme)
    publisher = _publisher_label(source, parts)
    if not publisher and source_group == "Other websites":
        publisher = source
    format_label = _format_label(host, kind, title, parts.path, parts.scheme)
    intent = _intent_label(str(resource.get("nextAction") or ""), format_label)
    collections = sorted(
        resource.get("collections") or [],
        key=lambda item: (
            _collection_authority_rank(str(item.get("authority") or "")),
            str(item.get("name") or "").casefold(),
        ),
    )
    spaces = [
        collection for collection in collections if collection.get("kind") == "space"
    ]
    topics = [
        collection for collection in collections if collection.get("kind") == "topic"
    ]
    focuses = [
        collection for collection in collections if collection.get("kind") == "focus"
    ]
    projects = [
        collection for collection in collections if collection.get("kind") == "project"
    ]
    action_lists = [
        collection
        for collection in collections
        if collection.get("kind") == "action_list"
    ]
    tabs = resource.get("tabs") or []
    context_tabs = resource.get("contexts") or tabs
    tasks = resource.get("tasks") or []
    group_titles = sorted(
        {
            str(tab.get("groupTitle") or "")
            for tab in context_tabs
            if tab.get("groupTitle")
        }
    )
    duplicate = exact_duplicate_summary(resource)
    duplicate_count = duplicate["safeCloseCandidates"]
    status = str(resource.get("status") or "open")
    library_state = str(resource.get("libraryState") or "accepted")

    if library_state == "candidate":
        queue = "discovery"
        decision_label = "New discovery"
        decision_tone = "review"
    elif status == "close_candidate":
        queue = "close_candidate"
        decision_label = "Close candidate"
        decision_tone = "close"
    elif status == "saved":
        queue = "kept"
        decision_label = "Kept"
        decision_tone = "keep"
    elif any(str(task.get("status") or "") == "open" for task in tasks):
        queue = "task"
        decision_label = "Task attached"
        decision_tone = "action"
    elif action_lists:
        queue = "action_list"
        decision_label = action_lists[0]["name"]
        decision_tone = "action"
    elif not spaces:
        queue = "needs_context"
        decision_label = "Needs context"
        decision_tone = "review"
    elif duplicate_count:
        queue = "duplicate"
        decision_label = "Consolidate"
        decision_tone = "duplicate"
    elif group_titles:
        queue = "grouped"
        decision_label = "In a group"
        decision_tone = "grouped"
    else:
        queue = "reference"
        decision_label = "Reference"
        decision_tone = "reference"

    next_action = str(resource.get("nextAction") or "").strip()
    if library_state == "candidate":
        decision_cue = "Review before adding this resource to the library."
    elif next_action and next_action.casefold() != "none":
        decision_cue = _sentence(next_action)
    elif duplicate_count:
        noun = "tab" if duplicate_count == 1 else "tabs"
        decision_cue = f"{duplicate_count} exact duplicate {noun} can be closed safely."
    elif not spaces:
        decision_cue = "Decide whether this still matters."
    else:
        decision_cue = "Keep as a reference."

    why_kept = str(resource.get("whyKept") or "").strip()
    if int(resource.get("noteCount") or 0):
        context_cue = "Your note has priority over inferred context."
    elif why_kept:
        context_cue = _sentence(why_kept)
    elif group_titles:
        context_cue = f"Browser group context: {group_titles[0]}."
    elif spaces:
        context_cue = f"Reference for {spaces[0]['name']}."
    elif duplicate_count:
        context_cue = f"Open in {len(tabs)} tab instances."
    else:
        context_cue = "No durable context recorded yet."

    video_id = _youtube_video_id(url)
    preview_label = {
        "YouTube": "YT",
        "GitHub": "GH",
        "ChatGPT": "AI",
        "Claude": "AI",
        "Kimi": "AI",
        "PDF": "PDF",
        "Browser": "BR",
        "Local file": "FILE",
    }.get(source, _initials(source))
    accent = {
        "YouTube": "red",
        "GitHub": "charcoal",
        "ChatGPT": "green",
        "Claude": "coral",
        "Kimi": "blue",
        "PDF": "amber",
        "Browser": "grey",
        "Local file": "grey",
    }.get(source, "teal")
    preview_url = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if video_id else ""
    local_preview = ""
    if resource.get("previewLocalPath"):
        suffix = Path(str(resource["previewLocalPath"])).suffix.casefold()
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            local_preview = f"media/{resource['resourceId']}{suffix}"
    preview_policy = _preview_policy(
        source_group,
        format_label,
        url,
        str(resource.get("previewSource") or ""),
    )
    motion_preview = _motion_preview(resource, video_id)

    return {
        "displayTitle": _clean_display_title(title, source),
        "source": source,
        "sourceGroup": source_group,
        "publisher": publisher,
        "format": format_label,
        "intent": intent,
        "queue": queue,
        "decisionLabel": decision_label,
        "decisionTone": decision_tone,
        "decisionCue": decision_cue,
        "contextCue": context_cue,
        "groupTitles": group_titles,
        "space": spaces[0]["name"] if spaces else "",
        "topics": [collection["name"] for collection in topics[:2]],
        "focuses": [collection["name"] for collection in focuses[:2]],
        "projects": [collection["name"] for collection in projects],
        "actionLists": [collection["name"] for collection in action_lists],
        "openTabCount": len(tabs),
        "stored": status in {"saved", "archived"},
        "duplicates": duplicate,
        "preview": {
            "label": preview_label,
            "accent": accent,
            "localImage": local_preview,
            "remoteImage": preview_url,
            "requiresUserLoad": bool(preview_url and not local_preview),
            "motion": motion_preview,
            **preview_policy,
        },
    }


def _preview_policy(
    source_group: str,
    format_label: str,
    url: str,
    preview_source: str,
) -> dict[str, Any]:
    evidence_labels = {
        "youtube_public_thumbnail": "Video thumbnail",
        "x_public_video_thumbnail": "Video poster",
        "x_public_post_image": "Post image",
        "x_public_card_image": "Link preview",
        "github_public_social_image": "Repository preview",
        "reddit_public_post_image": "Post image",
        "agent_local_capture": "Page capture",
    }
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = urlsplit("")
    segments = [segment for segment in parts.path.split("/") if segment]
    is_x_post = (
        source_group == "X"
        and len(segments) >= 3
        and segments[1].casefold() == "status"
        and segments[2].isdigit()
    )
    is_reddit_post = source_group == "Reddit" and any(
        segment.casefold() == "comments" for segment in segments
    )

    if source_group == "YouTube":
        metadata_label, request_label, strategy = (
            "Video metadata",
            "video thumbnail",
            "public_media",
        )
    elif is_x_post:
        metadata_label, request_label, strategy = (
            "Post metadata",
            "post media",
            "public_media",
        )
    elif source_group == "GitHub":
        metadata_label, request_label, strategy = (
            "Repository metadata",
            "repository preview",
            "public_social_image",
        )
    elif is_reddit_post:
        metadata_label, request_label, strategy = (
            "Post metadata",
            "post media",
            "public_media",
        )
    elif format_label == "PDF":
        metadata_label, request_label, strategy = (
            "Document metadata",
            "first-page preview",
            "local_capture",
        )
    elif format_label == "Conversation":
        return {
            "strategy": "private_summary",
            "metadataLabel": "Private summary",
            "evidenceLabel": evidence_labels.get(preview_source, "Private capture"),
            "canRequestRicher": False,
            "requestLabel": "",
        }
    elif format_label == "Search":
        return {
            "strategy": "metadata_only",
            "metadataLabel": "Search context",
            "evidenceLabel": evidence_labels.get(preview_source, "Saved image"),
            "canRequestRicher": False,
            "requestLabel": "",
        }
    elif source_group in {"Browser", "Local file"}:
        return {
            "strategy": "metadata_only",
            "metadataLabel": "Local metadata",
            "evidenceLabel": evidence_labels.get(preview_source, "Saved image"),
            "canRequestRicher": False,
            "requestLabel": "",
        }
    else:
        metadata_label, request_label, strategy = (
            "Page metadata",
            "page preview",
            "local_capture",
        )

    return {
        "strategy": strategy,
        "metadataLabel": metadata_label,
        "evidenceLabel": evidence_labels.get(preview_source, "Saved image"),
        "canRequestRicher": True,
        "requestLabel": request_label,
    }


def _motion_preview(resource: dict[str, Any], youtube_video_id: str) -> dict[str, str]:
    if youtube_video_id:
        return {
            "kind": "youtube",
            "videoId": youtube_video_id,
            "label": "Play video preview",
        }
    motion_url = str(resource.get("motionUrl") or "")
    if resource.get("motionKind") == "x_mp4" and _x_motion_url_allowed(motion_url):
        return {
            "kind": "x_mp4",
            "url": motion_url,
            "label": "Play post video preview",
        }
    return {}


def report_group_summaries(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    resources_by_id = {item["resourceId"]: item for item in resources}
    for resource in resources:
        for tab in resource["tabs"]:
            if tab["groupId"] in {None, "", "-1"}:
                continue
            identifier = _group_identifier(tab)
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
                if _group_identifier(tab) == group["id"]
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


def report_source_summaries(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for resource in resources:
        grouped[str(resource["presentation"]["sourceGroup"])].append(resource)

    summaries = []
    for source, members in grouped.items():
        publisher_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
        topic_counts: Counter[str] = Counter()
        format_counts: Counter[str] = Counter()
        for resource in members:
            presentation = resource["presentation"]
            publisher = str(presentation.get("publisher") or "")
            if publisher:
                publisher_members[publisher].append(resource)
            format_counts[str(presentation["format"])] += 1
            topic_counts.update(
                collection["name"]
                for collection in resource.get("collections") or []
                if collection.get("kind") == "topic"
            )

        publishers = [
            {
                "id": "pub_"
                + hashlib.sha256(
                    f"{source.casefold()}\n{name.casefold()}".encode("utf-8")
                ).hexdigest()[:20],
                "name": name,
                "resourceCount": len(publisher_resources),
                "resourceIds": [item["resourceId"] for item in publisher_resources],
            }
            for name, publisher_resources in publisher_members.items()
        ]
        publishers.sort(
            key=lambda item: (-item["resourceCount"], item["name"].casefold())
        )
        preview_count = sum(
            bool(item["presentation"]["preview"]["localImage"]) for item in members
        )
        summaries.append(
            {
                "id": "src_"
                + hashlib.sha256(source.casefold().encode("utf-8")).hexdigest()[:20],
                "name": source,
                "resourceCount": len(members),
                "resourceIds": [item["resourceId"] for item in members],
                "previewCount": preview_count,
                "metadataPreviewCount": len(members) - preview_count,
                "attributedCount": sum(item["resourceCount"] for item in publishers),
                "publishers": publishers,
                "topTopics": [
                    {"name": name, "count": count}
                    for name, count in topic_counts.most_common(5)
                ],
                "topFormats": [
                    {"name": name, "count": count}
                    for name, count in format_counts.most_common(3)
                ],
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
            }
        )
    return sorted(
        summaries, key=lambda item: (-item["resourceCount"], item["name"].casefold())
    )


def _source_label(host: str, kind: str, scheme: str) -> str:
    if scheme == "file":
        return "Local file"
    if scheme in {"chrome", "chrome-extension", "edge", "edge-extension"}:
        return "Browser"
    if kind == "pdf":
        return "PDF"
    if kind == "browser_internal":
        return "Browser"
    rules = (
        (("youtube.com", "youtu.be"), "YouTube"),
        (("x.com", "twitter.com"), "X"),
        (("github.com",), "GitHub"),
        (("chatgpt.com", "chat.openai.com"), "ChatGPT"),
        (("claude.ai",), "Claude"),
        (("kimi.com", "moonshot.cn"), "Kimi"),
        (("reddit.com",), "Reddit"),
        (("google.com", "google.co."), "Google"),
        (("bing.com",), "Bing"),
    )
    for suffixes, label in rules:
        if any(suffix in host for suffix in suffixes):
            return label
    return host.removeprefix("www.") or "Unknown source"


def _publisher_label(source: str, parts: Any) -> str:
    segments = [segment for segment in str(parts.path or "").split("/") if segment]
    if not segments:
        return ""
    first = segments[0]
    if source == "X":
        reserved = {
            "compose",
            "explore",
            "home",
            "i",
            "intent",
            "login",
            "messages",
            "notifications",
            "search",
            "settings",
            "share",
            "signup",
        }
        if first.casefold() not in reserved and re.fullmatch(
            r"[A-Za-z0-9_]{1,30}", first
        ):
            return f"@{first}"
    if source == "GitHub":
        reserved = {
            "about",
            "collections",
            "customer-stories",
            "enterprise",
            "events",
            "features",
            "login",
            "marketplace",
            "new",
            "notifications",
            "orgs",
            "pricing",
            "search",
            "security",
            "settings",
            "signup",
            "sponsors",
            "topics",
            "trending",
        }
        if first.casefold() not in reserved and re.fullmatch(
            r"[A-Za-z0-9_.-]{1,100}", first
        ):
            return first
    if source == "Reddit" and len(segments) >= 2:
        if first.casefold() in {"r", "u", "user"}:
            prefix = "r" if first.casefold() == "r" else "u"
            return f"{prefix}/{segments[1][:100]}"
    if source == "YouTube":
        if first.startswith("@") and re.fullmatch(r"@[A-Za-z0-9_.-]{1,100}", first):
            return first
        if first.casefold() in {"c", "channel", "user"} and len(segments) >= 2:
            return segments[1][:100]
    return ""


def _source_group(source: str, scheme: str) -> str:
    grouped_sources = {
        "Bing",
        "Browser",
        "ChatGPT",
        "Claude",
        "GitHub",
        "Google",
        "Kimi",
        "Local file",
        "PDF",
        "Reddit",
        "X",
        "YouTube",
    }
    if source in grouped_sources:
        return source
    if scheme.casefold() in {"http", "https"}:
        return "Other websites"
    return source


def _clean_display_title(title: str, source: str) -> str:
    value = title.strip()
    if source == "YouTube":
        value = re.sub(r"^\(\d+\)\s+", "", value)
        value = re.sub(r"\s+-\s+YouTube$", "", value, flags=re.IGNORECASE)
    return value or source


def _format_label(host: str, kind: str, title: str, path: str, scheme: str) -> str:
    if kind == "youtube_short":
        return "Short video"
    if kind == "youtube":
        return "Video"
    if kind == "pdf" or path.casefold().endswith(".pdf"):
        return "PDF"
    if kind == "github":
        return "Repository"
    if kind == "docs":
        return "Documentation"
    if kind == "search":
        return "Search"
    if kind == "browser_internal":
        return "Browser page"
    if scheme == "file":
        return "Local file"
    if any(
        value in host
        for value in ("chatgpt.com", "chat.openai.com", "claude.ai", "kimi.com")
    ):
        return "Conversation"
    lower_title = title.casefold()
    if any(
        value in lower_title
        for value in ("pricing", "membership", "subscription", "upgrade")
    ):
        return "Pricing"
    if re.search(r"\.(?:png|jpe?g|gif|webp|avif)$", path, re.IGNORECASE):
        return "Image"
    return "Web page"


def _intent_label(next_action: str, format_label: str) -> str:
    value = next_action.casefold().strip()
    rules = (
        (("needs context", "review context"), "Review"),
        (("watch", "listen"), "Watch"),
        (("read", "skim", "paper", "document"), "Read"),
        (("compare", "choose", "buy", "subscription", "plan"), "Decide"),
        (("continue", "resume", "revisit"), "Continue"),
        (("try", "test", "build", "install", "use", "run", "explore"), "Try"),
        (("close", "discard"), "Close"),
    )
    for needles, label in rules:
        if any(needle in value for needle in needles):
            return label
    return {
        "Video": "Watch",
        "Short video": "Watch",
        "PDF": "Read",
        "Documentation": "Read",
        "Repository": "Try",
        "Conversation": "Continue",
        "Pricing": "Decide",
        "Search": "Review",
    }.get(format_label, "Reference")


def _group_identifier(tab: dict[str, Any]) -> str:
    seed = "|".join(
        (
            str(tab.get("browser") or ""),
            str(tab.get("windowId") or ""),
            str(tab.get("groupId") or ""),
        )
    )
    return "group_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _sentence(value: str) -> str:
    text = value.strip()[:180]
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _initials(value: str) -> str:
    parts = [part for part in re.split(r"[.\-_\s]+", value) if part]
    return "".join(part[0] for part in parts[:2]).upper() or "?"
