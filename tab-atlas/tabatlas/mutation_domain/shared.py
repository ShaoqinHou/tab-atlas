from __future__ import annotations

import sqlite3
from typing import Any

from ..capture import latest_capture_rows
from ..common import normalize_browser


def _numeric_tab_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _resolve_plan_captures(
    connection: sqlite3.Connection,
    browsers: set[str],
    capture_ids: dict[str, str] | None,
) -> dict[str, dict[str, Any]]:
    if capture_ids is None:
        captures = {item["browser"]: item for item in latest_capture_rows(connection)}
    else:
        normalized = {
            normalize_browser(browser): str(capture_id)
            for browser, capture_id in capture_ids.items()
        }
        if set(normalized) != browsers:
            raise ValueError(
                "Explicit capture IDs must match the requested browsers exactly"
            )
        captures = {}
        for browser, capture_id in normalized.items():
            row = connection.execute(
                "SELECT rowid AS capture_rowid, * FROM captures "
                "WHERE id=? AND browser=? AND trust_state='trusted'",
                (capture_id, browser),
            ).fetchone()
            if row:
                captures[browser] = dict(row)
    missing = browsers - captures.keys()
    if missing:
        raise ValueError(f"No trusted capture for: {', '.join(sorted(missing))}")
    return captures


def _trusted_post_capture_row(
    connection: sqlite3.Connection,
    browser: str,
    before_capture_id: str,
    post_capture: dict[str, Any] | None,
) -> sqlite3.Row | None:
    if not post_capture:
        return None
    before_row = connection.execute(
        "SELECT rowid FROM captures WHERE id=? AND browser=? AND trust_state='trusted'",
        (before_capture_id, browser),
    ).fetchone()
    if not before_row:
        return None
    post_row = connection.execute(
        "SELECT rowid AS capture_rowid, * FROM captures "
        "WHERE id=? AND browser=? AND trust_state='trusted' AND source='extension_live'",
        (str(post_capture.get("id") or ""), browser),
    ).fetchone()
    if not post_row or int(post_row["capture_rowid"]) <= int(before_row["rowid"]):
        return None
    return post_row
