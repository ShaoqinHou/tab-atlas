from __future__ import annotations

from http import HTTPStatus
from typing import Any

from ..constants import TARGET_HASH_PROTOCOL_VERSION
from ..database import connect, mark_pairing_seen, protocol_proof


class CommandCapability:
    server: Any

    def _command(self) -> None:
        pairing = self._authenticate_signed("command")
        if not pairing:
            self._send_json(
                HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"}
            )
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
            if (
                self.server.session.mode == "revoke"
                and browser in self.server.session.expected_browsers
            ):
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
                if (
                    browser not in session.expected_browsers
                    or browser in session.captured
                ):
                    action = "idle"
                    request_id = ""
                else:
                    action = "capture"
                    request_id = session.request_id
            elif session.mode == "mutate" and session.mutation_plan:
                browser_plan = session.mutation_plan.get("browsers", {}).get(browser)
                if (
                    browser not in session.expected_browsers
                    or browser in session.mutated
                    or not browser_plan
                ):
                    action = "idle"
                    request_id = ""
                else:
                    action = str(
                        session.mutation_plan.get("action") or "close_exact_duplicates"
                    )
                    request_id = str(session.mutation_plan["requestId"])
                    targets = browser_plan["targets"]
                    targets_hash = browser_plan["targetsHash"]
            elif session.mode == "cleanup" and session.mutation_plan:
                browser_plan = session.mutation_plan.get("browsers", {}).get(browser)
                if (
                    browser not in session.expected_browsers
                    or browser in session.cleaned
                    or not browser_plan
                ):
                    action = "idle"
                    request_id = ""
                else:
                    action = "close_archive_control"
                    request_id = str(session.mutation_plan["requestId"])
                    targets = browser_plan["targets"]
                    targets_hash = browser_plan["targetsHash"]
            else:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {"ok": False, "error": "receiver is not active"},
                )
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
