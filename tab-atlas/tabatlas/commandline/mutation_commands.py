from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..browser.receiver import (
    require_mutation_protocol,
    run_archive_cleanup,
    run_capture,
    run_mutation,
)
from ..database import pairing_status
from ..mutations import (
    build_archive_cleanup_plan,
    build_archive_plan,
    build_exact_duplicate_plan,
    finalize_archive_plan,
    finalize_mutation_plan,
    record_archive_cleanup_result,
    record_archive_plan,
    record_mutation_plan,
)
from .approvals import (
    _archive_approval_path,
    _bounded_timeout,
    _load_archive_approval,
    _load_standing_approval,
    _standing_approval_path,
    _write_archive_approval,
    _write_standing_approval,
)
from .context import CommandContext
from .formatting import _archive_output, _dedupe_output, output


def dedupe(context: CommandContext) -> int:
    targets = (
        {"chrome", "edge"} if context.args.browser == "all" else {context.args.browser}
    )
    resource_ids = {
        str(value).strip() for value in context.args.resource_id if str(value).strip()
    }
    if not context.args.execute:
        plan = build_exact_duplicate_plan(
            context.connection,
            targets,
            resource_ids or None,
        )
        output(_dedupe_output(plan))
        return 0
    approval_scope = context.args.approval.strip() or _load_standing_approval(
        context.state_dir
    )
    if not approval_scope:
        raise ValueError(
            "--execute requires --approval or an active private dedupe-approval"
        )
    paired = {
        item["browser"]
        for item in pairing_status(context.connection)
        if item["enabled"]
    }
    missing = sorted(targets - paired)
    if missing:
        raise ValueError(f"Pair the following browser(s) first: {', '.join(missing)}")

    context.close()
    pre_complete, pre_captures = run_capture(
        context.database_path,
        context.state_dir,
        targets,
        _bounded_timeout(context.args.timeout),
    )
    if not pre_complete:
        output(
            {
                "complete": False,
                "phase": "fresh_capture",
                "browsers": sorted(targets),
            }
        )
        return 1
    require_mutation_protocol(context.database_path, targets)

    context.reconnect()
    plan = build_exact_duplicate_plan(
        context.connection,
        targets,
        resource_ids or None,
        {browser: result["id"] for browser, result in pre_captures.items()},
    )
    if not plan["summary"]["plannedClosures"]:
        output({**_dedupe_output(plan), "complete": True, "executed": False})
        return 0
    evidence_path = record_mutation_plan(
        context.connection,
        context.state_dir,
        plan,
        approval_scope,
    )
    context.close()

    mutation_complete, results = run_mutation(
        context.database_path,
        context.state_dir,
        plan,
        _bounded_timeout(context.args.timeout),
    )
    affected = {
        browser
        for browser, browser_plan in plan["browsers"].items()
        if browser_plan["targets"]
    }
    post_complete = False
    post_captures: dict[str, dict[str, Any]] = {}
    if mutation_complete:
        post_complete, post_captures = run_capture(
            context.database_path,
            context.state_dir,
            affected,
            _bounded_timeout(context.args.timeout),
        )
    error = ""
    if not mutation_complete:
        error = "mutation receiver timed out before every browser returned results"
    elif not post_complete:
        error = "post-action capture did not complete"
    context.reconnect()
    totals = finalize_mutation_plan(
        context.connection,
        context.state_dir,
        plan,
        results,
        post_captures,
        error,
    )
    complete = (
        mutation_complete
        and post_complete
        and totals["skipped"] == 0
        and totals["verifiedBrowsers"] == totals["expectedBrowsers"]
    )
    output(
        {
            **_dedupe_output(plan),
            "complete": complete,
            "executed": True,
            "closed": totals["closed"],
            "skipped": totals["skipped"],
            "postVerifiedBrowsers": totals["verifiedBrowsers"],
            "postCaptureComplete": post_complete,
            "auditPath": str(evidence_path),
            "error": error,
        }
    )
    return 0 if complete else 1


def dedupe_approval(context: CommandContext) -> int:
    if context.args.action == "grant":
        scope = context.args.scope.strip()
        if not scope:
            raise ValueError("grant requires a bounded --scope")
        path = _write_standing_approval(context.state_dir, scope, True)
        output(
            {
                "active": True,
                "action": "close_exact_duplicates",
                "path": str(path),
            }
        )
        return 0
    if context.args.action == "revoke":
        path = _write_standing_approval(
            context.state_dir,
            "Standing exact-duplicate approval revoked by the user or operator.",
            False,
        )
        output(
            {
                "active": False,
                "action": "close_exact_duplicates",
                "path": str(path),
            }
        )
        return 0
    output(
        {
            "active": bool(_load_standing_approval(context.state_dir)),
            "action": "close_exact_duplicates",
            "path": str(_standing_approval_path(context.state_dir)),
        }
    )
    return 0


def archive_tabs(context: CommandContext) -> int:
    targets = (
        {"chrome", "edge"} if context.args.browser == "all" else {context.args.browser}
    )
    if not context.args.execute:
        plan = build_archive_plan(
            context.connection,
            targets,
            include_dismissed=context.args.include_dismissed,
        )
        output(_archive_output(plan))
        return 0
    approval_scope = context.args.approval.strip() or _load_archive_approval(
        context.state_dir
    )
    if not approval_scope:
        raise ValueError(
            "--execute requires --approval or an active private archive-approval"
        )
    paired = {
        item["browser"]
        for item in pairing_status(context.connection)
        if item["enabled"]
    }
    missing = sorted(targets - paired)
    if missing:
        raise ValueError(f"Pair the following browser(s) first: {', '.join(missing)}")

    context.close()
    pre_complete, pre_captures = run_capture(
        context.database_path,
        context.state_dir,
        targets,
        _bounded_timeout(context.args.timeout),
    )
    if not pre_complete:
        output(
            {
                "complete": False,
                "phase": "fresh_capture",
                "browsers": sorted(targets),
            }
        )
        return 1
    require_mutation_protocol(context.database_path, targets)

    context.reconnect()
    plan = build_archive_plan(
        context.connection,
        targets,
        {browser: result["id"] for browser, result in pre_captures.items()},
        include_dismissed=context.args.include_dismissed,
    )
    if plan["summary"]["pendingDiscoveryCount"]:
        output(
            {
                **_archive_output(plan),
                "complete": False,
                "executed": False,
                "phase": "discovery_review",
                "nextCommand": "tab-atlas discoveries",
            }
        )
        return 1
    if (
        plan["summary"]["dismissedOpenResourceCount"]
        and not plan["summary"]["includeDismissed"]
    ):
        output(
            {
                **_archive_output(plan),
                "complete": False,
                "executed": False,
                "phase": "dismissed_tabs_open",
                "nextCommand": "tab-atlas archive-tabs --include-dismissed",
            }
        )
        return 1
    if not plan["summary"]["plannedClosures"]:
        output({**_archive_output(plan), "complete": True, "executed": False})
        return 0
    evidence_path = record_archive_plan(
        context.connection,
        context.state_dir,
        plan,
        approval_scope,
    )
    context.close()

    mutation_complete, results = run_mutation(
        context.database_path,
        context.state_dir,
        plan,
        _bounded_timeout(context.args.timeout),
    )
    affected = {
        browser
        for browser, browser_plan in plan["browsers"].items()
        if browser_plan["targets"]
    }
    post_complete = False
    post_captures: dict[str, dict[str, Any]] = {}
    if mutation_complete:
        post_complete, post_captures = run_capture(
            context.database_path,
            context.state_dir,
            affected,
            _bounded_timeout(context.args.timeout),
        )
    error = ""
    if not mutation_complete:
        error = "archive receiver timed out before every browser returned results"
    elif not post_complete:
        error = "post-archive capture did not complete"
    context.reconnect()
    totals = finalize_archive_plan(
        context.connection,
        context.state_dir,
        plan,
        results,
        post_captures,
        error,
    )
    archive_complete = (
        mutation_complete
        and post_complete
        and totals["skipped"] == 0
        and totals["verifiedBrowsers"] == totals["expectedBrowsers"]
        and totals["allControlsReported"]
    )
    cleanup_complete = False
    cleanup_results: dict[str, dict[str, Any]] = {}
    cleanup_plan: dict[str, Any] | None = None
    if totals["controls"]:
        cleanup_plan = build_archive_cleanup_plan(plan, totals["controls"])
        context.close()
        cleanup_received, cleanup_results = run_archive_cleanup(
            context.database_path,
            context.state_dir,
            cleanup_plan,
            _bounded_timeout(context.args.timeout),
        )
        cleanup_complete = (
            cleanup_received
            and set(cleanup_results) == set(totals["controls"])
            and all(
                result.get("status") == "closed" for result in cleanup_results.values()
            )
        )
        record_archive_cleanup_result(
            evidence_path,
            cleanup_plan,
            cleanup_complete,
            cleanup_results,
        )
        context.reconnect()
    complete = archive_complete and cleanup_complete
    output(
        {
            **_archive_output(plan),
            "complete": complete,
            "archiveVerified": archive_complete,
            "controlCleanupComplete": cleanup_complete,
            "executed": True,
            "closed": totals["closed"],
            "skipped": totals["skipped"],
            "archivedResources": totals["archivedResources"],
            "discardedResources": totals["discardedResources"],
            "operationalResources": totals["operationalResources"],
            "postVerifiedBrowsers": totals["verifiedBrowsers"],
            "postCaptureComplete": post_complete,
            "auditPath": str(evidence_path),
            "error": error,
        }
    )
    return 0 if complete else 1


def archive_approval(context: CommandContext) -> int:
    if context.args.action == "grant":
        scope = context.args.scope.strip()
        if not scope:
            raise ValueError("grant requires a bounded --scope")
        path = _write_archive_approval(context.state_dir, scope, True)
        output(
            {
                "active": True,
                "action": "archive_captured_tabs",
                "path": str(path),
            }
        )
        return 0
    if context.args.action == "revoke":
        path = _write_archive_approval(
            context.state_dir,
            "Standing captured-tab archive approval revoked by the user or operator.",
            False,
        )
        output(
            {
                "active": False,
                "action": "archive_captured_tabs",
                "path": str(path),
            }
        )
        return 0
    output(
        {
            "active": bool(_load_archive_approval(context.state_dir)),
            "action": "archive_captured_tabs",
            "path": str(_archive_approval_path(context.state_dir)),
        }
    )
    return 0


MUTATION_HANDLERS: dict[str, Callable[[CommandContext], int]] = {
    "dedupe": dedupe,
    "dedupe-approval": dedupe_approval,
    "archive-tabs": archive_tabs,
    "archive-approval": archive_approval,
}
