from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import _atomic_write, normalize_browser
from .constants import (
    LEGACY_CAPTURE_PROTOCOL_VERSION,
    SCHEMA_VERSION,
    TOPIC_PARENT_DEFAULTS,
)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pairings (
  browser TEXT PRIMARY KEY CHECK (browser IN ('chrome', 'edge')),
  extension_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1,
  protocol_version INTEGER NOT NULL DEFAULT 2,
  created_at TEXT NOT NULL,
  last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS captures (
  id TEXT PRIMARY KEY,
  browser TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  source TEXT NOT NULL,
  trust_state TEXT NOT NULL DEFAULT 'trusted' CHECK (trust_state IN ('trusted', 'candidate')),
  raw_path TEXT NOT NULL,
  request_id TEXT,
  extension_id TEXT,
  window_count INTEGER NOT NULL,
  group_count INTEGER NOT NULL,
  tab_count INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS captures_browser_time
ON captures(browser, captured_at DESC, received_at DESC);

CREATE INDEX IF NOT EXISTS captures_browser_received
ON captures(browser, received_at DESC);

CREATE TABLE IF NOT EXISTS resources (
  id TEXT PRIMARY KEY,
  canonical_url TEXT NOT NULL UNIQUE,
  host TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  brief TEXT,
  detail TEXT,
  why_kept TEXT,
  next_action TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  confidence REAL,
  archived_at TEXT,
  library_state TEXT NOT NULL DEFAULT 'accepted'
    CHECK (library_state IN ('accepted', 'candidate', 'dismissed')),
  accepted_at TEXT,
  dismissed_at TEXT,
  semantic_revision INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tab_instances (
  id TEXT PRIMARY KEY,
  capture_id TEXT NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
  resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  browser TEXT NOT NULL,
  window_id TEXT,
  window_focused INTEGER NOT NULL DEFAULT 0,
  tab_id TEXT,
  position INTEGER,
  active INTEGER NOT NULL DEFAULT 0,
  highlighted INTEGER NOT NULL DEFAULT 0,
  pinned INTEGER NOT NULL DEFAULT 0,
  audible INTEGER NOT NULL DEFAULT 0,
  discarded INTEGER NOT NULL DEFAULT 0,
  group_id TEXT,
  group_title TEXT,
  group_color TEXT,
  group_collapsed INTEGER NOT NULL DEFAULT 0,
  title TEXT,
  favicon_url TEXT,
  exact_url TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS tab_instances_capture ON tab_instances(capture_id);
CREATE INDEX IF NOT EXISTS tab_instances_resource ON tab_instances(resource_id);

CREATE TABLE IF NOT EXISTS collections (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  kind TEXT NOT NULL DEFAULT 'theme',
  description TEXT,
  objective TEXT,
  parent_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'active',
  workflow_kind TEXT NOT NULL DEFAULT 'none',
  config_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_collections (
  resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'reference',
  reason TEXT,
  confidence REAL,
  origin TEXT NOT NULL DEFAULT 'codex',
  accepted INTEGER NOT NULL DEFAULT 0,
  authority TEXT NOT NULL DEFAULT 'legacy_effective',
  applied_decision_id TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (resource_id, collection_id)
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  resource_id TEXT REFERENCES resources(id) ON DELETE CASCADE,
  collection_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  notes TEXT,
  due_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_previews (
  resource_id TEXT PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('thumbnail', 'screenshot')),
  local_path TEXT NOT NULL,
  source TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  width INTEGER,
  height INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_preview_attempts (
  resource_id TEXT PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
  attempted_at TEXT NOT NULL,
  retry_after TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_motion_previews (
  resource_id TEXT PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('none', 'x_mp4')),
  remote_url TEXT,
  source TEXT NOT NULL,
  refreshed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_notes (
  id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('text', 'audio')),
  text_body TEXT,
  audio_relpath TEXT,
  audio_mime TEXT,
  audio_sha256 TEXT,
  audio_bytes INTEGER,
  audio_duration_ms INTEGER,
  language_hint TEXT,
  source_surface TEXT NOT NULL DEFAULT 'workspace',
  supersedes_note_id TEXT REFERENCES resource_notes(id) ON DELETE SET NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  CHECK (
    (kind='text' AND text_body IS NOT NULL AND audio_relpath IS NULL)
    OR
    (kind='audio' AND text_body IS NULL AND audio_relpath IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS resource_notes_resource_time
ON resource_notes(resource_id, created_at DESC);

CREATE TABLE IF NOT EXISTS note_events (
  id TEXT PRIMARY KEY,
  note_id TEXT NOT NULL REFERENCES resource_notes(id) ON DELETE CASCADE,
  event TEXT NOT NULL CHECK (event IN ('retract', 'restore')),
  actor TEXT NOT NULL,
  request_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_processing_events (
  id TEXT PRIMARY KEY,
  note_id TEXT NOT NULL REFERENCES resource_notes(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL,
  stage TEXT NOT NULL CHECK (stage IN ('transcription', 'interpretation')),
  state TEXT NOT NULL CHECK (
    state IN ('queued', 'running', 'succeeded', 'failed', 'not_required', 'stale')
  ),
  sequence INTEGER NOT NULL,
  input_hash TEXT NOT NULL,
  output_text TEXT,
  output_json TEXT,
  engine TEXT,
  model TEXT,
  prompt_version TEXT,
  error_code TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(note_id, run_id, stage, sequence)
);

CREATE INDEX IF NOT EXISTS note_processing_latest
ON note_processing_events(note_id, stage, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_requests (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('interpret_note', 'workspace_chat')),
  resource_id TEXT REFERENCES resources(id) ON DELETE CASCADE,
  note_id TEXT REFERENCES resource_notes(id) ON DELETE CASCADE,
  request_text TEXT,
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
  response_json TEXT,
  error_code TEXT,
  thread_id TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS semantic_proposals (
  id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  note_id TEXT REFERENCES resource_notes(id) ON DELETE SET NULL,
  request_id TEXT REFERENCES agent_requests(id) ON DELETE SET NULL,
  base_revision INTEGER NOT NULL,
  proposer TEXT NOT NULL,
  model TEXT,
  thread_id TEXT,
  rationale TEXT,
  operations_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  input_hash TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT
);

CREATE TABLE IF NOT EXISTS semantic_audits (
  id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  proposal_id TEXT REFERENCES semantic_proposals(id) ON DELETE SET NULL,
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  before_hash TEXT NOT NULL,
  after_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  undone_at TEXT
);

CREATE TABLE IF NOT EXISTS semantic_decisions (
  id TEXT PRIMARY KEY,
  proposal_id TEXT NOT NULL UNIQUE REFERENCES semantic_proposals(id) ON DELETE CASCADE,
  decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected', 'partial')),
  accepted_operations_json TEXT NOT NULL DEFAULT '[]',
  actor TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  applied_revision INTEGER,
  audit_id TEXT REFERENCES semantic_audits(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_collection_progress (
  resource_id TEXT NOT NULL,
  collection_id TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued', 'in_progress', 'completed', 'snoozed', 'skipped')),
  priority INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
  completed_units INTEGER NOT NULL DEFAULT 0 CHECK (completed_units >= 0),
  total_units INTEGER CHECK (total_units IS NULL OR total_units >= 0),
  due_at TEXT,
  revision INTEGER NOT NULL DEFAULT 0,
  applied_decision_id TEXT REFERENCES semantic_decisions(id) ON DELETE SET NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (resource_id, collection_id),
  FOREIGN KEY (resource_id, collection_id)
    REFERENCES resource_collections(resource_id, collection_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mutation_audits (
  id TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  browser TEXT NOT NULL,
  request_id TEXT NOT NULL UNIQUE,
  approval_scope TEXT NOT NULL,
  plan_hash TEXT NOT NULL,
  planned_count INTEGER NOT NULL,
  closed_count INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('planned', 'completed', 'partial', 'failed')),
  evidence_path TEXT NOT NULL,
  before_capture_id TEXT,
  after_capture_id TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  error TEXT
);
"""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def open_connection(database_path: Path) -> sqlite3.Connection:
    """Open an initialized catalog without running schema work on the request path."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def connect(database_path: Path) -> sqlite3.Connection:
    """Initialize or migrate a catalog, then return an open connection."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    _backup_before_schema_upgrade(database_path)
    connection = open_connection(database_path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(SCHEMA)
    pairing_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(pairings)").fetchall()
    }
    if "protocol_version" not in pairing_columns:
        connection.execute(
            "ALTER TABLE pairings ADD COLUMN protocol_version INTEGER NOT NULL DEFAULT 2"
        )
    capture_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(captures)").fetchall()
    }
    if "trust_state" not in capture_columns:
        connection.execute(
            "ALTER TABLE captures ADD COLUMN trust_state TEXT NOT NULL DEFAULT 'trusted'"
        )
    tab_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(tab_instances)").fetchall()
    }
    if "favicon_url" not in tab_columns:
        connection.execute("ALTER TABLE tab_instances ADD COLUMN favicon_url TEXT")
    if "highlighted" not in tab_columns:
        connection.execute(
            "ALTER TABLE tab_instances ADD COLUMN highlighted INTEGER NOT NULL DEFAULT 0"
        )
    resource_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(resources)").fetchall()
    }
    if "archived_at" not in resource_columns:
        connection.execute("ALTER TABLE resources ADD COLUMN archived_at TEXT")
    if "library_state" not in resource_columns:
        connection.execute(
            "ALTER TABLE resources ADD COLUMN library_state TEXT NOT NULL DEFAULT 'accepted'"
        )
    if "accepted_at" not in resource_columns:
        connection.execute("ALTER TABLE resources ADD COLUMN accepted_at TEXT")
    if "dismissed_at" not in resource_columns:
        connection.execute("ALTER TABLE resources ADD COLUMN dismissed_at TEXT")
    if "semantic_revision" not in resource_columns:
        connection.execute(
            "ALTER TABLE resources ADD COLUMN semantic_revision INTEGER NOT NULL DEFAULT 0"
        )
    connection.execute(
        "UPDATE resources SET accepted_at=COALESCE(accepted_at, first_seen_at) "
        "WHERE library_state='accepted'"
    )
    collection_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(collections)").fetchall()
    }
    if "parent_id" not in collection_columns:
        connection.execute(
            "ALTER TABLE collections ADD COLUMN parent_id TEXT REFERENCES collections(id)"
        )
    if "workflow_kind" not in collection_columns:
        connection.execute(
            "ALTER TABLE collections ADD COLUMN workflow_kind TEXT NOT NULL DEFAULT 'none'"
        )
    if "config_json" not in collection_columns:
        connection.execute(
            "ALTER TABLE collections ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'"
        )
    membership_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(resource_collections)"
        ).fetchall()
    }
    if "authority" not in membership_columns:
        connection.execute(
            "ALTER TABLE resource_collections "
            "ADD COLUMN authority TEXT NOT NULL DEFAULT 'legacy_effective'"
        )
    if "applied_decision_id" not in membership_columns:
        connection.execute(
            "ALTER TABLE resource_collections ADD COLUMN applied_decision_id TEXT"
        )
    for topic_name, space_name in TOPIC_PARENT_DEFAULTS.items():
        connection.execute(
            """
            UPDATE collections
            SET parent_id=(
              SELECT parent.id FROM collections parent
              WHERE parent.name=? COLLATE NOCASE
            )
            WHERE name=? COLLATE NOCASE AND parent_id IS NULL
            """,
            (space_name, topic_name),
        )
    connection.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    connection.commit()
    return connection


def workspace_access_token(
    connection: sqlite3.Connection, *, rotate: bool = False
) -> str:
    """Return the private durable token used to authorize local browser sessions."""
    row = connection.execute(
        "SELECT value FROM meta WHERE key='workspace_access_token'"
    ).fetchone()
    token = str(row["value"] if row else "")
    if not rotate and re.fullmatch(r"[A-Za-z0-9_-]{40,100}", token):
        return token
    token = secrets.token_urlsafe(48)
    with connection:
        connection.execute(
            """
            INSERT INTO meta(key, value) VALUES('workspace_access_token', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (token,),
        )
    return token


def remember_workspace_origin(
    connection: sqlite3.Connection, origin: str, *, history_limit: int = 8
) -> None:
    """Remember recent loopback workspace origins so captures can ignore them."""
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", origin):
        raise ValueError("Workspace origin must be an HTTP loopback origin")
    origins: list[str] = []
    for row in connection.execute(
        "SELECT value FROM meta WHERE key IN ('workspace_origin', 'workspace_origins')"
    ):
        origins.extend(value for value in str(row["value"] or "").splitlines() if value)
    origins.append(origin)
    remembered = list(dict.fromkeys(origins))[-max(1, min(history_limit, 32)) :]
    with connection:
        connection.execute(
            """
            INSERT INTO meta(key, value) VALUES('workspace_origin', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (origin,),
        )
        connection.execute(
            """
            INSERT INTO meta(key, value) VALUES('workspace_origins', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            ("\n".join(remembered),),
        )


def _backup_before_schema_upgrade(database_path: Path) -> None:
    if not database_path.is_file() or database_path.stat().st_size == 0:
        return
    source = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        row = source.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        try:
            current_version = int(row[0]) if row else 0
        except (TypeError, ValueError):
            current_version = 0
        if current_version >= SCHEMA_VERSION:
            return
        backup_dir = database_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = backup_dir / f"pre-schema-v{current_version}-{timestamp}.sqlite"
        temporary = destination.with_suffix(".sqlite.tmp")
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValueError("Pre-migration database backup failed integrity_check")
            target.commit()
        finally:
            target.close()
        os.replace(temporary, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        _atomic_write(
            destination.with_suffix(".sqlite.sha256"),
            f"{digest}  {destination.name}\n".encode("ascii"),
        )
    finally:
        source.close()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def save_pairing(
    connection: sqlite3.Connection,
    browser: str,
    extension_id: str,
    token: str,
    protocol_version: int = LEGACY_CAPTURE_PROTOCOL_VERSION,
) -> None:
    browser = normalize_browser(browser)
    if browser not in {"chrome", "edge"}:
        raise ValueError("Pairing supports Chrome or Edge only")
    if not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ValueError("Unexpected extension ID")
    if not isinstance(protocol_version, int) or not 1 <= protocol_version <= 999:
        raise ValueError("Pairing protocol version is invalid")
    now = utc_now()
    with connection:
        connection.execute(
            """
            INSERT INTO pairings(
              browser, extension_id, token_hash, enabled, protocol_version, created_at
            )
            VALUES(?, ?, ?, 1, ?, ?)
            ON CONFLICT(browser) DO UPDATE SET
              extension_id=excluded.extension_id,
              token_hash=excluded.token_hash,
              enabled=1,
              protocol_version=excluded.protocol_version,
              created_at=excluded.created_at,
              last_seen_at=NULL
            """,
            (browser, extension_id, token_hash(token), protocol_version, now),
        )


def pairing_secret(
    connection: sqlite3.Connection,
    browser: str,
    extension_id: str,
) -> dict[str, Any] | None:
    browser = normalize_browser(browser)
    row = connection.execute(
        """
        SELECT browser, extension_id, token_hash, enabled, protocol_version
        FROM pairings
        WHERE browser=? AND extension_id=?
        """,
        (browser, extension_id),
    ).fetchone()
    if not row:
        return None
    return {
        "browser": row["browser"],
        "extension_id": row["extension_id"],
        "secret": row["token_hash"],
        "enabled": bool(row["enabled"]),
        "protocol_version": int(row["protocol_version"]),
    }


def mark_pairing_seen(
    connection: sqlite3.Connection,
    browser: str,
    protocol_version: int | None = None,
) -> None:
    browser = normalize_browser(browser)
    if protocol_version is not None and (
        not isinstance(protocol_version, int) or not 1 <= protocol_version <= 999
    ):
        raise ValueError("Pairing protocol version is invalid")
    with connection:
        if protocol_version is None:
            connection.execute(
                "UPDATE pairings SET last_seen_at=? WHERE browser=?",
                (utc_now(), browser),
            )
        else:
            connection.execute(
                "UPDATE pairings SET last_seen_at=?, protocol_version=? WHERE browser=?",
                (utc_now(), protocol_version, browser),
            )


def protocol_proof(secret_hex: str, *parts: str) -> str:
    try:
        key = bytes.fromhex(secret_hex)
    except ValueError as error:
        raise ValueError("Pairing secret is invalid") from error
    message = "\n".join(("tabatlas-v1", *(str(part) for part in parts))).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def pairing_status(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT browser, extension_id, enabled, protocol_version, created_at, last_seen_at "
        "FROM pairings ORDER BY browser"
    ).fetchall()
    return [dict(row) for row in rows]


def revoke_pairing(connection: sqlite3.Connection, browser: str) -> bool:
    browser = normalize_browser(browser)
    if browser not in {"chrome", "edge"}:
        raise ValueError("Revocation supports Chrome or Edge only")
    with connection:
        result = connection.execute(
            "UPDATE pairings SET enabled=0 WHERE browser=? AND enabled=1",
            (browser,),
        )
    return result.rowcount > 0
