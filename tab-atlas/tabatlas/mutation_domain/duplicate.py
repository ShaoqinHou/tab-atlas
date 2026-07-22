from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..catalog import _duplicate_keeper_order, _resources_for_capture_ids
from ..common import _atomic_write, normalize_browser
from ..database import utc_now
from .shared import (
    _numeric_tab_id,
    _resolve_plan_captures,
    _trusted_post_capture_row,
)


def build_exact_duplicate_plan(
    connection: sqlite3.Connection,
    browsers: set[str],
    resource_ids: set[str] | None = None,
    capture_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    requested = {normalize_browser(browser) for browser in browsers}
    if not requested or not requested.issubset({"chrome", "edge"}):
        raise ValueError("Exact duplicate planning supports Chrome and Edge only")
    captures = _resolve_plan_captures(connection, requested, capture_ids)

    targets_by_browser: dict[str, list[dict[str, Any]]] = {
        browser: [] for browser in requested
    }
    duplicate_sets = 0
    excluded_instances = 0
    for resource in _resources_for_capture_ids(
        connection,
        [captures[browser]["id"] for browser in sorted(requested)],
    ):
        if resource_ids and resource["resourceId"] not in resource_ids:
            continue
        buckets: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(
            list
        )
        for tab in resource["tabs"]:
            browser = str(tab.get("browser") or "")
            if browser not in requested:
                continue
            url = str(tab.get("url") or "")
            try:
                scheme = urlsplit(url).scheme.casefold()
            except ValueError:
                continue
            if scheme != "https":
                continue
            buckets[
                (
                    browser,
                    str(tab.get("windowId") or ""),
                    str(tab.get("groupId") or ""),
                    url,
                )
            ].append(tab)

        for (browser, window_id, group_id, exact_url), tabs in buckets.items():
            if len(tabs) < 2:
                continue
            duplicate_sets += 1
            ordered = sorted(tabs, key=_duplicate_keeper_order)
            keeper = ordered[0]
            keeper_tab_id = _numeric_tab_id(keeper.get("tabId"))
            if keeper_tab_id is None:
                excluded_instances += len(ordered) - 1
                continue
            expected_url_hash = hashlib.sha256(exact_url.encode("utf-8")).hexdigest()
            for target in ordered[1:]:
                target_tab_id = _numeric_tab_id(target.get("tabId"))
                if (
                    target_tab_id is None
                    or target.get("active")
                    or target.get("highlighted")
                    or target.get("pinned")
                    or target.get("audible")
                ):
                    excluded_instances += 1
                    continue
                targets_by_browser[browser].append(
                    {
                        "targetTabId": target_tab_id,
                        "keeperTabId": keeper_tab_id,
                        "expectedUrlHash": expected_url_hash,
                        "windowId": window_id,
                        "groupId": group_id,
                        "targetInstanceId": target["instanceId"],
                        "keeperInstanceId": keeper["instanceId"],
                    }
                )

    request_id = secrets.token_hex(12)
    browser_plans = {}
    for browser in sorted(requested):
        targets = sorted(
            targets_by_browser[browser],
            key=lambda item: (item["windowId"], item["groupId"], item["targetTabId"]),
        )
        target_payload = json.dumps(targets, separators=(",", ":"), ensure_ascii=True)
        browser_plans[browser] = {
            "beforeCaptureId": captures[browser]["id"],
            "targets": targets,
            "targetsHash": hashlib.sha256(target_payload.encode("utf-8")).hexdigest(),
        }
    plan = {
        "schemaVersion": 1,
        "action": "close_exact_duplicates",
        "policy": "same-browser-window-group exact HTTPS URL; retain one; protect active, highlighted, pinned, audible",
        "requestId": request_id,
        "createdAt": utc_now(),
        "browsers": browser_plans,
        "summary": {
            "duplicateSets": duplicate_sets,
            "plannedClosures": sum(
                len(item["targets"]) for item in browser_plans.values()
            ),
            "excludedInstances": excluded_instances,
        },
    }
    plan_json = json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    plan["planHash"] = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
    return plan


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
