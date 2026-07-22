from __future__ import annotations

from pathlib import Path
from typing import Any

from ..database import open_connection
from ..previews import cache_public_previews


def prepare_visual_review(
    database_path: Path,
    state_dir: Path,
    *,
    workers: int = 8,
) -> dict[str, Any]:
    """Cache only allowlisted public media needed to review current candidates."""
    connection = open_connection(database_path)
    try:
        result = cache_public_previews(
            connection,
            state_dir,
            refresh=False,
            workers=workers,
        )
    finally:
        connection.close()
    failed = int(result.get("failed") or 0)
    deferred = int(result.get("deferred") or 0)
    return {
        "state": "partial" if failed or deferred else "complete",
        "eligible": int(result.get("eligible") or 0),
        "alreadyCached": int(result.get("alreadyCached") or 0),
        "prepared": int(result.get("downloaded") or 0),
        "motionPrepared": int(result.get("motionResolved") or 0),
        "failed": failed,
        "deferred": deferred,
    }
