from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit


def _youtube_video_id(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").casefold()
    segments = [segment for segment in parts.path.split("/") if segment]
    candidate = ""
    if host.endswith("youtu.be") and segments:
        candidate = segments[0]
    elif "youtube.com" in host:
        if parts.path.rstrip("/") == "/watch":
            candidate = dict(parse_qsl(parts.query)).get("v", "")
        elif len(segments) >= 2 and segments[0] in {"shorts", "embed", "live"}:
            candidate = segments[1]
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{6,32}", candidate) else ""


def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    return any(
        host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts
    )


def _provider_url_allowed(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme.casefold() == "https"
        and _host_allowed((parts.hostname or "").casefold(), allowed_hosts)
        and parts.username is None
        and parts.password is None
        and port in {None, 443}
    )


def _x_motion_url_allowed(url: str) -> bool:
    if not _provider_url_allowed(url, ("video.twimg.com",)):
        return False
    parts = urlsplit(url)
    if (parts.hostname or "").casefold() != "video.twimg.com":
        return False
    path = parts.path.casefold()
    return path.startswith(
        (
            "/amplify_video/",
            "/ext_tw_video/",
            "/tweet_video/",
        )
    ) and path.endswith(".mp4")
