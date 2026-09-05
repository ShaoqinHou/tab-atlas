from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..common import _atomic_write, _file_sha256, _require_inside_path
from ..constants import TABATLAS_EXTENSION_URL_PREFIX


def record_archive_plan(
    connection: sqlite3.Connection,
    state_dir: Path,
    plan: dict[str, Any],
    approval_scope: str,
) -> Path:
    approval = approval_scope.strip()[:1000]
    if not approval:
        raise ValueError("A bounded explicit archive approval scope is required")
    if plan.get("action") != "archive_captured_tabs":
        raise ValueError("Archive evidence requires an archive_captured_tabs plan")
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    if integrity != "ok":
        raise ValueError(f"Catalog integrity check failed: {integrity[:200]}")

    capture_evidence: dict[str, dict[str, Any]] = {}
    for browser, browser_plan in plan["browsers"].items():
        capture_row = connection.execute(
            "SELECT rowid AS capture_rowid, * FROM captures "
            "WHERE id=? AND browser=? AND trust_state='trusted'",
            (browser_plan["beforeCaptureId"], browser),
        ).fetchone()
        if not capture_row:
            raise ValueError(
                f"Archive plan is not bound to a trusted {browser} capture"
            )
        capture = dict(capture_row)
        raw_path = (state_dir / str(capture["raw_path"])).resolve()
        _require_inside_path(raw_path, state_dir)
        if not raw_path.is_file():
            raise ValueError(f"Raw capture evidence is missing for {browser}")
        capture_evidence[browser] = {
            "captureId": capture["id"],
            "tabCount": int(capture["tab_count"]),
            "rawPath": str(raw_path.relative_to(state_dir)),
            "rawSha256": _file_sha256(raw_path),
        }

    retained_resource_ids = {
        str(target["resourceId"])
        for browser_plan in plan["browsers"].values()
        for target in browser_plan["targets"]
        if str(target.get("retention") or "retain") == "retain"
    }
    discarded_resource_ids = {
        str(target["resourceId"])
        for browser_plan in plan["browsers"].values()
        for target in browser_plan["targets"]
        if str(target.get("retention") or "retain") == "discard"
    }
    operational_resource_ids = {
        str(target["resourceId"])
        for browser_plan in plan["browsers"].values()
        for target in browser_plan["targets"]
        if str(target.get("retention") or "retain") == "operational"
    }
    planned_targets = [
        target
        for browser_plan in plan["browsers"].values()
        for target in browser_plan["targets"]
    ]
    if any(
        str(target.get("retention") or "retain")
        not in {"retain", "discard", "operational"}
        for target in planned_targets
    ):
        raise ValueError("Archive target retention decision is invalid")
    if retained_resource_ids:
        placeholders = ",".join("?" for _ in retained_resource_ids)
        retained_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM resources WHERE id IN ({placeholders}) "
                "AND canonical_url <> '' AND library_state='accepted'",
                sorted(retained_resource_ids),
            ).fetchone()[0]
        )
    else:
        retained_count = 0
    if retained_count != len(retained_resource_ids):
        raise ValueError("Not every archive target has a durable resource record")
    if discarded_resource_ids:
        placeholders = ",".join("?" for _ in discarded_resource_ids)
        discarded_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM resources WHERE id IN ({placeholders}) "
                "AND canonical_url <> '' AND library_state='dismissed' "
                "AND dismissed_at IS NOT NULL",
                sorted(discarded_resource_ids),
            ).fetchone()[0]
        )
    else:
        discarded_count = 0
    if discarded_count != len(discarded_resource_ids):
        raise ValueError("Not every discard target has an explicit reviewed dismissal")
    if operational_resource_ids:
        placeholders = ",".join("?" for _ in operational_resource_ids)
        operational_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM resources WHERE id IN ({placeholders}) "
                "AND canonical_url LIKE ? AND library_state='accepted'",
                [
                    *sorted(operational_resource_ids),
                    f"{TABATLAS_EXTENSION_URL_PREFIX}%",
                ],
            ).fetchone()[0]
        )
    else:
        operational_count = 0
    if operational_count != len(operational_resource_ids):
        raise ValueError(
            "Not every operational target belongs to the TabAtlas extension"
        )

    safe_time = re.sub(r"[^0-9]", "", str(plan["createdAt"]))[:14] or "undated"
    backup_dir = state_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{safe_time}-pre-archive-{plan['requestId'][:8]}.sqlite"
    temporary_backup = backup_path.with_suffix(".tmp")
    temporary_backup.unlink(missing_ok=True)
    destination = sqlite3.connect(temporary_backup)
    try:
        connection.backup(destination)
        backup_integrity = str(
            destination.execute("PRAGMA integrity_check").fetchone()[0]
        )
    finally:
        destination.close()
    if backup_integrity != "ok":
        temporary_backup.unlink(missing_ok=True)
        raise ValueError(
            f"Archive backup integrity check failed: {backup_integrity[:200]}"
        )
    temporary_backup.replace(backup_path)

    request_id = str(plan["requestId"])
    evidence_path = (
        state_dir / "mutations" / f"{safe_time}-archive-{request_id[:8]}.json"
    )
    durability = {
        "catalogIntegrity": integrity,
        "durableResourceCount": retained_count,
        "reviewedDiscardResourceCount": discarded_count,
        "operationalResourceCount": operational_count,
        "captures": capture_evidence,
        "backupPath": str(backup_path.relative_to(state_dir)),
        "backupSha256": _file_sha256(backup_path),
        "backupIntegrity": backup_integrity,
    }
    evidence = {
        "plan": plan,
        "approvalScope": approval,
        "durability": durability,
        "results": {},
        "postCaptures": {},
    }
    _atomic_write(
        evidence_path,
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        ),
    )
    relative_path = str(evidence_path.relative_to(state_dir))
    with connection:
        for browser, browser_plan in plan["browsers"].items():
            if not browser_plan["targets"]:
                continue
            audit_id = (
                "mut_"
                + hashlib.sha256(f"{request_id}|{browser}".encode("utf-8")).hexdigest()[
                    :24
                ]
            )
            connection.execute(
                """
                INSERT INTO mutation_audits(
                  id, action, browser, request_id, approval_scope, plan_hash,
                  planned_count, status, evidence_path, before_capture_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?)
                """,
                (
                    audit_id,
                    plan["action"],
                    browser,
                    f"{request_id}:{browser}",
                    approval,
                    browser_plan["targetsHash"],
                    len(browser_plan["targets"]),
                    relative_path,
                    browser_plan["beforeCaptureId"],
                    plan["createdAt"],
                ),
            )
    return evidence_path
