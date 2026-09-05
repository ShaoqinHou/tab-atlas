from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..common import _atomic_write, _chunks
from ..constants import TABATLAS_EXTENSION_URL_PREFIX
from ..database import utc_now
from .shared import _numeric_tab_id, _trusted_post_capture_row


def finalize_archive_plan(
    connection: sqlite3.Connection,
    state_dir: Path,
    plan: dict[str, Any],
    results: dict[str, dict[str, Any]],
    post_captures: dict[str, dict[str, Any]],
    error: str = "",
) -> dict[str, Any]:
    request_id = str(plan["requestId"])
    row = connection.execute(
        "SELECT evidence_path FROM mutation_audits WHERE action='archive_captured_tabs' AND request_id LIKE ? LIMIT 1",
        (f"{request_id}:%",),
    ).fetchone()
    if not row:
        raise ValueError("Archive audit plan was not recorded")
    evidence_path = state_dir / row["evidence_path"]
    existing = json.loads(evidence_path.read_text(encoding="utf-8"))
    post_verification: dict[str, dict[str, Any]] = {}
    valid_post_capture_ids: dict[str, str] = {}
    controls: dict[str, dict[str, int]] = {}
    retained_resource_ids: set[str] = set()
    discarded_resource_ids: set[str] = set()
    operational_resource_ids: set[str] = set()
    closed_total = 0
    skipped_total = 0
    completed_at = utc_now()
    for browser, browser_plan in plan["browsers"].items():
        if not browser_plan["targets"]:
            continue
        result = results.get(browser) or {}
        result_by_id = {
            int(item["tabId"]): item
            for item in result.get("results") or []
            if isinstance(item, dict) and item.get("tabId") is not None
        }
        post_capture = post_captures.get(browser)
        post_row = _trusted_post_capture_row(
            connection,
            browser,
            browser_plan["beforeCaptureId"],
            post_capture,
        )
        post_tab_ids: set[int] = set()
        if post_row:
            for tab_row in connection.execute(
                "SELECT tab_id FROM tab_instances WHERE capture_id=?",
                (post_row["id"],),
            ):
                tab_id = _numeric_tab_id(tab_row["tab_id"])
                if tab_id is not None:
                    post_tab_ids.add(tab_id)
        planned_ids = {int(item["targetTabId"]) for item in browser_plan["targets"]}
        closed_ids = {
            tab_id
            for tab_id in planned_ids
            if result_by_id.get(tab_id, {}).get("status") == "closed"
        }
        post_capture_valid = post_row is not None
        if post_row:
            valid_post_capture_ids[browser] = str(post_row["id"])
        targets_absent = post_capture_valid and planned_ids.isdisjoint(post_tab_ids)
        all_reported_closed = closed_ids == planned_ids
        verified = targets_absent and all_reported_closed
        post_verification[browser] = {
            "targetsAbsent": targets_absent,
            "allReportedClosed": all_reported_closed,
            "postCaptureValid": post_capture_valid,
            "verified": verified,
        }
        control_tab_id = _numeric_tab_id(result.get("controlTabId"))
        control_window_id = _numeric_tab_id(result.get("controlWindowId"))
        if control_tab_id is not None and control_window_id is not None:
            controls[browser] = {
                "controlTabId": control_tab_id,
                "controlWindowId": control_window_id,
            }
        closed_total += int(result.get("closedCount") or 0)
        skipped_total += int(result.get("skippedCount") or 0)
        for item in browser_plan["targets"]:
            retention = str(item.get("retention") or "retain")
            if retention == "discard":
                discarded_resource_ids.add(str(item["resourceId"]))
            elif retention == "operational":
                operational_resource_ids.add(str(item["resourceId"]))
            else:
                retained_resource_ids.add(str(item["resourceId"]))

    expected_control_browsers = {
        browser
        for browser, browser_plan in plan["browsers"].items()
        if browser_plan["targets"]
    }
    all_controls_reported = set(controls) == expected_control_browsers
    all_verified = (
        bool(post_verification)
        and all(item["verified"] for item in post_verification.values())
        and all_controls_reported
        and not error
    )
    if all_verified and retained_resource_ids:
        with connection:
            for resource_chunk in _chunks(sorted(retained_resource_ids), 400):
                placeholders = ",".join("?" for _ in resource_chunk)
                connection.execute(
                    f"UPDATE resources SET status='saved', archived_at=? WHERE id IN ({placeholders})",
                    [completed_at, *resource_chunk],
                )
    if all_verified and discarded_resource_ids:
        with connection:
            for resource_chunk in _chunks(sorted(discarded_resource_ids), 400):
                placeholders = ",".join("?" for _ in resource_chunk)
                connection.execute(
                    f"UPDATE resources SET status='archived', archived_at=? "
                    f"WHERE library_state='dismissed' AND id IN ({placeholders})",
                    [completed_at, *resource_chunk],
                )
    if all_verified and operational_resource_ids:
        with connection:
            for resource_chunk in _chunks(sorted(operational_resource_ids), 400):
                placeholders = ",".join("?" for _ in resource_chunk)
                connection.execute(
                    f"UPDATE resources SET status='archived', archived_at=? "
                    f"WHERE canonical_url LIKE ? AND id IN ({placeholders})",
                    [
                        completed_at,
                        f"{TABATLAS_EXTENSION_URL_PREFIX}%",
                        *resource_chunk,
                    ],
                )

    evidence = {
        **existing,
        "results": results,
        "postCaptures": {
            browser: {"captureId": item.get("id"), "tabCount": item.get("tab_count")}
            for browser, item in post_captures.items()
        },
        "postVerification": post_verification,
        "allControlsReported": all_controls_reported,
        "completedAt": completed_at,
        "error": error[:500],
    }
    _atomic_write(
        evidence_path,
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        ),
    )
    with connection:
        for browser, browser_plan in plan["browsers"].items():
            if not browser_plan["targets"]:
                continue
            result = results.get(browser) or {}
            closed = int(result.get("closedCount") or 0)
            skipped = int(result.get("skippedCount") or 0)
            planned = len(browser_plan["targets"])
            verified = post_verification.get(browser, {}).get("verified", False)
            if error or not result:
                status = "failed"
            elif closed == planned and skipped == 0 and verified:
                status = "completed"
            else:
                status = "partial"
            connection.execute(
                """
                UPDATE mutation_audits
                SET closed_count=?, skipped_count=?, status=?, after_capture_id=?,
                    completed_at=?, error=?
                WHERE request_id=?
                """,
                (
                    closed,
                    skipped,
                    status,
                    valid_post_capture_ids.get(browser),
                    completed_at,
                    error[:500] or None,
                    f"{request_id}:{browser}",
                ),
            )
    return {
        "closed": closed_total,
        "skipped": skipped_total,
        "verifiedBrowsers": sum(
            bool(item["verified"]) for item in post_verification.values()
        ),
        "expectedBrowsers": len(post_verification),
        "allControlsReported": all_controls_reported,
        "archivedResources": len(retained_resource_ids) if all_verified else 0,
        "discardedResources": len(discarded_resource_ids) if all_verified else 0,
        "operationalResources": len(operational_resource_ids) if all_verified else 0,
        "controls": controls,
        "evidencePath": str(evidence_path),
    }
