from __future__ import annotations

import hmac
import hashlib
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from tab_atlas_core import (
    connect,
    LEGACY_CAPTURE_PROTOCOL_VERSION,
    mark_pairing_seen,
    MUTATION_PROTOCOL_VERSION,
    normalize_browser,
    pairing_secret,
    protocol_proof,
    save_pairing,
    store_snapshot,
    TABATLAS_EXTENSION_ID,
    TARGET_HASH_PROTOCOL_VERSION,
    token_hash,
)


HOST = "127.0.0.1"
PORT = 9786
MAX_BODY_BYTES = 10 * 1024 * 1024
EXPECTED_EXTENSION_ID = TABATLAS_EXTENSION_ID
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass
class ReceiverSession:
    mode: str
    database_path: Path
    state_dir: Path
    expected_browsers: set[str]
    request_id: str = field(default_factory=lambda: secrets.token_hex(12))
    pairing_code: str | None = None
    captured: dict[str, dict[str, Any]] = field(default_factory=dict)
    mutation_plan: dict[str, Any] | None = None
    mutated: dict[str, dict[str, Any]] = field(default_factory=dict)
    cleaned: dict[str, dict[str, Any]] = field(default_factory=dict)
    paired_browser: str | None = None
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


class TabAtlasHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    session: ReceiverSession


class TabAtlasHandler(BaseHTTPRequestHandler):
    server: TabAtlasHTTPServer

    def do_OPTIONS(self) -> None:
        if not self._request_allowed(require_origin=True):
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "origin denied"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header(
            "Access-Control-Allow-Headers",
            "content-type, x-tabatlas-auth, x-tabatlas-browser, "
            "x-tabatlas-extension, x-tabatlas-nonce, x-tabatlas-protocol",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "300")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._request_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "request denied"})
            return
        path = urlsplit(self.path).path
        if path != "/v1/command":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        pairing = self._authenticate_signed("command")
        if not pairing:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        browser = pairing["browser"]
        if not pairing["enabled"]:
            payload = {
                "ok": False,
                "action": "revoked",
                "requestId": "",
                "serverProof": protocol_proof(
                    pairing["secret"],
                    "response",
                    browser,
                    pairing["extension_id"],
                    pairing["nonce"],
                    "",
                    "revoked",
                ),
            }
            self._send_json(HTTPStatus.UNAUTHORIZED, payload)
            if self.server.session.mode == "revoke" and browser in self.server.session.expected_browsers:
                self.server.session.done.set()
            return
        connection = connect(self.server.session.database_path)
        try:
            mark_pairing_seen(connection, browser, pairing["protocol_version"])
        finally:
            connection.close()
        targets: list[dict[str, Any]] = []
        targets_hash = ""
        with self.server.session.lock:
            session = self.server.session
            if session.mode == "capture":
                if browser not in session.expected_browsers or browser in session.captured:
                    action = "idle"
                    request_id = ""
                else:
                    action = "capture"
                    request_id = session.request_id
            elif session.mode == "mutate" and session.mutation_plan:
                browser_plan = session.mutation_plan.get("browsers", {}).get(browser)
                if browser not in session.expected_browsers or browser in session.mutated or not browser_plan:
                    action = "idle"
                    request_id = ""
                else:
                    action = str(session.mutation_plan.get("action") or "close_exact_duplicates")
                    request_id = str(session.mutation_plan["requestId"])
                    targets = browser_plan["targets"]
                    targets_hash = browser_plan["targetsHash"]
            elif session.mode == "cleanup" and session.mutation_plan:
                browser_plan = session.mutation_plan.get("browsers", {}).get(browser)
                if browser not in session.expected_browsers or browser in session.cleaned or not browser_plan:
                    action = "idle"
                    request_id = ""
                else:
                    action = "close_archive_control"
                    request_id = str(session.mutation_plan["requestId"])
                    targets = browser_plan["targets"]
                    targets_hash = browser_plan["targetsHash"]
            else:
                self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": "receiver is not active"})
                return
        response_parts = [
            "response",
            browser,
            pairing["extension_id"],
            pairing["nonce"],
            request_id,
            action,
        ]
        if pairing["protocol_version"] >= TARGET_HASH_PROTOCOL_VERSION:
            response_parts.append(targets_hash)
        payload = {
            "ok": True,
            "action": action,
            "requestId": request_id,
            "targets": targets,
            "targetsHash": targets_hash,
            "serverProof": protocol_proof(
                pairing["secret"],
                *response_parts,
            ),
        }
        self._send_json(HTTPStatus.OK, payload)

    def do_POST(self) -> None:
        if not self._request_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "request denied"})
            return
        path = urlsplit(self.path).path
        if path == "/v1/pair":
            self._pair()
            return
        if path == "/v1/snapshot":
            self._snapshot()
            return
        if path == "/v1/mutation":
            self._mutation()
            return
        if path == "/v1/cleanup":
            self._cleanup()
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def _pair(self) -> None:
        session = self.server.session
        if session.mode != "pair" or not session.pairing_code:
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": "pairing is not active"})
            return
        try:
            body = self._read_json(64 * 1024)
            browser = normalize_browser(body.get("browser"))
            extension_id = str(body.get("extensionId") or "")
            nonce = str(body.get("nonce") or "")
            client_proof = str(body.get("clientProof") or "")
        except (ValueError, TypeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)})
            return
        if browser not in session.expected_browsers:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "wrong browser"})
            return
        if extension_id != EXPECTED_EXTENSION_ID:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "wrong extension"})
            return
        if not re.fullmatch(r"[a-f0-9]{32}", nonce) or not re.fullmatch(r"[a-f0-9]{64}", client_proof):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid pairing proof"})
            return
        pairing_key = token_hash(session.pairing_code)
        expected_proof = protocol_proof(
            pairing_key,
            "pair",
            browser,
            extension_id,
            nonce,
        )
        if not hmac.compare_digest(client_proof, expected_proof):
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "invalid pairing proof"})
            return

        token = secrets.token_urlsafe(32)
        issued_key = token_hash(token)
        connection = connect(session.database_path)
        try:
            save_pairing(connection, browser, extension_id, token)
        finally:
            connection.close()
        session.paired_browser = browser
        self._send_json(HTTPStatus.OK, {
            "ok": True,
            "token": token,
            "browser": browser,
            "serverProof": protocol_proof(
                pairing_key,
                "paired",
                browser,
                extension_id,
                nonce,
                issued_key,
            ),
        })
        session.done.set()

    def _snapshot(self) -> None:
        session = self.server.session
        if session.mode != "capture":
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": "capture is not active"})
            return
        try:
            raw_body = self._read_body(MAX_BODY_BYTES)
            body = json.loads(raw_body.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            request_id = str(body.get("requestId") or "")
            body_hash = hashlib.sha256(raw_body).hexdigest()
            pairing = self._authenticate_signed("snapshot", request_id, body_hash)
            if not pairing or not pairing["enabled"]:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                return
            browser = normalize_browser(body.get("browser"))
            extension_id = str(body.get("extensionId") or "")
            if browser != pairing["browser"]:
                raise ValueError("browser does not match pairing")
            if extension_id != pairing["extension_id"] or extension_id != EXPECTED_EXTENSION_ID:
                raise ValueError("extension does not match pairing")
            if browser not in session.expected_browsers:
                raise ValueError("browser was not requested")
            if request_id != session.request_id:
                raise ValueError("capture request does not match receiver run")
            connection = connect(session.database_path)
            try:
                mark_pairing_seen(connection, browser, pairing["protocol_version"])
                result = store_snapshot(
                    connection,
                    session.state_dir,
                    body,
                    "extension_live",
                    new_resource_state="candidate",
                )
            finally:
                connection.close()
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)[:240]})
            return

        with session.lock:
            session.captured[browser] = result
            complete = session.expected_browsers.issubset(session.captured.keys())
        self._send_json(HTTPStatus.OK, {
            "ok": True,
            "captureId": result["id"],
            "browser": browser,
            "tabs": result["tab_count"],
            "newResources": result.get("new_resource_count", 0),
            "pendingResources": result.get("candidate_resource_count", 0),
            "serverProof": protocol_proof(
                pairing["secret"],
                "accepted",
                browser,
                pairing["extension_id"],
                pairing["nonce"],
                request_id,
                result["id"],
            ),
        })
        if complete:
            session.done.set()

    def _mutation(self) -> None:
        session = self.server.session
        if session.mode != "mutate" or not session.mutation_plan:
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": "mutation is not active"})
            return
        try:
            raw_body = self._read_body(2 * 1024 * 1024)
            body = json.loads(raw_body.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            request_id = str(body.get("requestId") or "")
            targets_hash = str(body.get("targetsHash") or "")
            body_hash = hashlib.sha256(raw_body).hexdigest()
            pairing = self._authenticate_signed("mutation", request_id, targets_hash, body_hash)
            if not pairing or not pairing["enabled"]:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                return
            browser = normalize_browser(body.get("browser"))
            extension_id = str(body.get("extensionId") or "")
            if browser != pairing["browser"] or browser not in session.expected_browsers:
                raise ValueError("browser does not match mutation plan")
            if extension_id != pairing["extension_id"] or extension_id != EXPECTED_EXTENSION_ID:
                raise ValueError("extension does not match pairing")
            if request_id != str(session.mutation_plan["requestId"]):
                raise ValueError("mutation request does not match receiver run")
            browser_plan = session.mutation_plan["browsers"].get(browser)
            if not browser_plan or targets_hash != browser_plan["targetsHash"]:
                raise ValueError("mutation target set does not match receiver plan")
            results = body.get("results")
            if not isinstance(results, list) or len(results) != len(browser_plan["targets"]):
                raise ValueError("mutation result count does not match plan")
            expected_ids = {int(item["targetTabId"]) for item in browser_plan["targets"]}
            seen_ids: set[int] = set()
            normalized_results = []
            for item in results:
                if not isinstance(item, dict):
                    raise ValueError("mutation result must be an object")
                tab_id = int(item.get("tabId"))
                status = str(item.get("status") or "")
                reason = str(item.get("reason") or "")[:160]
                if tab_id not in expected_ids or tab_id in seen_ids:
                    raise ValueError("mutation result contains an unexpected tab")
                if status not in {"closed", "skipped"}:
                    raise ValueError("mutation result status is invalid")
                seen_ids.add(tab_id)
                normalized_results.append({"tabId": tab_id, "status": status, "reason": reason})
            if seen_ids != expected_ids:
                raise ValueError("mutation results are incomplete")
            control_tab_id = None
            control_window_id = None
            if session.mutation_plan.get("action") == "archive_captured_tabs":
                control_tab_id = int(body.get("controlTabId"))
                control_window_id = int(body.get("controlWindowId"))
                if control_tab_id < 0 or control_window_id < 0:
                    raise ValueError("archive control tab is invalid")
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)[:240]})
            return

        closed_count = sum(item["status"] == "closed" for item in normalized_results)
        skipped_count = len(normalized_results) - closed_count
        result = {
            "requestId": request_id,
            "targetsHash": targets_hash,
            "closedCount": closed_count,
            "skippedCount": skipped_count,
            "results": normalized_results,
        }
        if control_tab_id is not None and control_window_id is not None:
            result["controlTabId"] = control_tab_id
            result["controlWindowId"] = control_window_id
        with session.lock:
            session.mutated[browser] = result
            complete = session.expected_browsers.issubset(session.mutated.keys())
        self._send_json(HTTPStatus.OK, {
            "ok": True,
            "browser": browser,
            "closedCount": closed_count,
            "skippedCount": skipped_count,
            "serverProof": protocol_proof(
                pairing["secret"],
                "mutation-accepted",
                browser,
                pairing["extension_id"],
                pairing["nonce"],
                request_id,
                targets_hash,
                str(closed_count),
                str(skipped_count),
            ),
        })
        if complete:
            session.done.set()

    def _cleanup(self) -> None:
        session = self.server.session
        if session.mode != "cleanup" or not session.mutation_plan:
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": "cleanup is not active"})
            return
        try:
            raw_body = self._read_body(64 * 1024)
            body = json.loads(raw_body.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            request_id = str(body.get("requestId") or "")
            targets_hash = str(body.get("targetsHash") or "")
            body_hash = hashlib.sha256(raw_body).hexdigest()
            pairing = self._authenticate_signed("cleanup", request_id, targets_hash, body_hash)
            if not pairing or not pairing["enabled"]:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                return
            browser = normalize_browser(body.get("browser"))
            extension_id = str(body.get("extensionId") or "")
            control_tab_id = int(body.get("controlTabId"))
            control_window_id = int(body.get("controlWindowId"))
            status = str(body.get("status") or "")
            reason = str(body.get("reason") or "")[:160]
            if browser != pairing["browser"] or browser not in session.expected_browsers:
                raise ValueError("browser does not match cleanup plan")
            if extension_id != pairing["extension_id"] or extension_id != EXPECTED_EXTENSION_ID:
                raise ValueError("extension does not match pairing")
            if request_id != str(session.mutation_plan["requestId"]):
                raise ValueError("cleanup request does not match receiver run")
            browser_plan = session.mutation_plan["browsers"].get(browser)
            if not browser_plan or targets_hash != browser_plan["targetsHash"]:
                raise ValueError("cleanup target does not match receiver plan")
            expected = browser_plan["targets"]
            if len(expected) != 1:
                raise ValueError("cleanup plan must contain one control tab")
            if (
                control_tab_id != int(expected[0]["controlTabId"])
                or control_window_id != int(expected[0]["controlWindowId"])
            ):
                raise ValueError("cleanup control tab does not match plan")
            if status not in {"closed", "skipped"}:
                raise ValueError("cleanup result status is invalid")
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)[:240]})
            return

        result = {
            "requestId": request_id,
            "controlTabId": control_tab_id,
            "controlWindowId": control_window_id,
            "accepted": True,
            "status": status,
            "reason": reason,
        }
        with session.lock:
            session.cleaned[browser] = result
            complete = session.expected_browsers.issubset(session.cleaned.keys())
        self._send_json(HTTPStatus.OK, {
            "ok": True,
            "browser": browser,
            "serverProof": protocol_proof(
                pairing["secret"],
                "cleanup-accepted",
                browser,
                pairing["extension_id"],
                pairing["nonce"],
                request_id,
                targets_hash,
                str(control_tab_id),
                str(control_window_id),
                status,
                reason,
            ),
        })
        if complete:
            session.done.set()

    def _authenticate_signed(self, purpose: str, *extra_parts: str) -> dict[str, Any] | None:
        browser = normalize_browser(self.headers.get("X-TabAtlas-Browser"))
        extension_id = self.headers.get("X-TabAtlas-Extension", "")
        nonce = self.headers.get("X-TabAtlas-Nonce", "")
        supplied = self.headers.get("X-TabAtlas-Auth", "")
        protocol_header = self.headers.get("X-TabAtlas-Protocol", "").strip()
        if not protocol_header:
            protocol_version = LEGACY_CAPTURE_PROTOCOL_VERSION
        elif re.fullmatch(r"[1-9][0-9]{0,2}", protocol_header):
            protocol_version = int(protocol_header)
        else:
            return None
        if (
            browser not in {"chrome", "edge"}
            or extension_id != EXPECTED_EXTENSION_ID
            or not re.fullmatch(r"[a-f0-9]{32}", nonce)
            or not re.fullmatch(r"[a-f0-9]{64}", supplied)
        ):
            return None
        connection = connect(self.server.session.database_path)
        try:
            pairing = pairing_secret(connection, browser, extension_id)
        finally:
            connection.close()
        if not pairing:
            return None
        expected = protocol_proof(
            pairing["secret"],
            purpose,
            browser,
            extension_id,
            nonce,
            *extra_parts,
        )
        if not hmac.compare_digest(supplied, expected):
            return None
        return {**pairing, "nonce": nonce, "protocol_version": protocol_version}

    def _read_json(self, maximum: int) -> dict[str, Any]:
        value = json.loads(self._read_body(maximum).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def _read_body(self, maximum: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("invalid content length") from error
        if length <= 0 or length > maximum:
            raise ValueError("request body size is invalid")
        return self.rfile.read(length)

    def _request_allowed(self, require_origin: bool = False) -> bool:
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return not require_origin
        return origin in {
            f"chrome-extension://{EXPECTED_EXTENSION_ID}",
            f"extension://{EXPECTED_EXTENSION_ID}",
        }

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in {
            f"chrome-extension://{EXPECTED_EXTENSION_ID}",
            f"extension://{EXPECTED_EXTENSION_ID}",
        }:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send_json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


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
    thread = threading.Thread(target=server.serve_forever, name="tab-atlas-receiver", daemon=True)
    thread.start()
    try:
        if on_ready:
            on_ready()
        return session.done.wait(timeout_seconds)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
