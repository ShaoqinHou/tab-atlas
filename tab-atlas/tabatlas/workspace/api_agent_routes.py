from __future__ import annotations

import re
from http import HTTPStatus

from .agent_requests import create_workspace_chat_request
from .api_contract import ApiRequest
from .organization_batches import decide_organization_batch, defer_organization_batch
from .semantics import (
    apply_semantic_proposal,
    reject_semantic_proposal,
    undo_semantic_audit,
)
from .server_constants import OPAQUE_ID_PATTERN


def handle_agent_post(request: ApiRequest, path: str) -> bool:
    server = request.workspace_server
    if path == "/api/v1/agent-requests":
        document = request._read_json_body()
        with server.database() as connection:
            result = create_workspace_chat_request(
                connection,
                document.get("message"),
                document.get("resourceId") or None,
            )
        server.enqueue_agent_request(result["id"])
        request._send_json(result, HTTPStatus.ACCEPTED)
        return True
    match = re.fullmatch(
        rf"/api/v1/proposals/({OPAQUE_ID_PATTERN})/(accept|reject)", path
    )
    if match:
        with server.database() as connection:
            if match.group(2) == "accept":
                result = apply_semantic_proposal(
                    connection,
                    match.group(1),
                    "workspace_user",
                    request._idempotency_key(),
                )
            else:
                result = reject_semantic_proposal(
                    connection,
                    match.group(1),
                    "workspace_user",
                    request._idempotency_key(),
                )
        server.regenerate_report()
        request._send_json(result)
        return True
    match = re.fullmatch(
        rf"/api/v1/organization-batches/({OPAQUE_ID_PATTERN})/(accept|reject|defer)",
        path,
    )
    if match:
        request._read_empty_body()
        with server.database() as connection:
            if match.group(2) == "defer":
                result = defer_organization_batch(
                    connection,
                    match.group(1),
                    "workspace_user",
                    request._idempotency_key(),
                )
            else:
                result = decide_organization_batch(
                    connection,
                    match.group(1),
                    match.group(2),
                    "workspace_user",
                    request._idempotency_key(),
                )
        server.regenerate_report()
        request._send_json(result)
        return True
    match = re.fullmatch(rf"/api/v1/audits/({OPAQUE_ID_PATTERN})/undo", path)
    if match:
        with server.database() as connection:
            result = undo_semantic_audit(connection, match.group(1))
        server.regenerate_report()
        request._send_json(result)
        return True
    if path == "/api/v1/agent/handoff":
        request._read_empty_body()
        request._send_json(server.handoff_agent())
        return True
    if path == "/api/v1/agent/reclaim":
        request._read_empty_body()
        request._send_json(server.reclaim_agent())
        return True
    return False
