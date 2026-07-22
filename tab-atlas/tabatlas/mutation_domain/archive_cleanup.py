from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from ..common import _atomic_write
from ..constants import TABATLAS_EXTENSION_URL_PREFIX
from ..database import utc_now


def build_archive_cleanup_plan(
    archive_plan: dict[str, Any],
    controls: dict[str, dict[str, int]],
) -> dict[str, Any]:
    request_id = secrets.token_hex(12)
    control_url = f"{TABATLAS_EXTENSION_URL_PREFIX}archive_complete.html"
    expected_url_hash = hashlib.sha256(control_url.encode("utf-8")).hexdigest()
    browsers: dict[str, dict[str, Any]] = {}
    for browser, control in sorted(controls.items()):
        targets = [
            {
                "controlTabId": int(control["controlTabId"]),
                "controlWindowId": int(control["controlWindowId"]),
                "expectedUrlHash": expected_url_hash,
            }
        ]
        payload = json.dumps(targets, separators=(",", ":"), ensure_ascii=True)
        browsers[browser] = {
            "targets": targets,
            "targetsHash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }
    plan = {
        "schemaVersion": 1,
        "action": "close_archive_control",
        "requestId": request_id,
        "archiveRequestId": archive_plan["requestId"],
        "createdAt": utc_now(),
        "browsers": browsers,
    }
    serialized = json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    plan["planHash"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return plan


def record_archive_cleanup_result(
    evidence_path: Path,
    cleanup_plan: dict[str, Any],
    complete: bool,
    results: dict[str, dict[str, Any]],
) -> None:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["controlCleanup"] = {
        "requestId": cleanup_plan.get("requestId"),
        "planHash": cleanup_plan.get("planHash"),
        "complete": bool(complete),
        "browsers": {
            browser: {
                "accepted": bool(result.get("accepted")),
                "controlTabId": result.get("controlTabId"),
                "controlWindowId": result.get("controlWindowId"),
                "status": result.get("status"),
                "reason": str(result.get("reason") or "")[:160],
            }
            for browser, result in results.items()
        },
        "completedAt": utc_now(),
    }
    _atomic_write(
        evidence_path,
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        ),
    )
