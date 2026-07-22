from __future__ import annotations

import re
from http import HTTPStatus

from ..catalog import inventory, set_discovery_state
from .api_contract import ApiRequest


def handle_sync_post(request: ApiRequest, path: str) -> bool:
    server = request.workspace_server
    if path == "/api/v1/browser-sync":
        document = request._read_json_body()
        raw_browsers = document.get("browsers", ["chrome", "edge"])
        if not isinstance(raw_browsers, list):
            raise ValueError("browsers must be a list")
        browsers = tuple(str(browser) for browser in raw_browsers)
        result = server.start_browser_sync(browsers, trigger="user")
        request._send_json(
            result,
            HTTPStatus.OK if result.get("reused") else HTTPStatus.ACCEPTED,
        )
        return True
    match = re.fullmatch(r"/api/v1/discoveries/(accept|dismiss)", path)
    if not match:
        return False
    document = request._read_json_body()
    resource_ids = document.get("resourceIds")
    if not isinstance(resource_ids, list) or not resource_ids:
        raise ValueError("resourceIds must be a non-empty list")
    target_state = "accepted" if match.group(1) == "accept" else "dismissed"
    with server.database() as connection:
        result = set_discovery_state(connection, target_state, resource_ids)
        result["inventory"] = inventory(connection)
    server.regenerate_report()
    request._send_json(result)
    return True
