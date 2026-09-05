from __future__ import annotations

import hashlib
import json
from http import HTTPStatus
from typing import Any

from ..common import normalize_browser
from ..database import protocol_proof
from .protocol_transport import EXPECTED_EXTENSION_ID


class ArchiveCapability:
    server: Any

    def _cleanup(self) -> None:
        session = self.server.session
        if session.mode != "cleanup" or not session.mutation_plan:
            self._send_json(
                HTTPStatus.CONFLICT, {"ok": False, "error": "cleanup is not active"}
            )
            return
        try:
            raw_body = self._read_body(64 * 1024)
            body = json.loads(raw_body.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            request_id = str(body.get("requestId") or "")
            targets_hash = str(body.get("targetsHash") or "")
            body_hash = hashlib.sha256(raw_body).hexdigest()
            pairing = self._authenticate_signed(
                "cleanup", request_id, targets_hash, body_hash
            )
            if not pairing or not pairing["enabled"]:
                self._send_json(
                    HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"}
                )
                return
            browser = normalize_browser(body.get("browser"))
            extension_id = str(body.get("extensionId") or "")
            control_tab_id = int(body.get("controlTabId"))
            control_window_id = int(body.get("controlWindowId"))
            status = str(body.get("status") or "")
            reason = str(body.get("reason") or "")[:160]
            if (
                browser != pairing["browser"]
                or browser not in session.expected_browsers
            ):
                raise ValueError("browser does not match cleanup plan")
            if (
                extension_id != pairing["extension_id"]
                or extension_id != EXPECTED_EXTENSION_ID
            ):
                raise ValueError("extension does not match pairing")
            if request_id != str(session.mutation_plan["requestId"]):
                raise ValueError("cleanup request does not match receiver run")
            browser_plan = session.mutation_plan["browsers"].get(browser)
            if not browser_plan or targets_hash != browser_plan["targetsHash"]:
                raise ValueError("cleanup target does not match receiver plan")
            expected = browser_plan["targets"]
            if len(expected) != 1:
                raise ValueError("cleanup plan must contain one control tab")
            if control_tab_id != int(
                expected[0]["controlTabId"]
            ) or control_window_id != int(expected[0]["controlWindowId"]):
                raise ValueError("cleanup control tab does not match plan")
            if status not in {"closed", "skipped"}:
                raise ValueError("cleanup result status is invalid")
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)[:240]}
            )
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
        self._send_json(
            HTTPStatus.OK,
            {
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
            },
        )
        if complete:
            session.done.set()
