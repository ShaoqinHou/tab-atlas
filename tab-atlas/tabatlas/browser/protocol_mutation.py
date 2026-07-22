from __future__ import annotations

import hashlib
import json
from http import HTTPStatus
from typing import Any

from ..common import normalize_browser
from ..database import protocol_proof
from .protocol_transport import EXPECTED_EXTENSION_ID


class MutationCapability:
    server: Any

    def _mutation(self) -> None:
        session = self.server.session
        if session.mode != "mutate" or not session.mutation_plan:
            self._send_json(
                HTTPStatus.CONFLICT, {"ok": False, "error": "mutation is not active"}
            )
            return
        try:
            raw_body = self._read_body(2 * 1024 * 1024)
            body = json.loads(raw_body.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            request_id = str(body.get("requestId") or "")
            targets_hash = str(body.get("targetsHash") or "")
            body_hash = hashlib.sha256(raw_body).hexdigest()
            pairing = self._authenticate_signed(
                "mutation", request_id, targets_hash, body_hash
            )
            if not pairing or not pairing["enabled"]:
                self._send_json(
                    HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"}
                )
                return
            browser = normalize_browser(body.get("browser"))
            extension_id = str(body.get("extensionId") or "")
            if (
                browser != pairing["browser"]
                or browser not in session.expected_browsers
            ):
                raise ValueError("browser does not match mutation plan")
            if (
                extension_id != pairing["extension_id"]
                or extension_id != EXPECTED_EXTENSION_ID
            ):
                raise ValueError("extension does not match pairing")
            if request_id != str(session.mutation_plan["requestId"]):
                raise ValueError("mutation request does not match receiver run")
            browser_plan = session.mutation_plan["browsers"].get(browser)
            if not browser_plan or targets_hash != browser_plan["targetsHash"]:
                raise ValueError("mutation target set does not match receiver plan")
            results = body.get("results")
            if not isinstance(results, list) or len(results) != len(
                browser_plan["targets"]
            ):
                raise ValueError("mutation result count does not match plan")
            expected_ids = {
                int(item["targetTabId"]) for item in browser_plan["targets"]
            }
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
                normalized_results.append(
                    {"tabId": tab_id, "status": status, "reason": reason}
                )
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
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)[:240]}
            )
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
        self._send_json(
            HTTPStatus.OK,
            {
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
            },
        )
        if complete:
            session.done.set()
