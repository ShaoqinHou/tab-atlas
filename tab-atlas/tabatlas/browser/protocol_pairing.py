from __future__ import annotations

import hmac
import re
import secrets
from http import HTTPStatus
from typing import Any

from ..common import normalize_browser
from ..database import connect, protocol_proof, save_pairing, token_hash
from .protocol_transport import EXPECTED_EXTENSION_ID


class PairingCapability:
    server: Any

    def _pair(self) -> None:
        session = self.server.session
        if session.mode != "pair" or not session.pairing_code:
            self._send_json(
                HTTPStatus.CONFLICT, {"ok": False, "error": "pairing is not active"}
            )
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
            self._send_json(
                HTTPStatus.FORBIDDEN, {"ok": False, "error": "wrong browser"}
            )
            return
        if extension_id != EXPECTED_EXTENSION_ID:
            self._send_json(
                HTTPStatus.FORBIDDEN, {"ok": False, "error": "wrong extension"}
            )
            return
        if not re.fullmatch(r"[a-f0-9]{32}", nonce) or not re.fullmatch(
            r"[a-f0-9]{64}", client_proof
        ):
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid pairing proof"}
            )
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
            self._send_json(
                HTTPStatus.FORBIDDEN, {"ok": False, "error": "invalid pairing proof"}
            )
            return

        token = secrets.token_urlsafe(32)
        issued_key = token_hash(token)
        connection = connect(session.database_path)
        try:
            save_pairing(connection, browser, extension_id, token)
        finally:
            connection.close()
        session.paired_browser = browser
        self._send_json(
            HTTPStatus.OK,
            {
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
            },
        )
        session.done.set()
