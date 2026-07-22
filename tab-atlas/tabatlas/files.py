from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .capture import normalize_snapshot_document, store_snapshot


def import_file(
    connection: sqlite3.Connection,
    state_dir: Path,
    path: Path,
    source: str,
) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        document = json.load(handle)
    return [
        store_snapshot(connection, state_dir, snapshot, source)
        for snapshot in normalize_snapshot_document(document)
    ]


def copy_extension(source_dir: Path, destination_dir: Path) -> Path:
    if destination_dir.exists():
        shutil.rmtree(destination_dir)
    shutil.copytree(source_dir, destination_dir)
    return destination_dir
