from __future__ import annotations

import argparse
from pathlib import Path

from ..report_server import DEFAULT_REPORT_PORT
from ..workspace import DEFAULT_WORKSPACE_PORT
from .paths import DEFAULT_REPORT, DEFAULT_STATE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tab-atlas",
        description="Local, agent-operated Chrome and Edge tab catalog.",
    )
    parser.add_argument(
        "--state", type=Path, default=DEFAULT_STATE, help="Local state directory"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "status", help="Show safe local status and aggregate inventory"
    )

    extension_parser = subparsers.add_parser(
        "prepare-extension", help="Create the stable unpacked extension directory"
    )
    extension_parser.add_argument("--destination", type=Path)

    import_parser = subparsers.add_parser(
        "import", help="Import a preserved browser snapshot"
    )
    import_parser.add_argument("path", type=Path)
    import_parser.add_argument("--source", default="manual_import")

    pair_parser = subparsers.add_parser("pair", help="Pair one browser extension")
    pair_parser.add_argument("--browser", choices=["chrome", "edge"], required=True)
    pair_parser.add_argument("--timeout", type=int, default=180)

    revoke_parser = subparsers.add_parser("revoke", help="Revoke one browser pairing")
    revoke_parser.add_argument("--browser", choices=["chrome", "edge"], required=True)
    revoke_parser.add_argument("--timeout", type=int, default=35)

    capture_parser = subparsers.add_parser(
        "capture", help="Capture paired running browsers"
    )
    capture_parser.add_argument(
        "--browser", choices=["all", "chrome", "edge"], default="all"
    )
    capture_parser.add_argument("--timeout", type=int, default=50)

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Capture open tabs, stage new discoveries, and regenerate the report",
    )
    refresh_parser.add_argument(
        "--browser", choices=["all", "chrome", "edge"], default="all"
    )
    refresh_parser.add_argument("--timeout", type=int, default=50)
    refresh_parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)

    subparsers.add_parser("inventory", help="Show aggregate current-library metrics")

    discoveries_parser = subparsers.add_parser(
        "discoveries",
        help="List newly observed resources awaiting a library decision",
    )
    discoveries_parser.add_argument("--limit", type=int, default=50)
    discoveries_parser.add_argument("--offset", type=int, default=0)
    discoveries_parser.add_argument(
        "--state",
        dest="discovery_state",
        choices=["pending", "dismissed"],
        default="pending",
        help="List pending discoveries or previously dismissed resources",
    )

    accept_parser = subparsers.add_parser(
        "accept",
        help="Accept selected or all pending discoveries into the durable library",
    )
    accept_parser.add_argument("--all", action="store_true", dest="all_discoveries")
    accept_parser.add_argument("--resource-id", action="append", default=[])

    dismiss_parser = subparsers.add_parser(
        "dismiss",
        help="Dismiss selected or all pending discoveries without adding them to the library",
    )
    dismiss_parser.add_argument("--all", action="store_true", dest="all_discoveries")
    dismiss_parser.add_argument("--resource-id", action="append", default=[])

    remove_parser = subparsers.add_parser(
        "remove",
        help="Remove accepted resources from the library while retaining recoverable evidence",
    )
    remove_parser.add_argument("--resource-id", action="append", required=True)

    batch_parser = subparsers.add_parser(
        "batch", help="Emit a bounded resource batch for Codex"
    )
    batch_parser.add_argument(
        "--state",
        dest="batch_state",
        choices=["all", "unclassified", "actionable"],
        default="unclassified",
    )
    batch_parser.add_argument("--limit", type=int, default=30)
    batch_parser.add_argument("--offset", type=int, default=0)

    apply_parser = subparsers.add_parser(
        "apply", help="Apply structured resource annotations"
    )
    apply_parser.add_argument("path", type=Path)

    organization_parser = subparsers.add_parser(
        "stage-organization",
        help="Stage one whole-cohort organization plan for explicit review",
    )
    organization_parser.add_argument("path", type=Path)

    query_parser = subparsers.add_parser(
        "query", help="Search the current local library"
    )
    query_parser.add_argument("--text", required=True)
    query_parser.add_argument("--limit", type=int, default=30)

    report_parser = subparsers.add_parser(
        "report", help="Generate the local read-only HTML report"
    )
    report_parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    report_parser.add_argument("--open", action="store_true", dest="open_report")

    view_parser = subparsers.add_parser(
        "view",
        help="Generate and serve the report from a read-only loopback viewer",
    )
    view_parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    view_parser.add_argument("--port", type=int, default=DEFAULT_REPORT_PORT)
    view_parser.add_argument("--open", action="store_true", dest="open_report")

    workspace_parser = subparsers.add_parser(
        "workspace",
        help="Start the authenticated, writable TabAtlas workspace on demand",
    )
    workspace_parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    workspace_parser.add_argument("--port", type=int, default=DEFAULT_WORKSPACE_PORT)
    workspace_parser.add_argument("--open", action="store_true", dest="open_report")
    workspace_parser.add_argument(
        "--rotate-access",
        action="store_true",
        help="Revoke previously authorized workspace browser sessions",
    )

    enrich_parser = subparsers.add_parser(
        "enrich",
        help="Cache privacy-bounded public visual evidence for the current library",
    )
    enrich_parser.add_argument("--refresh", action="store_true")
    enrich_parser.add_argument("--workers", type=int, default=8)

    preview_parser = subparsers.add_parser(
        "register-preview",
        help="Validate and store a local screenshot for one known resource",
    )
    preview_parser.add_argument("--resource-id", required=True)
    preview_parser.add_argument("path", type=Path)

    dedupe_parser = subparsers.add_parser(
        "dedupe",
        help="Preview or execute conservative exact-URL duplicate closure",
    )
    dedupe_parser.add_argument(
        "--browser", choices=["all", "chrome", "edge"], default="all"
    )
    dedupe_parser.add_argument("--execute", action="store_true")
    dedupe_parser.add_argument("--approval", default="")
    dedupe_parser.add_argument("--resource-id", action="append", default=[])
    dedupe_parser.add_argument("--timeout", type=int, default=70)

    approval_parser = subparsers.add_parser(
        "dedupe-approval",
        help="Inspect, grant, or revoke the private standing exact-duplicate approval",
    )
    approval_parser.add_argument("action", choices=["status", "grant", "revoke"])
    approval_parser.add_argument("--scope", default="")

    archive_parser = subparsers.add_parser(
        "archive-tabs",
        help="Preview or execute verified capture-to-library closure for every captured tab",
    )
    archive_parser.add_argument(
        "--browser", choices=["all", "chrome", "edge"], default="all"
    )
    archive_parser.add_argument("--execute", action="store_true")
    archive_parser.add_argument(
        "--include-dismissed",
        action="store_true",
        help="Close explicitly reviewed dismissed tabs as audited discards",
    )
    archive_parser.add_argument("--approval", default="")
    archive_parser.add_argument("--timeout", type=int, default=240)

    archive_approval_parser = subparsers.add_parser(
        "archive-approval",
        help="Inspect, grant, or revoke the private standing captured-tab archive approval",
    )
    archive_approval_parser.add_argument(
        "action", choices=["status", "grant", "revoke"]
    )
    archive_approval_parser.add_argument("--scope", default="")
    return parser
