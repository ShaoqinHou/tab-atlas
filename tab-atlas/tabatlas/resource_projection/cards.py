from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..catalog import _collection_authority_rank, exact_duplicate_summary
from ..media import _youtube_video_id
from .labels import clean_display_title, format_label, intent_label, sentence
from .previews import preview_projection
from .source_facets import publisher_label, source_group, source_label


def resource_presentation(resource: dict[str, Any]) -> dict[str, Any]:
    url = str(resource.get("openUrl") or resource.get("canonicalUrl") or "")
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = urlsplit("")
    host = str(resource.get("host") or parts.hostname or "").casefold()
    kind = str(resource.get("kind") or "web_page")
    title = str(resource.get("title") or "")
    source = source_label(host, kind, parts.scheme)
    source_group_label = source_group(source, parts.scheme)
    publisher = publisher_label(source, parts)
    if not publisher and source_group_label == "Other websites":
        publisher = source
    resource_format = format_label(host, kind, title, parts.path, parts.scheme)
    intent = intent_label(str(resource.get("nextAction") or ""), resource_format)
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
        decision_cue = sentence(next_action)
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
        context_cue = sentence(why_kept)
    elif group_titles:
        context_cue = f"Browser group context: {group_titles[0]}."
    elif spaces:
        context_cue = f"Reference for {spaces[0]['name']}."
    elif duplicate_count:
        context_cue = f"Open in {len(tabs)} tab instances."
    else:
        context_cue = "No durable context recorded yet."

    video_id = _youtube_video_id(url)
    preview = preview_projection(
        resource,
        source,
        source_group_label,
        resource_format,
        url,
        video_id,
    )

    return {
        "displayTitle": clean_display_title(title, source),
        "source": source,
        "sourceGroup": source_group_label,
        "publisher": publisher,
        "format": resource_format,
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
        "preview": preview,
    }
