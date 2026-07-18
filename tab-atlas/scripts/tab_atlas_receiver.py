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
    mark_pairing_seen,
    normalize_browser,
    pairing_secret,
    protocol_proof,
    save_pairing,
    store_snapshot,
    token_hash,
)


HOST = "127.0.0.1"
PORT = 9786
MAX_BODY_BYTES = 10 * 1024 * 1024
EXPECTED_EXTENSION_ID = "ohgpplkophdikjnbefigdhikdooehmkh"
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
            "x-tabatlas-extension, x-tabatlas-nonce",
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
        if self.server.session.mode != "capture":
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": "capture is not active"})
            return
        connection = connect(self.server.session.database_path)
        try:
            mark_pairing_seen(connection, browser)
        finally:
            connection.close()
        with self.server.session.lock:
            if browser not in self.server.session.expected_browsers or browser in self.server.session.captured:
                action = "idle"
                request_id = ""
            else:
                action = "capture"
                request_id = self.server.session.request_id
        payload = {
            "ok": True,
            "action": action,
            "requestId": request_id,
            "serverProof": protocol_proof(
                pairing["secret"],
                "response",
                browser,
                pairing["extension_id"],
                pairing["nonce"],
                request_id,
                action,
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
                mark_pairing_seen(connection, browser)
                result = store_snapshot(connection, session.state_dir, body, "extension_live")
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

    def _authenticate_signed(self, purpose: str, *extra_parts: str) -> dict[str, Any] | None:
        browser = normalize_browser(self.headers.get("X-TabAtlas-Browser"))
        extension_id = self.headers.get("X-TabAtlas-Extension", "")
        nonce = self.headers.get("X-TabAtlas-Nonce", "")
        supplied = self.headers.get("X-TabAtlas-Auth", "")
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
        return {**pairing, "nonce": nonce}

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
