from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .catalog import library_resources
from .common import _atomic_write
from .constants import PUBLIC_PREVIEW_USER_AGENT
from .database import utc_now
from .media import _provider_url_allowed, _x_motion_url_allowed, _youtube_video_id


class _OpenGraphParser(HTMLParser):
    FIELDS = {
        "og:description",
        "og:image",
        "og:image:height",
        "og:image:secure_url",
        "og:image:width",
        "og:title",
        "og:type",
        "twitter:card",
        "twitter:creator",
        "twitter:description",
        "twitter:image",
        "twitter:title",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}
        self.json_ld: list[Any] = []
        self._json_ld_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.casefold()
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        if tag_name == "script":
            if values.get("type", "").casefold() == "application/ld+json":
                self._json_ld_parts = []
            return
        if tag_name != "meta":
            return
        name = str(values.get("property") or values.get("name") or "").casefold()
        content = str(values.get("content") or "").strip()
        if name in self.FIELDS and content:
            self.values.setdefault(name, content)

    def handle_data(self, data: str) -> None:
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or self._json_ld_parts is None:
            return
        raw = "".join(self._json_ld_parts).strip()
        self._json_ld_parts = None
        if not raw or len(raw) > 262_144:
            return
        try:
            document = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError):
            return
        self.json_ld.append(document)


class _AllowlistedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        if not _provider_url_allowed(new_url, self.allowed_hosts):
            raise ValueError("Preview redirect left the provider allowlist")
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )


def cache_public_previews(
    connection: sqlite3.Connection,
    state_dir: Path,
    refresh: bool = False,
    workers: int = 8,
) -> dict[str, Any]:
    preview_dir = state_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    resources = library_resources(connection, {"accepted", "candidate"})
    candidates: list[dict[str, Any]] = []
    provider_stats: dict[str, Counter[str]] = defaultdict(Counter)
    eligible = 0
    cached = 0
    preserved_screenshots = 0
    for resource in resources:
        candidate = _public_preview_candidate(resource, preview_dir)
        if not candidate:
            continue
        provider = candidate["provider"]
        eligible += 1
        provider_stats[provider]["eligible"] += 1
        existing = _existing_preview_path(
            state_dir, str(resource.get("previewLocalPath") or "")
        )
        protected_screenshot = bool(
            existing and resource.get("previewKind") == "screenshot"
        )
        if existing:
            cached += 1
            provider_stats[provider]["alreadyCached"] += 1
        if protected_screenshot:
            preserved_screenshots += 1
        needs_image = not existing or (refresh and not protected_screenshot)
        needs_motion = provider == "x_post" and (
            refresh or not resource.get("motionRefreshedAt")
        )
        if not needs_image and not needs_motion:
            continue
        candidate["downloadImage"] = needs_image
        candidates.append(candidate)

    downloaded: list[tuple[str, dict[str, Any]]] = []
    motion_updates: list[tuple[str, dict[str, str] | None]] = []
    failed = 0
    with ThreadPoolExecutor(max_workers=max(1, min(16, workers))) as executor:
        futures = {
            executor.submit(_download_public_preview, candidate): candidate
            for candidate in candidates
        }
        for future in as_completed(futures):
            candidate = futures[future]
            provider = candidate["provider"]
            try:
                result = future.result()
            except (HTTPError, URLError, OSError, UnicodeError, ValueError):
                failed += 1
                provider_stats[provider]["failed"] += 1
                continue
            if result.get("preview"):
                downloaded.append((candidate["resourceId"], result["preview"]))
                provider_stats[provider]["downloaded"] += 1
            elif result.get("previewFailed"):
                failed += 1
                provider_stats[provider]["failed"] += 1
            if result.get("motionChecked"):
                motion_updates.append((candidate["resourceId"], result.get("motion")))
                provider_stats[provider]["motionChecked"] += 1
                if result.get("motion"):
                    provider_stats[provider]["motionResolved"] += 1

    now = utc_now()
    state_root = state_dir.resolve()
    with connection:
        for resource_id_value, result in downloaded:
            connection.execute(
                """
                INSERT INTO resource_previews(
                  resource_id, kind, local_path, source, content_sha256,
                  width, height, created_at
                ) VALUES(?, 'thumbnail', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resource_id) DO UPDATE SET
                  kind=excluded.kind,
                  local_path=excluded.local_path,
                  source=excluded.source,
                  content_sha256=excluded.content_sha256,
                  width=excluded.width,
                  height=excluded.height,
                  created_at=excluded.created_at
                """,
                (
                    resource_id_value,
                    str(result["target"].relative_to(state_root)),
                    result["source"],
                    result["digest"],
                    result.get("width"),
                    result.get("height"),
                    now,
                ),
            )
        for resource_id_value, motion in motion_updates:
            connection.execute(
                """
                INSERT INTO resource_motion_previews(
                  resource_id, kind, remote_url, source, refreshed_at
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(resource_id) DO UPDATE SET
                  kind=excluded.kind,
                  remote_url=excluded.remote_url,
                  source=excluded.source,
                  refreshed_at=excluded.refreshed_at
                """,
                (
                    resource_id_value,
                    str(motion.get("kind") if motion else "none"),
                    str(motion.get("url") if motion else "") or None,
                    str(motion.get("source") if motion else "x_public_no_motion"),
                    now,
                ),
            )
    return {
        "eligible": eligible,
        "alreadyCached": cached,
        "preservedScreenshots": preserved_screenshots,
        "downloaded": len(downloaded),
        "motionChecked": len(motion_updates),
        "motionResolved": sum(1 for _, motion in motion_updates if motion),
        "failed": failed,
        "providers": {
            provider: {
                key: int(stats.get(key, 0))
                for key in (
                    "eligible",
                    "alreadyCached",
                    "downloaded",
                    "motionChecked",
                    "motionResolved",
                    "failed",
                )
            }
            for provider, stats in sorted(provider_stats.items())
        },
    }


def _public_preview_candidate(
    resource: dict[str, Any],
    preview_dir: Path,
) -> dict[str, Any] | None:
    url = str(resource.get("canonicalUrl") or "")
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").casefold()
    segments = [segment for segment in parts.path.split("/") if segment]
    target_base = preview_dir / str(resource["resourceId"])

    video_id = _youtube_video_id(url)
    if video_id and _provider_url_allowed(url, ("youtu.be", "youtube.com")):
        return {
            "provider": "youtube",
            "resourceId": resource["resourceId"],
            "targetBase": target_base,
            "imageUrl": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
            "imageHosts": ("i.ytimg.com",),
            "source": "youtube_public_thumbnail",
            "width": 320,
            "height": 180,
        }
    if (
        _provider_url_allowed(url, ("x.com", "twitter.com"))
        and len(segments) >= 3
        and segments[1].casefold() == "status"
        and segments[2].isdigit()
    ):
        return {
            "provider": "x_post",
            "resourceId": resource["resourceId"],
            "targetBase": target_base,
            "pageUrl": url,
            "pageHosts": ("x.com", "twitter.com"),
            "imageHosts": ("pbs.twimg.com",),
        }
    if (
        host in {"github.com", "www.github.com"}
        and len(segments) >= 2
        and _provider_url_allowed(url, ("github.com",))
    ):
        return {
            "provider": "github",
            "resourceId": resource["resourceId"],
            "targetBase": target_base,
            "pageUrl": url,
            "pageHosts": ("github.com",),
            "imageHosts": (
                "opengraph.githubassets.com",
                "repository-images.githubusercontent.com",
            ),
        }
    if _provider_url_allowed(url, ("reddit.com",)) and any(
        segment.casefold() == "comments" for segment in segments
    ):
        return {
            "provider": "reddit_post",
            "resourceId": resource["resourceId"],
            "targetBase": target_base,
            "pageUrl": url,
            "pageHosts": ("reddit.com",),
            "imageHosts": ("preview.redd.it", "external-preview.redd.it", "i.redd.it"),
        }
    return None


def _existing_preview_path(state_dir: Path, local_path: str) -> Path | None:
    if not local_path:
        return None
    state_root = state_dir.resolve()
    candidate = (state_root / local_path).resolve()
    try:
        candidate.relative_to(state_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _download_public_preview(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, str] = {}
    json_ld: list[Any] = []
    image_url = str(candidate.get("imageUrl") or "")
    if not image_url:
        metadata, json_ld, page_url = _fetch_open_graph(
            str(candidate["pageUrl"]),
            tuple(candidate["pageHosts"]),
        )
        image_url = str(
            metadata.get("og:image:secure_url")
            or metadata.get("og:image")
            or metadata.get("twitter:image")
            or ""
        )
        image_url = urljoin(page_url, image_url)
    provider = str(candidate["provider"])
    result: dict[str, Any] = {
        "preview": None,
        "previewFailed": False,
        "motionChecked": provider == "x_post",
        "motion": _x_motion_from_json_ld(json_ld) if provider == "x_post" else None,
    }
    if not candidate.get("downloadImage", True):
        return result
    try:
        if not _preview_image_allowed(
            provider,
            image_url,
            tuple(candidate["imageHosts"]),
        ):
            raise ValueError("Public preview image left the provider allowlist")
        data, suffix = _download_public_image(image_url, tuple(candidate["imageHosts"]))
    except (HTTPError, URLError, OSError, UnicodeError, ValueError):
        result["previewFailed"] = True
        return result

    target = Path(candidate["targetBase"]).with_suffix(suffix)
    _atomic_write(target, data)
    result["preview"] = {
        "target": target,
        "digest": hashlib.sha256(data).hexdigest(),
        "source": str(
            candidate.get("source") or _public_preview_source(provider, image_url)
        ),
        "width": candidate.get("width")
        or _positive_int(metadata.get("og:image:width")),
        "height": candidate.get("height")
        or _positive_int(metadata.get("og:image:height")),
    }
    return result


def _fetch_open_graph(
    url: str,
    allowed_hosts: tuple[str, ...],
) -> tuple[dict[str, str], list[Any], str]:
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "DNT": "1",
            "User-Agent": PUBLIC_PREVIEW_USER_AGENT,
        },
    )
    with _open_allowlisted(request, allowed_hosts, timeout=15) as response:
        final_url = response.geturl()
        if not _provider_url_allowed(final_url, allowed_hosts):
            raise ValueError("Preview page redirect left the allowed origin")
        content_type = (
            str(response.headers.get("Content-Type") or "").split(";", 1)[0].casefold()
        )
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("Preview metadata response was not HTML")
        charset = response.headers.get_content_charset() or "utf-8"
        data = response.read(786_432)
    parser = _OpenGraphParser()
    parser.feed(data.decode(charset, errors="replace"))
    return parser.values, parser.json_ld, final_url


def _x_motion_from_json_ld(documents: list[Any]) -> dict[str, str] | None:
    pending = list(documents)
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
            continue
        if not isinstance(value, dict):
            continue
        schema_type = value.get("@type")
        types = schema_type if isinstance(schema_type, list) else [schema_type]
        if any(str(item).casefold() == "videoobject" for item in types):
            motion_url = str(value.get("contentUrl") or "")
            if _x_motion_url_allowed(motion_url):
                return {
                    "kind": "x_mp4",
                    "url": motion_url,
                    "source": "x_public_json_ld_video",
                }
        pending.extend(value.values())
    return None


def _preview_image_allowed(
    provider: str,
    image_url: str,
    allowed_hosts: tuple[str, ...],
) -> bool:
    try:
        parts = urlsplit(image_url)
    except ValueError:
        return False
    if not _provider_url_allowed(image_url, allowed_hosts):
        return False
    path = parts.path.casefold()
    if provider == "x_post":
        return path.startswith(
            (
                "/amplify_video_thumb/",
                "/card_img/",
                "/ext_tw_video_thumb/",
                "/media/",
                "/tweet_video_thumb/",
            )
        )
    return True


def _download_public_image(
    url: str, allowed_hosts: tuple[str, ...]
) -> tuple[bytes, str]:
    request = Request(
        url,
        headers={
            "Accept": "image/jpeg,image/png,image/webp",
            "DNT": "1",
            "User-Agent": PUBLIC_PREVIEW_USER_AGENT,
        },
    )
    with _open_allowlisted(request, allowed_hosts, timeout=15) as response:
        if not _provider_url_allowed(response.geturl(), allowed_hosts):
            raise ValueError("Preview image redirect left the allowed origin")
        content_type = (
            str(response.headers.get("Content-Type") or "").split(";", 1)[0].casefold()
        )
        if content_type not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
            raise ValueError("Preview response was not a supported image")
        data = response.read(5_000_001)
    if len(data) > 5_000_000:
        raise ValueError("Preview image exceeded 5 MB")
    return data, _image_suffix(data)


def _open_allowlisted(
    request: Request,
    allowed_hosts: tuple[str, ...],
    timeout: int,
) -> Any:
    if not _provider_url_allowed(request.full_url, allowed_hosts):
        raise ValueError("Preview request left the provider allowlist")
    opener = build_opener(_AllowlistedRedirectHandler(allowed_hosts))
    return opener.open(request, timeout=timeout)


def _public_preview_source(provider: str, image_url: str) -> str:
    path = urlsplit(image_url).path.casefold()
    if provider == "x_post":
        if any(value in path for value in ("video_thumb", "amplify_video_thumb")):
            return "x_public_video_thumbnail"
        if path.startswith("/card_img/"):
            return "x_public_card_image"
        return "x_public_post_image"
    return {
        "github": "github_public_social_image",
        "reddit_post": "reddit_public_post_image",
    }.get(provider, "public_social_image")


def _positive_int(value: Any) -> int | None:
    try:
        number = int(str(value or ""))
    except ValueError:
        return None
    return number if 0 < number <= 100_000 else None


def _image_suffix(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return ".jpg"
    if (
        len(data) >= 24
        and data.startswith(b"\x89PNG\r\n\x1a\n")
        and data[12:16] == b"IHDR"
        and int.from_bytes(data[16:20], "big") > 0
        and int.from_bytes(data[20:24], "big") > 0
    ):
        return ".png"
    if (
        len(data) >= 16
        and data.startswith(b"RIFF")
        and data[8:12] == b"WEBP"
        and int.from_bytes(data[4:8], "little") + 8 <= len(data)
    ):
        return ".webp"
    raise ValueError("Preview image must be JPEG, PNG, or WebP")


def register_local_preview(
    connection: sqlite3.Connection,
    state_dir: Path,
    resource_id_value: str,
    image_path: Path,
) -> dict[str, Any]:
    current_resource_id = str(resource_id_value).strip()
    if not re.fullmatch(r"res_[0-9a-f]{24}", current_resource_id):
        raise ValueError("Resource ID is invalid")
    if not connection.execute(
        "SELECT 1 FROM resources WHERE id=?",
        (current_resource_id,),
    ).fetchone():
        raise ValueError(f"Unknown resource ID: {current_resource_id}")

    source = image_path.resolve()
    if not source.is_file():
        raise ValueError(f"Preview image does not exist: {source}")
    size = source.stat().st_size
    if size <= 0 or size > 8_000_000:
        raise ValueError("Preview image must be between 1 byte and 8 MB")
    data = source.read_bytes()
    suffix = _image_suffix(data)

    target = state_dir.resolve() / "previews" / f"{current_resource_id}{suffix}"
    _atomic_write(target, data)
    digest = hashlib.sha256(data).hexdigest()
    now = utc_now()
    with connection:
        connection.execute(
            """
            INSERT INTO resource_previews(
              resource_id, kind, local_path, source, content_sha256,
              width, height, created_at
            ) VALUES(?, 'screenshot', ?, 'agent_local_capture', ?, NULL, NULL, ?)
            ON CONFLICT(resource_id) DO UPDATE SET
              kind=excluded.kind,
              local_path=excluded.local_path,
              source=excluded.source,
              content_sha256=excluded.content_sha256,
              width=excluded.width,
              height=excluded.height,
              created_at=excluded.created_at
            """,
            (
                current_resource_id,
                str(target.relative_to(state_dir.resolve())),
                digest,
                now,
            ),
        )
    return {
        "resourceId": current_resource_id,
        "kind": "screenshot",
        "bytes": len(data),
        "sha256": digest,
    }
