from __future__ import annotations

import json
from pathlib import Path

from ..database import utc_now


def _standing_approval_path(state_dir: Path) -> Path:
    return state_dir / "approvals" / "exact-duplicate.json"


def _load_standing_approval(state_dir: Path) -> str:
    path = _standing_approval_path(state_dir)
    if not path.is_file():
        return ""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != 1
        or document.get("action") != "close_exact_duplicates"
        or document.get("policyVersion") != 1
        or document.get("active") is not True
    ):
        return ""
    return str(document.get("scope") or "").strip()[:1000]


def _write_standing_approval(state_dir: Path, scope: str, active: bool) -> Path:
    path = _standing_approval_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schemaVersion": 1,
        "action": "close_exact_duplicates",
        "policyVersion": 1,
        "active": bool(active),
        "scope": scope.strip()[:1000],
        "recordedAt": utc_now(),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _archive_approval_path(state_dir: Path) -> Path:
    return state_dir / "approvals" / "archive-captured-tabs.json"


def _load_archive_approval(state_dir: Path) -> str:
    path = _archive_approval_path(state_dir)
    if not path.is_file():
        return ""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != 1
        or document.get("action") != "archive_captured_tabs"
        or document.get("policyVersion") != 1
        or document.get("active") is not True
    ):
        return ""
    return str(document.get("scope") or "").strip()[:1000]


def _write_archive_approval(state_dir: Path, scope: str, active: bool) -> Path:
    path = _archive_approval_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schemaVersion": 1,
        "action": "archive_captured_tabs",
        "policyVersion": 1,
        "active": bool(active),
        "scope": scope.strip()[:1000],
        "recordedAt": utc_now(),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _bounded_timeout(value: int) -> int:
    return max(5, min(600, value))


def _require_inside(path: Path, parent: Path) -> None:
    try:
        path.relative_to(parent.resolve())
    except ValueError as error:
        raise ValueError(
            f"Destination must stay under the state directory: {parent}"
        ) from error
