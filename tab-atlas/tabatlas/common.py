from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


def normalize_browser(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"chrome", "google chrome"}:
        return "chrome"
    if text in {"edge", "microsoft edge", "msedge"}:
        return "edge"
    return "chromium"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_inside_path(path: Path, parent: Path) -> None:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as error:
        raise ValueError(
            f"Evidence path escapes the private state directory: {path}"
        ) from error


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]
