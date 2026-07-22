from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from .common import _atomic_write, _chunks, _file_sha256, _require_inside_path
from .constants import TABATLAS_EXTENSION_URL_PREFIX
from .database import utc_now

from .mutation_plans import (
    _numeric_tab_id,
    build_archive_plan as build_archive_plan,
    build_exact_duplicate_plan as build_exact_duplicate_plan,
)


def record_archive_plan(
    connection: sqlite3.Connection,
    state_dir: Path,
    plan: dict[str, Any],
    approval_scope: str,
) -> Path:
    approval = approval_scope.strip()[:1000]
    if not approval:
        raise ValueError("A bounded explicit archive approval scope is required")
    if plan.get("action") != "archive_captured_tabs":
        raise ValueError("Archive evidence requires an archive_captured_tabs plan")
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    if integrity != "ok":
        raise ValueError(f"Catalog integrity check failed: {integrity[:200]}")

    capture_evidence: dict[str, dict[str, Any]] = {}
    for browser, browser_plan in plan["browsers"].items():
        capture_row = connection.execute(
            "SELECT rowid AS capture_rowid, * FROM captures "
            "WHERE id=? AND browser=? AND trust_state='trusted'",
            (browser_plan["beforeCaptureId"], browser),
        ).fetchone()
        if not capture_row:
            raise ValueError(
                f"Archive plan is not bound to a trusted {browser} capture"
            )
        capture = dict(capture_row)
        raw_path = (state_dir / str(capture["raw_path"])).resolve()
        _require_inside_path(raw_path, state_dir)
        if not raw_path.is_file():
            raise ValueError(f"Raw capture evidence is missing for {browser}")
        capture_evidence[browser] = {
            "captureId": capture["id"],
            "tabCount": int(capture["tab_count"]),
            "rawPath": str(raw_path.relative_to(state_dir)),
            "rawSha256": _file_sha256(raw_path),
        }

    retained_resource_ids = {
        str(target["resourceId"])
        for browser_plan in plan["browsers"].values()
        for target in browser_plan["targets"]
        if str(target.get("retention") or "retain") == "retain"
    }
    discarded_resource_ids = {
        str(target["resourceId"])
        for browser_plan in plan["browsers"].values()
        for target in browser_plan["targets"]
        if str(target.get("retention") or "retain") == "discard"
    }
    operational_resource_ids = {
        str(target["resourceId"])
        for browser_plan in plan["browsers"].values()
        for target in browser_plan["targets"]
        if str(target.get("retention") or "retain") == "operational"
    }
    planned_targets = [
        target
        for browser_plan in plan["browsers"].values()
        for target in browser_plan["targets"]
    ]
    if any(
        str(target.get("retention") or "retain")
        not in {"retain", "discard", "operational"}
        for target in planned_targets
    ):
        raise ValueError("Archive target retention decision is invalid")
    if retained_resource_ids:
        placeholders = ",".join("?" for _ in retained_resource_ids)
        retained_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM resources WHERE id IN ({placeholders}) "
                "AND canonical_url <> '' AND library_state='accepted'",
                sorted(retained_resource_ids),
            ).fetchone()[0]
        )
    else:
        retained_count = 0
    if retained_count != len(retained_resource_ids):
        raise ValueError("Not every archive target has a durable resource record")
    if discarded_resource_ids:
        placeholders = ",".join("?" for _ in discarded_resource_ids)
        discarded_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM resources WHERE id IN ({placeholders}) "
                "AND canonical_url <> '' AND library_state='dismissed' "
                "AND dismissed_at IS NOT NULL",
                sorted(discarded_resource_ids),
            ).fetchone()[0]
        )
    else:
        discarded_count = 0
    if discarded_count != len(discarded_resource_ids):
        raise ValueError("Not every discard target has an explicit reviewed dismissal")
    if operational_resource_ids:
        placeholders = ",".join("?" for _ in operational_resource_ids)
        operational_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM resources WHERE id IN ({placeholders}) "
                "AND canonical_url LIKE ? AND library_state='accepted'",
                [
                    *sorted(operational_resource_ids),
                    f"{TABATLAS_EXTENSION_URL_PREFIX}%",
                ],
            ).fetchone()[0]
        )
    else:
        operational_count = 0
    if operational_count != len(operational_resource_ids):
        raise ValueError(
            "Not every operational target belongs to the TabAtlas extension"
        )

    safe_time = re.sub(r"[^0-9]", "", str(plan["createdAt"]))[:14] or "undated"
    backup_dir = state_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{safe_time}-pre-archive-{plan['requestId'][:8]}.sqlite"
    temporary_backup = backup_path.with_suffix(".tmp")
    temporary_backup.unlink(missing_ok=True)
    destination = sqlite3.connect(temporary_backup)
    try:
        connection.backup(destination)
        backup_integrity = str(
            destination.execute("PRAGMA integrity_check").fetchone()[0]
        )
    finally:
        destination.close()
    if backup_integrity != "ok":
        temporary_backup.unlink(missing_ok=True)
        raise ValueError(
            f"Archive backup integrity check failed: {backup_integrity[:200]}"
        )
    temporary_backup.replace(backup_path)

    request_id = str(plan["requestId"])
    evidence_path = (
        state_dir / "mutations" / f"{safe_time}-archive-{request_id[:8]}.json"
    )
    durability = {
        "catalogIntegrity": integrity,
        "durableResourceCount": retained_count,
        "reviewedDiscardResourceCount": discarded_count,
        "operationalResourceCount": operational_count,
        "captures": capture_evidence,
        "backupPath": str(backup_path.relative_to(state_dir)),
        "backupSha256": _file_sha256(backup_path),
        "backupIntegrity": backup_integrity,
    }
    evidence = {
        "plan": plan,
        "approvalScope": approval,
        "durability": durability,
        "results": {},
        "postCaptures": {},
    }
    _atomic_write(
        evidence_path,
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        ),
    )
    relative_path = str(evidence_path.relative_to(state_dir))
    with connection:
        for browser, browser_plan in plan["browsers"].items():
            if not browser_plan["targets"]:
                continue
            audit_id = (
                "mut_"
                + hashlib.sha256(f"{request_id}|{browser}".encode("utf-8")).hexdigest()[
                    :24
                ]
            )
            connection.execute(
                """
                INSERT INTO mutation_audits(
                  id, action, browser, request_id, approval_scope, plan_hash,
                  planned_count, status, evidence_path, before_capture_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?)
                """,
                (
                    audit_id,
                    plan["action"],
                    browser,
                    f"{request_id}:{browser}",
                    approval,
                    browser_plan["targetsHash"],
                    len(browser_plan["targets"]),
                    relative_path,
                    browser_plan["beforeCaptureId"],
                    plan["createdAt"],
                ),
            )
    return evidence_path


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


def build_archive_cleanup_plan(
    archive_plan: dict[str, Any],
    controls: dict[str, dict[str, int]],
) -> dict[str, Any]:
    request_id = secrets.token_hex(12)
    control_url = f"{TABATLAS_EXTENSION_URL_PREFIX}archive_complete.html"
    expected_url_hash = hashlib.sha256(control_url.encode("utf-8")).hexdigest()
    browsers: dict[str, dict[str, Any]] = {}
    for browser, control in sorted(controls.items()):
        targets = [
            {
                "controlTabId": int(control["controlTabId"]),
                "controlWindowId": int(control["controlWindowId"]),
                "expectedUrlHash": expected_url_hash,
            }
        ]
        payload = json.dumps(targets, separators=(",", ":"), ensure_ascii=True)
        browsers[browser] = {
            "targets": targets,
            "targetsHash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }
    plan = {
        "schemaVersion": 1,
        "action": "close_archive_control",
        "requestId": request_id,
        "archiveRequestId": archive_plan["requestId"],
        "createdAt": utc_now(),
        "browsers": browsers,
    }
    serialized = json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    plan["planHash"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return plan


def record_archive_cleanup_result(
    evidence_path: Path,
    cleanup_plan: dict[str, Any],
    complete: bool,
    results: dict[str, dict[str, Any]],
) -> None:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["controlCleanup"] = {
        "requestId": cleanup_plan.get("requestId"),
        "planHash": cleanup_plan.get("planHash"),
        "complete": bool(complete),
        "browsers": {
            browser: {
                "accepted": bool(result.get("accepted")),
                "controlTabId": result.get("controlTabId"),
                "controlWindowId": result.get("controlWindowId"),
                "status": result.get("status"),
                "reason": str(result.get("reason") or "")[:160],
            }
            for browser, result in results.items()
        },
        "completedAt": utc_now(),
    }
    _atomic_write(
        evidence_path,
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        ),
    )


def record_mutation_plan(
    connection: sqlite3.Connection,
    state_dir: Path,
    plan: dict[str, Any],
    approval_scope: str,
) -> Path:
    approval = approval_scope.strip()[:1000]
    if not approval:
        raise ValueError("A bounded explicit approval scope is required")
    request_id = str(plan["requestId"])
    safe_time = re.sub(r"[^0-9]", "", str(plan["createdAt"]))[:14] or "undated"
    evidence_path = (
        state_dir / "mutations" / f"{safe_time}-dedupe-{request_id[:8]}.json"
    )
    evidence = {
        "plan": plan,
        "approvalScope": approval,
        "results": {},
        "postCaptures": {},
    }
    _atomic_write(
        evidence_path,
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        ),
    )
    relative_path = str(evidence_path.relative_to(state_dir))
    with connection:
        for browser, browser_plan in plan["browsers"].items():
            if not browser_plan["targets"]:
                continue
            audit_id = (
                "mut_"
                + hashlib.sha256(f"{request_id}|{browser}".encode("utf-8")).hexdigest()[
                    :24
                ]
            )
            connection.execute(
                """
                INSERT INTO mutation_audits(
                  id, action, browser, request_id, approval_scope, plan_hash,
                  planned_count, status, evidence_path, before_capture_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?)
                """,
                (
                    audit_id,
                    plan["action"],
                    browser,
                    f"{request_id}:{browser}",
                    approval,
                    browser_plan["targetsHash"],
                    len(browser_plan["targets"]),
                    relative_path,
                    browser_plan["beforeCaptureId"],
                    plan["createdAt"],
                ),
            )
    return evidence_path


def finalize_mutation_plan(
    connection: sqlite3.Connection,
    state_dir: Path,
    plan: dict[str, Any],
    results: dict[str, dict[str, Any]],
    post_captures: dict[str, dict[str, Any]],
    error: str = "",
) -> dict[str, int]:
    request_id = str(plan["requestId"])
    row = connection.execute(
        "SELECT evidence_path FROM mutation_audits WHERE request_id LIKE ? LIMIT 1",
        (f"{request_id}:%",),
    ).fetchone()
    if not row:
        raise ValueError("Mutation audit plan was not recorded")
    evidence_path = state_dir / row["evidence_path"]
    post_verification: dict[str, dict[str, Any]] = {}
    valid_post_capture_ids: dict[str, str] = {}
    for browser, browser_plan in plan["browsers"].items():
        if not browser_plan["targets"]:
            continue
        post_capture = post_captures.get(browser)
        result = results.get(browser) or {}
        result_by_id = {
            int(item["tabId"]): item
            for item in result.get("results") or []
            if isinstance(item, dict) and item.get("tabId") is not None
        }
        post_row = _trusted_post_capture_row(
            connection,
            browser,
            browser_plan["beforeCaptureId"],
            post_capture,
        )
        post_tab_ids: set[int] = set()
        if post_row:
            valid_post_capture_ids[browser] = str(post_row["id"])
            for tab_row in connection.execute(
                "SELECT tab_id FROM tab_instances WHERE capture_id=?",
                (post_row["id"],),
            ):
                tab_id = _numeric_tab_id(tab_row["tab_id"])
                if tab_id is not None:
                    post_tab_ids.add(tab_id)
        closed_targets = {
            int(item["targetTabId"])
            for item in browser_plan["targets"]
            if result_by_id.get(int(item["targetTabId"]), {}).get("status") == "closed"
        }
        required_keepers = {
            int(item["keeperTabId"])
            for item in browser_plan["targets"]
            if int(item["targetTabId"]) in closed_targets
        }
        post_capture_valid = post_row is not None
        closed_absent = post_capture_valid and closed_targets.isdisjoint(post_tab_ids)
        keepers_present = post_capture_valid and required_keepers.issubset(post_tab_ids)
        post_verification[browser] = {
            "closedTargetsAbsent": closed_absent,
            "keepersPresent": keepers_present,
            "postCaptureValid": post_capture_valid,
            "verified": closed_absent and keepers_present,
        }
    evidence = {
        "plan": plan,
        "approvalScope": connection.execute(
            "SELECT approval_scope FROM mutation_audits WHERE request_id LIKE ? LIMIT 1",
            (f"{request_id}:%",),
        ).fetchone()["approval_scope"],
        "results": results,
        "postCaptures": {
            browser: {"captureId": item.get("id"), "tabCount": item.get("tab_count")}
            for browser, item in post_captures.items()
        },
        "postVerification": post_verification,
        "completedAt": utc_now(),
        "error": error[:500],
    }
    _atomic_write(
        evidence_path,
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        ),
    )
    closed_total = 0
    skipped_total = 0
    with connection:
        for browser, browser_plan in plan["browsers"].items():
            if not browser_plan["targets"]:
                continue
            result = results.get(browser) or {}
            closed = int(result.get("closedCount") or 0)
            skipped = int(result.get("skippedCount") or 0)
            planned = len(browser_plan["targets"])
            post_capture = post_captures.get(browser)
            if error or not result:
                status = "failed"
            elif (
                closed == planned
                and skipped == 0
                and post_capture
                and post_verification.get(browser, {}).get("verified")
            ):
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
                    evidence["completedAt"],
                    error[:500] or None,
                    f"{request_id}:{browser}",
                ),
            )
            closed_total += closed
            skipped_total += skipped
    return {
        "closed": closed_total,
        "skipped": skipped_total,
        "verifiedBrowsers": sum(
            bool(item.get("verified")) for item in post_verification.values()
        ),
        "expectedBrowsers": len(post_verification),
    }
