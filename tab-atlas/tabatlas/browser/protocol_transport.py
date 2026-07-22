from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..common import normalize_browser
from ..constants import (
    LEGACY_CAPTURE_PROTOCOL_VERSION,
    TABATLAS_EXTENSION_ID,
)
from ..database import connect, pairing_secret, protocol_proof


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


class ProtocolTransportHandler(BaseHTTPRequestHandler):
    server: TabAtlasHTTPServer

    def do_OPTIONS(self) -> None:
        if not self._request_allowed(require_origin=True):
            self._send_json(
                HTTPStatus.FORBIDDEN, {"ok": False, "error": "origin denied"}
            )
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
            self._send_json(
                HTTPStatus.FORBIDDEN, {"ok": False, "error": "request denied"}
            )
            return
        path = urlsplit(self.path).path
        if path != "/v1/command":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        self._command()

    def do_POST(self) -> None:
        if not self._request_allowed():
            self._send_json(
                HTTPStatus.FORBIDDEN, {"ok": False, "error": "request denied"}
            )
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

    def _authenticate_signed(
        self, purpose: str, *extra_parts: str
    ) -> dict[str, Any] | None:
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

    def _command(self) -> None:
        raise NotImplementedError

    def _pair(self) -> None:
        raise NotImplementedError

    def _snapshot(self) -> None:
        raise NotImplementedError

    def _mutation(self) -> None:
        raise NotImplementedError

    def _cleanup(self) -> None:
        raise NotImplementedError
