from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from ..database import utc_now
from .constants import MAX_NOTE_BYTES


def _insert_agent_request(
    connection: sqlite3.Connection,
    kind: str,
    *,
    resource_id: str | None,
    note_id: str | None,
    request_text: str | None,
) -> str:
    request_id = _new_id("agent")
    connection.execute(
        """
        INSERT INTO agent_requests(
          id, kind, resource_id, note_id, request_text, status, created_at
        ) VALUES(?, ?, ?, ?, ?, 'queued', ?)
        """,
        (request_id, kind, resource_id, note_id, request_text, utc_now()),
    )
    return request_id


def _require_resource(connection: sqlite3.Connection, resource_id: str) -> None:
    if not re.fullmatch(r"res_[a-f0-9]{24}", str(resource_id or "")):
        raise ValueError("Resource ID is invalid")
    row = connection.execute(
        "SELECT 1 FROM resources WHERE id=? AND library_state='accepted'",
        (resource_id,),
    ).fetchone()
    if not row:
        raise ValueError("Accepted resource was not found")


def _validate_note_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Note text must be a string")
    if "\x00" in value:
        raise ValueError("Note text contains a NUL character")
    encoded = value.encode("utf-8")
    if not value.strip() or len(encoded) > MAX_NOTE_BYTES:
        raise ValueError("Note text must be between 1 and 32768 UTF-8 bytes")
    return value


def _validate_idempotency_key(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{12,160}", text):
        raise ValueError("Idempotency key is invalid")
    return text


def _collection_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 100 or any(ord(character) < 32 for character in name):
        raise ValueError("Collection name is invalid")
    return name


def _optional_collection_name(value: Any) -> str:
    if value in {None, ""}:
        return ""
    return _collection_name(value)


def _bounded_text(value: Any, limit: int, default: str) -> str:
    text = str(value or "").strip()
    return (text or default)[:limit]


def _bounded_label(value: Any, limit: int, default: str) -> str:
    text = str(value or "").strip()
    text = "".join(character for character in text if ord(character) >= 32)
    return (text or default)[:limit]


def _bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(maximum, max(minimum, number))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_or_none(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return None


def _atomic_private_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _count_values(values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[str(value)] = result.get(str(value), 0) + 1
    return result


class ConflictError(ValueError):
    pass
