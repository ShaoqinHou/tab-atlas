from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any


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


def source_label(host: str, kind: str, scheme: str) -> str:
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


def publisher_label(source: str, parts: Any) -> str:
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


def source_group(source: str, scheme: str) -> str:
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
