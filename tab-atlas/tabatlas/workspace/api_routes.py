from __future__ import annotations

import json
from http import HTTPStatus

from .agent import AgentUnavailable
from .api_agent_routes import handle_agent_post
from .api_contract import ApiRequest
from .api_read_routes import handle_read_get
from .api_resource_routes import handle_resource_post
from .api_sync_routes import handle_sync_post
from .context import _safe_error_code
from .support import ConflictError


def handle_get(request: ApiRequest, path: str) -> bool:
    return handle_read_get(request, path)


def handle_post(request: ApiRequest, path: str) -> None:
    try:
        if handle_sync_post(request, path):
            return
        if handle_resource_post(request, path):
            return
        if handle_agent_post(request, path):
            return
        request.send_error(HTTPStatus.NOT_FOUND)
    except ConflictError as error:
        request._send_json(
            {"error": str(error), "code": "conflict"}, HTTPStatus.CONFLICT
        )
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        request._send_json(
            {"error": str(error), "code": "invalid_request"},
            HTTPStatus.BAD_REQUEST,
        )
    except AgentUnavailable as error:
        request._send_json(
            {
                "error": "Codex is unavailable; saved work remains local.",
                "code": _safe_error_code(error),
            },
            HTTPStatus.SERVICE_UNAVAILABLE,
        )
