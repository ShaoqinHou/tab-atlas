from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from .catalog import discovery_resources, inventory, library_resources
from .common import _atomic_write
from .constants import SPACE_DEFINITIONS
from .database import utc_now
from .resource_view import (
    report_collection_summaries,
    report_group_summaries,
    report_source_summaries,
    resource_presentation,
)


# Classic scripts are intentional: Chromium blocks ES-module imports from file://.
# This tuple is the dependency graph and load order for both offline and HTTP reports.
REPORT_SCRIPT_PATHS = (
    # Shared state owner and stable DOM shell.
    "modules/state.js",
    "modules/shell.js",
    # Cross-feature primitives and optional HTTP workspace transport.
    "modules/utilities.js",
    "modules/workspace.js",
    # Catalog selectors and reusable navigation controls.
    "modules/catalog-selection.js",
    "modules/directory-controls.js",
    # Resource presentation and inspector features.
    "modules/preview.js",
    "modules/resource-controls.js",
    "modules/gallery.js",
    "modules/details.js",
    "modules/notes.js",
    "modules/action-progress.js",
    "modules/resource-workspace.js",
    "modules/voice-notes.js",
    "modules/drawer.js",
    "modules/actions.js",
    # Navigation and one renderer per report area.
    "modules/navigation.js",
    "modules/home-view.js",
    "modules/library-view.js",
    "modules/review-view.js",
    "modules/search-view.js",
    "modules/views.js",
    # Optional live-workspace features, followed by the only entry point.
    "modules/sync.js",
    "modules/agent.js",
    "app.js",
)


def report_payload(connection: sqlite3.Connection) -> dict[str, Any]:
    """Build one consistent, versioned catalog snapshot for file and HTTP clients."""
    savepoint = "tabatlas_catalog_snapshot"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        document = _report_payload(connection)
    except Exception:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
        raise
    connection.execute(f"RELEASE {savepoint}")
    return document


def _report_payload(connection: sqlite3.Connection) -> dict[str, Any]:
    raw_resources = library_resources(connection)
    resources = []
    for item in raw_resources:
        resource = dict(item)
        resource["presentation"] = resource_presentation(resource)
        resources.append(resource)
    discoveries = []
    for item in discovery_resources(connection):
        resource = dict(item)
        resource["presentation"] = resource_presentation(resource)
        discoveries.append(resource)
    dismissed = []
    for item in library_resources(connection, {"dismissed"}):
        resource = dict(item)
        resource["presentation"] = resource_presentation(resource)
        dismissed.append(resource)
    collections = [
        dict(row)
        for row in connection.execute(
            """
            SELECT c.*, parent.name AS parentName
            FROM collections c
            LEFT JOIN collections parent ON parent.id=c.parent_id
            ORDER BY c.name
            """
        )
    ]
    tasks = [
        dict(row)
        for row in connection.execute("SELECT * FROM tasks ORDER BY status, created_at")
    ]
    groups = report_group_summaries(resources)
    collection_summaries = report_collection_summaries(resources, collections)
    active_summaries = [item for item in collection_summaries if item["resourceCount"]]
    action_list_summaries = []
    for item in active_summaries:
        if item["kind"] != "action_list":
            continue
        states = Counter(
            row["state"]
            for row in connection.execute(
                "SELECT state FROM resource_collection_progress WHERE collection_id=?",
                (item["id"],),
            )
        )
        action_list_summaries.append({**item, "stateCounts": dict(states)})
    space_order = {name: index for index, name in enumerate(SPACE_DEFINITIONS)}
    space_summaries = sorted(
        (item for item in active_summaries if item["kind"] == "space"),
        key=lambda item: (space_order.get(item["name"], 999), item["name"].casefold()),
    )
    source_summaries = report_source_summaries(resources)
    format_counts = Counter(item["presentation"]["format"] for item in resources)
    intent_counts = Counter(item["presentation"]["intent"] for item in resources)
    queue_counts = Counter(item["presentation"]["queue"] for item in resources)
    document = {
        "contractVersion": 1,
        "generatedAt": utc_now(),
        "inventory": inventory(connection),
        "resources": resources,
        "discoveries": discoveries,
        "dismissed": dismissed,
        "groups": groups,
        "collections": collections,
        "collectionSummaries": active_summaries,
        "spaceSummaries": space_summaries,
        "topicSummaries": [
            item for item in active_summaries if item["kind"] == "topic"
        ],
        "focusSummaries": [
            item for item in active_summaries if item["kind"] == "focus"
        ],
        "projectSummaries": [
            item for item in active_summaries if item["kind"] == "project"
        ],
        "actionListSummaries": action_list_summaries,
        "sourceSummaries": source_summaries,
        "facets": {
            "formats": [
                {"name": name, "count": count}
                for name, count in format_counts.most_common()
            ],
            "intents": [
                {"name": name, "count": count}
                for name, count in intent_counts.most_common()
            ],
            "queues": dict(queue_counts),
        },
        "tasks": tasks,
        "workspace": {
            "pendingAgentRequests": connection.execute(
                "SELECT COUNT(*) AS count FROM agent_requests WHERE status IN ('queued', 'running')"
            ).fetchone()["count"],
            "recentAudits": [
                {
                    "id": row["id"],
                    "resourceId": row["resource_id"],
                    "createdAt": row["created_at"],
                    "undoneAt": row["undone_at"] or "",
                }
                for row in connection.execute(
                    """
                    SELECT id, resource_id, created_at, undone_at
                    FROM semantic_audits ORDER BY created_at DESC, rowid DESC LIMIT 20
                    """
                )
            ],
        },
    }
    refresh_catalog_revision(document)
    return document


def refresh_catalog_revision(document: dict[str, Any]) -> str:
    revision_input = {
        key: value
        for key, value in document.items()
        if key not in {"generatedAt", "revision"}
    }
    revision = hashlib.sha256(
        json.dumps(
            revision_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    document["revision"] = revision
    return revision


def _report_scripts(assets_dir: Path) -> tuple[str, ...]:
    # Minimal test/embedded clients may provide one self-contained app.js.
    if not (assets_dir / "modules").is_dir():
        return tuple(
            path for path in REPORT_SCRIPT_PATHS if (assets_dir / path).is_file()
        )
    missing = [
        path for path in REPORT_SCRIPT_PATHS if not (assets_dir / path).is_file()
    ]
    if missing:
        raise FileNotFoundError(f"Incomplete report script graph: {', '.join(missing)}")
    return REPORT_SCRIPT_PATHS


def generate_report(
    connection: sqlite3.Connection,
    report_dir: Path,
    assets_dir: Path,
    state_dir: Path | None = None,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    available_previews = _copy_report_previews(
        connection,
        state_dir or _database_parent(connection),
        report_dir,
    )
    document = report_payload(connection)
    for resource in [*document["resources"], *document["discoveries"]]:
        if resource["resourceId"] not in available_previews:
            resource["presentation"]["preview"]["localImage"] = ""
    document["sourceSummaries"] = report_source_summaries(document["resources"])
    refresh_catalog_revision(document)
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    payload = (
        payload.replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    script_tags = "\n".join(
        f'  <script src="{path}"></script>' for path in _report_scripts(assets_dir)
    )
    template = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>TabAtlas</title>
  <link rel=\"stylesheet\" href=\"app.css\">
</head>
<body>
  <div id=\"app\"></div>
  <script>window.__TAB_ATLAS__=__PAYLOAD__;</script>
__REPORT_SCRIPTS__
</body>
</html>
""".replace("__PAYLOAD__", payload).replace("__REPORT_SCRIPTS__", script_tags)
    _atomic_write(report_dir / "index.html", template.encode("utf-8"))
    _atomic_write(report_dir / "app.css", (assets_dir / "app.css").read_bytes())
    _atomic_write(report_dir / "app.js", (assets_dir / "app.js").read_bytes())
    for directory in ("modules", "styles"):
        source = assets_dir / directory
        if source.is_dir():
            _publish_asset_tree(source, report_dir / directory)
    return report_dir / "index.html"


def _database_parent(connection: sqlite3.Connection) -> Path:
    for row in connection.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return Path(row[2]).resolve().parent
    return Path.cwd()


def _copy_report_previews(
    connection: sqlite3.Connection,
    state_dir: Path,
    report_dir: Path,
) -> set[str]:
    media_dir = report_dir / "media"
    staging = report_dir / f".media-staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    state_root = state_dir.resolve()
    copied: set[str] = set()
    for row in connection.execute(
        "SELECT resource_id, local_path FROM resource_previews ORDER BY resource_id"
    ):
        source = (state_root / row["local_path"]).resolve()
        try:
            source.relative_to(state_root)
        except ValueError:
            continue
        suffix = source.suffix.casefold()
        if not source.is_file() or suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        destination = staging / f"{row['resource_id']}{suffix}"
        shutil.copy2(source, destination)
        copied.add(row["resource_id"])
    _replace_directory(staging, media_dir)
    return copied


def _publish_asset_tree(source: Path, destination: Path) -> None:
    staging = destination.parent / f".{destination.name}-staging-{uuid.uuid4().hex}"
    shutil.copytree(source, staging)
    _replace_directory(staging, destination)


def _replace_directory(staging: Path, destination: Path) -> None:
    backup = destination.parent / f".{destination.name}-old-{uuid.uuid4().hex}"
    moved_old = False
    try:
        if destination.exists():
            destination.replace(backup)
            moved_old = True
        staging.replace(destination)
    except Exception:
        if moved_old and not destination.exists() and backup.exists():
            backup.replace(destination)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists():
            shutil.rmtree(backup)
