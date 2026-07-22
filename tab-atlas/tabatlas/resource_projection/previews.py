from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..media import _x_motion_url_allowed
from .labels import initials


def preview_projection(
    resource: dict[str, Any],
    source: str,
    source_group: str,
    format_label: str,
    url: str,
    youtube_video_id: str,
) -> dict[str, Any]:
    preview_label = {
        "YouTube": "YT",
        "GitHub": "GH",
        "ChatGPT": "AI",
        "Claude": "AI",
        "Kimi": "AI",
        "PDF": "PDF",
        "Browser": "BR",
        "Local file": "FILE",
    }.get(source, initials(source))
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
    preview_url = (
        f"https://i.ytimg.com/vi/{youtube_video_id}/mqdefault.jpg"
        if youtube_video_id
        else ""
    )
    local_preview = ""
    if resource.get("previewLocalPath"):
        suffix = Path(str(resource["previewLocalPath"])).suffix.casefold()
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            local_preview = f"media/{resource['resourceId']}{suffix}"
    policy = preview_policy(
        source_group,
        format_label,
        url,
        str(resource.get("previewSource") or ""),
    )
    motion = motion_preview(resource, youtube_video_id)
    return {
        "label": preview_label,
        "accent": accent,
        "localImage": local_preview,
        "remoteImage": preview_url,
        "requiresUserLoad": bool(preview_url and not local_preview),
        "motion": motion,
        **policy,
    }


def preview_policy(
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


def motion_preview(resource: dict[str, Any], youtube_video_id: str) -> dict[str, str]:
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
