from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

from .capture import latest_capture_rows
from .catalog import _duplicate_keeper_order, _resources_for_capture_ids
from .common import normalize_browser
from .constants import TABATLAS_EXTENSION_URL_PREFIX
from .database import utc_now


def _numeric_tab_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


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


def build_archive_plan(
    connection: sqlite3.Connection,
    browsers: set[str],
    capture_ids: dict[str, str] | None = None,
    include_dismissed: bool = False,
) -> dict[str, Any]:
    requested = {normalize_browser(browser) for browser in browsers}
    if not requested or not requested.issubset({"chrome", "edge"}):
        raise ValueError("Captured-tab archiving supports Chrome and Edge only")
    captures = _resolve_plan_captures(connection, requested, capture_ids)

    targets_by_browser: dict[str, list[dict[str, Any]]] = {
        browser: [] for browser in requested
    }
    blocked: list[str] = []
    pending_discovery_ids: set[str] = set()
    dismissed_resource_ids: set[str] = set()
    retained_resource_ids: set[str] = set()
    discarded_resource_ids: set[str] = set()
    operational_resource_ids: set[str] = set()
    for resource in _resources_for_capture_ids(
        connection,
        [captures[browser]["id"] for browser in sorted(requested)],
        include_extension_tabs=True,
    ):
        resource_browsers = {str(tab.get("browser") or "") for tab in resource["tabs"]}
        if not resource_browsers.intersection(requested):
            continue
        is_extension_resource = str(resource["canonicalUrl"]).startswith(
            TABATLAS_EXTENSION_URL_PREFIX
        )
        if is_extension_resource:
            if str(resource["canonicalUrl"]).endswith("/archive_complete.html"):
                continue
            retention = "operational"
        elif resource["libraryState"] == "candidate":
            pending_discovery_ids.add(resource["resourceId"])
            continue
        elif resource["libraryState"] == "dismissed":
            dismissed_resource_ids.add(resource["resourceId"])
            if not include_dismissed:
                continue
            retention = "discard"
        else:
            retention = "retain"
        for tab in resource["tabs"]:
            browser = str(tab.get("browser") or "")
            if browser not in requested:
                continue
            tab_id = _numeric_tab_id(tab.get("tabId"))
            exact_url = str(tab.get("url") or "")
            if tab_id is None:
                blocked.append(str(tab.get("instanceId") or "unknown"))
                continue
            target = {
                "targetTabId": tab_id,
                "expectedUrlHash": hashlib.sha256(
                    exact_url.encode("utf-8")
                ).hexdigest(),
                "windowId": str(tab.get("windowId") or ""),
                "groupId": str(tab.get("groupId") or ""),
                "resourceId": resource["resourceId"],
                "targetInstanceId": tab["instanceId"],
                "retention": retention,
            }
            targets_by_browser[browser].append(target)
            if retention == "discard":
                discarded_resource_ids.add(resource["resourceId"])
            elif retention == "operational":
                operational_resource_ids.add(resource["resourceId"])
            else:
                retained_resource_ids.add(resource["resourceId"])
    if blocked:
        raise ValueError(
            f"Archive plan contains {len(blocked)} tab(s) without stable numeric IDs"
        )

    request_id = secrets.token_hex(12)
    browser_plans: dict[str, dict[str, Any]] = {}
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
        "action": "archive_captured_tabs",
        "policy": "close only freshly captured tabs after exact URL and context revalidation; retain accepted resources, discard only explicitly reviewed dismissals, and remove extension-owned operational pages",
        "requestId": request_id,
        "createdAt": utc_now(),
        "browsers": browser_plans,
        "summary": {
            "plannedClosures": sum(
                len(item["targets"]) for item in browser_plans.values()
            ),
            "resourceCount": len(
                retained_resource_ids
                | discarded_resource_ids
                | operational_resource_ids
            ),
            "retainedResourceCount": len(retained_resource_ids),
            "discardedResourceCount": len(discarded_resource_ids),
            "operationalResourceCount": len(operational_resource_ids),
            "plannedRetainedClosures": sum(
                target["retention"] == "retain"
                for item in browser_plans.values()
                for target in item["targets"]
            ),
            "plannedDiscardedClosures": sum(
                target["retention"] == "discard"
                for item in browser_plans.values()
                for target in item["targets"]
            ),
            "plannedOperationalClosures": sum(
                target["retention"] == "operational"
                for item in browser_plans.values()
                for target in item["targets"]
            ),
            "includeDismissed": bool(include_dismissed),
            "pendingDiscoveryCount": len(pending_discovery_ids),
            "pendingDiscoveryIds": sorted(pending_discovery_ids),
            "dismissedOpenResourceCount": len(dismissed_resource_ids),
            "dismissedOpenResourceIds": sorted(dismissed_resource_ids),
        },
    }
    plan_json = json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    plan["planHash"] = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
    return plan


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
