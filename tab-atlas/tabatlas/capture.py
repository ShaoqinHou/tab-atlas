from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .common import _atomic_write, normalize_browser
from .constants import (
    LIBRARY_STATES,
    TABATLAS_EXTENSION_URL_PREFIX,
    TRACKING_PARAMETERS,
)
from .database import utc_now


def normalize_snapshot_document(document: Any) -> list[dict[str, Any]]:
    if isinstance(document, dict) and isinstance(document.get("tabs"), list):
        return [_normalize_snapshot(document)]

    if isinstance(document, dict):
        snapshots: list[dict[str, Any]] = []
        for key, value in document.items():
            if isinstance(value, dict) and isinstance(value.get("tabs"), list):
                candidate = dict(value)
                candidate.setdefault("browser", key)
                snapshots.append(_normalize_snapshot(candidate))
        if snapshots:
            return snapshots

    if isinstance(document, list):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for index, item in enumerate(document):
            if not isinstance(item, dict):
                continue
            browser = normalize_browser(_pick(item, "browser", "Browser"))
            url = _pick(item, "url", "Url", "URL")
            if not isinstance(url, str) or not url.strip():
                continue
            grouped[browser].append(
                {
                    "id": index,
                    "windowId": _pick(item, "windowId", "WindowId") or "legacy-0",
                    "index": _as_int(_pick(item, "index", "Rank"), index),
                    "groupId": _pick(item, "groupId", "GroupId"),
                    "active": _as_bool(_pick(item, "active", "Active")),
                    "pinned": _as_bool(_pick(item, "pinned", "Pinned")),
                    "title": str(_pick(item, "title", "Title") or "")[:10000],
                    "url": url.strip()[:100000],
                }
            )
        return [
            _normalize_snapshot(
                {
                    "schemaVersion": 1,
                    "browser": browser,
                    "capturedAt": utc_now(),
                    "requestId": f"legacy-{browser}",
                    "windows": [
                        {"id": "legacy-0", "focused": False, "tabCount": len(tabs)}
                    ],
                    "groups": [],
                    "tabs": tabs,
                }
            )
            for browser, tabs in grouped.items()
        ]

    raise ValueError("Unsupported snapshot shape")


def _normalize_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    tabs_value = value.get("tabs")
    if not isinstance(tabs_value, list):
        raise ValueError("Snapshot tabs must be an array")
    if len(tabs_value) > 100000:
        raise ValueError("Snapshot tab limit exceeded")

    browser = normalize_browser(
        value.get("browser") or _browser_from_user_agent(value.get("userAgent"))
    )
    windows = [
        _normalize_window(item) for item in _object_items(value.get("windows"), 10000)
    ]
    groups = [
        _normalize_group(item) for item in _object_items(value.get("groups"), 10000)
    ]
    tabs = [
        _normalize_tab(item, index)
        for index, item in enumerate(_object_items(tabs_value, 100000))
    ]
    return {
        "schemaVersion": 1,
        "requestId": str(value.get("requestId") or "")[:200],
        "trigger": str(value.get("trigger") or "import")[:100],
        "browser": browser,
        "extensionId": str(value.get("extensionId") or "")[:100],
        "capturedAt": str(value.get("capturedAt") or utc_now())[:100],
        "windows": windows,
        "groups": groups,
        "tabs": tabs,
    }


def _normalize_window(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean_id(item.get("id")),
        "focused": _as_bool(item.get("focused")),
        "incognito": _as_bool(item.get("incognito")),
        "state": str(item.get("state") or "normal")[:50],
        "type": str(item.get("type") or "normal")[:50],
        "tabCount": _as_int(item.get("tabCount"), 0),
    }


def _normalize_group(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean_id(item.get("id")),
        "windowId": _clean_id(item.get("windowId")),
        "title": str(item.get("title") or "")[:1000],
        "color": str(item.get("color") or "grey")[:50],
        "collapsed": _as_bool(item.get("collapsed")),
        "shared": _as_bool(item.get("shared")),
    }


def _normalize_tab(item: dict[str, Any], fallback_index: int) -> dict[str, Any]:
    url = item.get("url") or item.get("pendingUrl") or ""
    return {
        "id": _clean_id(
            item.get("id") if item.get("id") is not None else item.get("tabId")
        ),
        "windowId": _clean_id(item.get("windowId")),
        "index": _as_int(item.get("index"), fallback_index),
        "groupId": _clean_id(item.get("groupId")),
        "active": _as_bool(item.get("active")),
        "highlighted": _as_bool(item.get("highlighted")),
        "pinned": _as_bool(item.get("pinned")),
        "audible": _as_bool(item.get("audible")),
        "muted": _as_bool(item.get("muted")),
        "discarded": _as_bool(item.get("discarded")),
        "autoDiscardable": _as_bool(item.get("autoDiscardable")),
        "incognito": _as_bool(item.get("incognito")),
        "title": str(item.get("title") or "")[:10000],
        "favIconUrl": str(item.get("favIconUrl") or "")[:100000],
        "url": str(url)[:100000],
    }


def _object_items(value: Any, maximum: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    if len(value) > maximum:
        raise ValueError("Snapshot item limit exceeded")
    return [item for item in value if isinstance(item, dict)]


def _pick(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item:
            return item[name]
    return None


def _clean_id(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:200]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _browser_from_user_agent(value: Any) -> str:
    text = str(value or "")
    if "Edg/" in text:
        return "edge"
    if "Chrome/" in text:
        return "chrome"
    return "chromium"


def canonicalize_url(url: str) -> tuple[str, str, str]:
    text = url.strip()
    if not text:
        text = "about:blank"
    try:
        parts = urlsplit(text)
    except ValueError:
        return text, "", "unknown"

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme in {"http", "https"}:
        port = parts.port
        netloc = host
        if port and not (
            (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        ):
            netloc = f"{host}:{port}"
        query_items = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            lower = key.lower()
            if lower.startswith("utm_") or lower in TRACKING_PARAMETERS:
                continue
            query_items.append((key, value))
        query_items.sort(key=lambda pair: (pair[0].lower(), pair[1]))
        path = parts.path or "/"
        canonical = urlunsplit(
            (scheme, netloc, path, urlencode(query_items, doseq=True), "")
        )
    else:
        canonical = (
            urlunsplit((scheme, parts.netloc, parts.path, parts.query, "")) or text
        )
    return canonical, host, classify_url(canonical, host)


def classify_url(url: str, host: str) -> str:
    path = urlsplit(url).path.lower()
    if host in {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com"}:
        if path.startswith("/shorts/"):
            return "youtube_short"
        if path.startswith("/playlist"):
            return "youtube_playlist"
        return "youtube"
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    if path.endswith(".pdf"):
        return "pdf"
    if host.startswith("docs.") or "/docs/" in path:
        return "docs"
    if "search" in path or host.startswith("search."):
        return "search"
    if url.startswith(("chrome://", "edge://", "about:")):
        return "browser_internal"
    return "web_page" if host else "other"


def resource_id(canonical_url: str) -> str:
    return "res_" + hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]


def store_snapshot(
    connection: sqlite3.Connection,
    state_dir: Path,
    snapshot: dict[str, Any],
    source: str,
    trust_state: str = "trusted",
    new_resource_state: str | None = None,
) -> dict[str, Any]:
    if trust_state not in {"trusted", "candidate"}:
        raise ValueError("trust_state must be trusted or candidate")
    if new_resource_state is None:
        new_resource_state = "candidate" if trust_state == "candidate" else "accepted"
    if new_resource_state not in LIBRARY_STATES:
        raise ValueError("new_resource_state must be accepted, candidate, or dismissed")
    normalized = _normalize_snapshot(snapshot)
    browser = normalized["browser"]
    raw = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True).encode(
        "utf-8"
    )
    digest = hashlib.sha256(raw).hexdigest()
    capture_id = "cap_" + digest[:24]
    existing = connection.execute(
        "SELECT * FROM captures WHERE id=?", (capture_id,)
    ).fetchone()
    if existing:
        candidate_count = connection.execute(
            """
            SELECT COUNT(DISTINCT t.resource_id) AS count
            FROM tab_instances t
            JOIN resources r ON r.id=t.resource_id
            WHERE t.capture_id=? AND r.library_state='candidate'
            """,
            (capture_id,),
        ).fetchone()["count"]
        return {
            **dict(existing),
            "duplicate": True,
            "resource_count": _capture_resource_count(connection, capture_id),
            "new_resource_count": 0,
            "candidate_resource_count": int(candidate_count),
        }

    snapshot_dir = state_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    safe_time = re.sub(r"[^0-9]", "", normalized["capturedAt"])[:14] or "undated"
    raw_path = snapshot_dir / f"{safe_time}-{browser}-{capture_id[4:12]}.json"
    _atomic_write(raw_path, raw)

    group_by_id = {
        str(group["id"]): group
        for group in normalized["groups"]
        if group.get("id") is not None
    }
    window_by_id = {
        str(window["id"]): window
        for window in normalized["windows"]
        if window.get("id") is not None
    }
    seen_resources: set[str] = set()
    new_resources: set[str] = set()
    received_at = utc_now()
    relative_raw = str(raw_path.relative_to(state_dir))

    try:
        with connection:
            connection.execute(
                """
                INSERT INTO captures(
                  id, browser, captured_at, received_at, source, trust_state, raw_path,
                  request_id, extension_id, window_count, group_count, tab_count
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capture_id,
                    browser,
                    normalized["capturedAt"],
                    received_at,
                    source[:100],
                    trust_state,
                    relative_raw,
                    normalized["requestId"] or None,
                    normalized["extensionId"] or None,
                    len(normalized["windows"]),
                    len(normalized["groups"]),
                    len(normalized["tabs"]),
                ),
            )
            for ordinal, tab in enumerate(normalized["tabs"]):
                canonical_url, host, kind = canonicalize_url(tab["url"])
                is_extension_resource = canonical_url.startswith(
                    TABATLAS_EXTENSION_URL_PREFIX
                )
                current_resource_id = resource_id(canonical_url)
                if not is_extension_resource:
                    seen_resources.add(current_resource_id)
                title = tab["title"].strip()
                if (
                    not is_extension_resource
                    and current_resource_id not in new_resources
                    and not connection.execute(
                        "SELECT 1 FROM resources WHERE id=?", (current_resource_id,)
                    ).fetchone()
                ):
                    new_resources.add(current_resource_id)
                effective_library_state = (
                    "accepted" if is_extension_resource else new_resource_state
                )
                connection.execute(
                    """
                    INSERT INTO resources(
                      id, canonical_url, host, kind, title, first_seen_at, last_seen_at,
                      library_state, accepted_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(canonical_url) DO UPDATE SET
                      host=excluded.host,
                      kind=excluded.kind,
                      title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE resources.title END,
                      last_seen_at=excluded.last_seen_at
                    """,
                    (
                        current_resource_id,
                        canonical_url,
                        host,
                        kind,
                        title,
                        normalized["capturedAt"],
                        normalized["capturedAt"],
                        effective_library_state,
                        normalized["capturedAt"]
                        if effective_library_state == "accepted"
                        else None,
                    ),
                )
                group_key = (
                    str(tab["groupId"]) if tab.get("groupId") is not None else ""
                )
                window_key = (
                    str(tab["windowId"]) if tab.get("windowId") is not None else ""
                )
                group = group_by_id.get(group_key, {})
                window = window_by_id.get(window_key, {})
                instance_seed = f"{capture_id}|{ordinal}|{tab.get('id')}|{tab['url']}"
                instance_id = (
                    "tab_"
                    + hashlib.sha256(instance_seed.encode("utf-8")).hexdigest()[:24]
                )
                connection.execute(
                    """
                    INSERT INTO tab_instances(
                      id, capture_id, resource_id, browser, window_id, window_focused,
                      tab_id, position, active, highlighted, pinned, audible, discarded, group_id,
                      group_title, group_color, group_collapsed, title, favicon_url, exact_url
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        instance_id,
                        capture_id,
                        current_resource_id,
                        browser,
                        tab["windowId"],
                        int(bool(window.get("focused"))),
                        tab["id"],
                        tab["index"],
                        int(tab["active"]),
                        int(tab["highlighted"]),
                        int(tab["pinned"]),
                        int(tab["audible"]),
                        int(tab["discarded"]),
                        tab["groupId"],
                        str(group.get("title") or ""),
                        str(group.get("color") or ""),
                        int(bool(group.get("collapsed"))),
                        title,
                        tab["favIconUrl"] or None,
                        tab["url"],
                    ),
                )
    except Exception:
        raw_path.unlink(missing_ok=True)
        raise

    return {
        "id": capture_id,
        "browser": browser,
        "captured_at": normalized["capturedAt"],
        "received_at": received_at,
        "source": source,
        "raw_path": relative_raw,
        "window_count": len(normalized["windows"]),
        "group_count": len(normalized["groups"]),
        "tab_count": len(normalized["tabs"]),
        "resource_count": len(seen_resources),
        "new_resource_count": len(new_resources),
        "candidate_resource_count": (
            len(new_resources) if new_resource_state == "candidate" else 0
        ),
        "duplicate": False,
    }


def _capture_resource_count(connection: sqlite3.Connection, capture_id: str) -> int:
    row = connection.execute(
        "SELECT COUNT(DISTINCT resource_id) AS count FROM tab_instances WHERE capture_id=?",
        (capture_id,),
    ).fetchone()
    return int(row["count"])


def latest_capture_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    browsers = [
        row["browser"]
        for row in connection.execute(
            "SELECT DISTINCT browser FROM captures WHERE trust_state='trusted'"
        )
    ]
    rows = []
    for browser in browsers:
        row = connection.execute(
            """
            SELECT * FROM captures
            WHERE browser=? AND trust_state='trusted'
            ORDER BY received_at DESC, rowid DESC
            LIMIT 1
            """,
            (browser,),
        ).fetchone()
        if row:
            rows.append(dict(row))
    return sorted(rows, key=lambda item: item["browser"])
