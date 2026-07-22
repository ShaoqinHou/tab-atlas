from __future__ import annotations

import sys
from collections.abc import Callable

from ..browser.receiver import run_capture, run_pairing, run_revocation
from ..catalog import inventory
from ..database import pairing_status, revoke_pairing
from ..presentation import generate_report
from .approvals import _bounded_timeout
from .context import CommandContext
from .formatting import output
from .paths import REPORT_ASSETS


def pair(context: CommandContext) -> int:
    context.close()
    print(
        f"Pairing receiver: http://127.0.0.1:9786 ({context.args.browser})",
        flush=True,
    )

    def announce(code: str) -> None:
        print(f"One-time code: {code}", flush=True)
        print(
            "Open the TabAtlas Capture popup in that browser and enter the code.",
            flush=True,
        )

    completed, _code, paired_browser = run_pairing(
        context.database_path,
        context.state_dir,
        context.args.browser,
        _bounded_timeout(context.args.timeout),
        announce,
    )
    if not completed:
        print("Pairing timed out; no token was issued.", file=sys.stderr)
        return 1
    output({"paired": True, "browser": paired_browser})
    return 0


def capture(context: CommandContext) -> int:
    targets = (
        {"chrome", "edge"} if context.args.browser == "all" else {context.args.browser}
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
    print(
        f"Receiver waiting for {', '.join(sorted(targets))}; enabled extensions poll within 30 seconds.",
        flush=True,
    )
    completed, captures = run_capture(
        context.database_path,
        context.state_dir,
        targets,
        _bounded_timeout(context.args.timeout),
    )
    capture_output = {
        "complete": completed,
        "requestedBrowsers": sorted(targets),
        "captured": {
            browser: {
                "captureId": result["id"],
                "tabs": result["tab_count"],
                "resources": result["resource_count"],
                "windows": result["window_count"],
                "groups": result["group_count"],
                "newResources": result.get("new_resource_count", 0),
                "pendingResources": result.get("candidate_resource_count", 0),
            }
            for browser, result in captures.items()
        },
        "missingBrowsers": sorted(targets - captures.keys()),
    }
    if context.args.command == "refresh" and completed:
        context.reconnect()
        report_path = generate_report(
            context.connection,
            context.args.output.resolve(),
            REPORT_ASSETS,
            context.state_dir,
        )
        capture_output["report"] = str(report_path)
        capture_output["inventory"] = inventory(context.connection)
        capture_output["reviewRequired"] = bool(
            capture_output["inventory"]["pendingDiscoveries"]
        )
    output(capture_output)
    return 0 if completed else 1


def revoke(context: CommandContext) -> int:
    revoked = revoke_pairing(context.connection, context.args.browser)
    context.close()
    extension_notified = False
    if revoked:
        print(
            f"Pairing revoked; waiting briefly for {context.args.browser} to turn itself off.",
            flush=True,
        )
        extension_notified = run_revocation(
            context.database_path,
            context.state_dir,
            context.args.browser,
            _bounded_timeout(context.args.timeout),
        )
    output(
        {
            "browser": context.args.browser,
            "revoked": revoked,
            "extensionNotified": extension_notified,
        }
    )
    return 0


BROWSER_HANDLERS: dict[str, Callable[[CommandContext], int]] = {
    "pair": pair,
    "capture": capture,
    "refresh": capture,
    "revoke": revoke,
}
