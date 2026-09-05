from __future__ import annotations

import hashlib
import json
from http import HTTPStatus
from typing import Any

from ..capture import store_snapshot
from ..common import normalize_browser
from ..database import connect, mark_pairing_seen, protocol_proof
from .protocol_transport import EXPECTED_EXTENSION_ID, MAX_BODY_BYTES


class CaptureCapability:
    server: Any

    def _snapshot(self) -> None:
        session = self.server.session
        if session.mode != "capture":
            self._send_json(
                HTTPStatus.CONFLICT, {"ok": False, "error": "capture is not active"}
            )
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
                self._send_json(
                    HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"}
                )
                return
            browser = normalize_browser(body.get("browser"))
            extension_id = str(body.get("extensionId") or "")
            if browser != pairing["browser"]:
                raise ValueError("browser does not match pairing")
            if (
                extension_id != pairing["extension_id"]
                or extension_id != EXPECTED_EXTENSION_ID
            ):
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
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)[:240]}
            )
            return

        with session.lock:
            session.captured[browser] = result
            complete = session.expected_browsers.issubset(session.captured.keys())
        self._send_json(
            HTTPStatus.OK,
            {
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
            },
        )
        if complete:
            session.done.set()
