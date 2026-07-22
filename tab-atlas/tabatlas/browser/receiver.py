from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Any, Callable

from ..common import normalize_browser
from ..constants import MUTATION_PROTOCOL_VERSION
from ..database import connect
from .protocol import (
    EXPECTED_EXTENSION_ID as EXPECTED_EXTENSION_ID,
    HOST,
    PAIRING_ALPHABET,
    PORT,
    ReceiverSession,
    TabAtlasHandler,
    TabAtlasHTTPServer,
)


def pairing_code() -> str:
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))


def run_pairing(
    database_path: Path,
    state_dir: Path,
    browser: str,
    timeout_seconds: int,
    announce: Callable[[str], None] | None = None,
) -> tuple[bool, str, str | None]:
    browser = normalize_browser(browser)
    code = pairing_code()
    session = ReceiverSession(
        mode="pair",
        database_path=database_path,
        state_dir=state_dir,
        expected_browsers={browser},
        pairing_code=code,
    )
    completed = _run(
        session,
        timeout_seconds,
        on_ready=(lambda: announce(code)) if announce else None,
    )
    return completed, code, session.paired_browser


def run_capture(
    database_path: Path,
    state_dir: Path,
    browsers: set[str],
    timeout_seconds: int,
) -> tuple[bool, dict[str, dict[str, Any]]]:
    session = ReceiverSession(
        mode="capture",
        database_path=database_path,
        state_dir=state_dir,
        expected_browsers={normalize_browser(browser) for browser in browsers},
    )
    completed = _run(session, timeout_seconds)
    return completed, session.captured


def run_revocation(
    database_path: Path,
    state_dir: Path,
    browser: str,
    timeout_seconds: int,
) -> bool:
    session = ReceiverSession(
        mode="revoke",
        database_path=database_path,
        state_dir=state_dir,
        expected_browsers={normalize_browser(browser)},
    )
    return _run(session, timeout_seconds)


def run_mutation(
    database_path: Path,
    state_dir: Path,
    plan: dict[str, Any],
    timeout_seconds: int,
) -> tuple[bool, dict[str, dict[str, Any]]]:
    expected = {
        browser
        for browser, browser_plan in plan.get("browsers", {}).items()
        if browser_plan.get("targets")
    }
    if not expected:
        return True, {}
    require_mutation_protocol(database_path, expected)
    session = ReceiverSession(
        mode="mutate",
        database_path=database_path,
        state_dir=state_dir,
        expected_browsers=expected,
        request_id=str(plan["requestId"]),
        mutation_plan=plan,
    )
    completed = _run(session, timeout_seconds)
    return completed, session.mutated


def run_archive_cleanup(
    database_path: Path,
    state_dir: Path,
    plan: dict[str, Any],
    timeout_seconds: int,
) -> tuple[bool, dict[str, dict[str, Any]]]:
    expected = {
        browser
        for browser, browser_plan in plan.get("browsers", {}).items()
        if browser_plan.get("targets")
    }
    if not expected:
        return True, {}
    require_mutation_protocol(database_path, expected)
    session = ReceiverSession(
        mode="cleanup",
        database_path=database_path,
        state_dir=state_dir,
        expected_browsers=expected,
        request_id=str(plan["requestId"]),
        mutation_plan=plan,
    )
    completed = _run(session, timeout_seconds)
    return completed, session.cleaned


def require_mutation_protocol(database_path: Path, browsers: set[str]) -> None:
    expected = {normalize_browser(browser) for browser in browsers}
    connection = connect(database_path)
    try:
        statuses = {
            item["browser"]: item
            for item in connection.execute(
                "SELECT browser, enabled, protocol_version FROM pairings"
            ).fetchall()
        }
    finally:
        connection.close()
    unsupported = [
        browser
        for browser in sorted(expected)
        if browser not in statuses
        or not bool(statuses[browser]["enabled"])
        or int(statuses[browser]["protocol_version"]) < MUTATION_PROTOCOL_VERSION
    ]
    if unsupported:
        names = ", ".join(browser.title() for browser in unsupported)
        raise ValueError(
            f"Tab-closing requires TabAtlas protocol {MUTATION_PROTOCOL_VERSION}. "
            f"Reload the TabAtlas Bridge extension in {names}, then run refresh again."
        )


def _run(
    session: ReceiverSession,
    timeout_seconds: int,
    on_ready: Callable[[], None] | None = None,
) -> bool:
    server = TabAtlasHTTPServer((HOST, PORT), TabAtlasHandler)
    server.session = session
    thread = threading.Thread(
        target=server.serve_forever, name="tab-atlas-receiver", daemon=True
    )
    thread.start()
    try:
        if on_ready:
            on_ready()
        return session.done.wait(timeout_seconds)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
