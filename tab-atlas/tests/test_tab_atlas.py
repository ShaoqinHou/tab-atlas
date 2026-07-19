from __future__ import annotations

import json
import hashlib
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tab_atlas_core import (  # noqa: E402
    apply_annotations,
    build_archive_cleanup_plan,
    build_archive_plan,
    build_exact_duplicate_plan,
    connect,
    current_resources,
    discovery_batch,
    discovery_resources,
    generate_report,
    finalize_archive_plan,
    finalize_mutation_plan,
    inventory,
    library_resources,
    normalize_snapshot_document,
    pairing_secret,
    protocol_proof,
    report_payload,
    record_archive_plan,
    record_mutation_plan,
    resource_presentation,
    revoke_pairing,
    save_pairing,
    set_discovery_state,
    store_snapshot,
    token_hash,
)
from tab_atlas_receiver import (  # noqa: E402
    EXPECTED_EXTENSION_ID,
    run_archive_cleanup,
    run_capture,
    run_mutation,
    run_pairing,
    run_revocation,
)
from tab_atlas import (  # noqa: E402
    DEFAULT_STATE,
    _load_archive_approval,
    _load_standing_approval,
    _write_archive_approval,
    _write_standing_approval,
    build_parser,
)


ORIGIN = f"chrome-extension://{EXPECTED_EXTENSION_ID}"


class CatalogTests(unittest.TestCase):
    def test_live_discoveries_require_acceptance_and_known_urls_are_not_readded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-20T00:00:00Z",
                    "tabs": [
                        {"id": 1, "windowId": 1, "index": 0, "title": "Known", "url": "https://example.com/known"},
                    ],
                },
                "preserved_import",
            )
            first_live = store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-20T01:00:00Z",
                    "tabs": [
                        {"id": 1, "windowId": 1, "index": 0, "title": "Known again", "url": "https://example.com/known"},
                        {"id": 2, "windowId": 1, "index": 1, "title": "New", "url": "https://example.com/new"},
                    ],
                },
                "extension_live",
                new_resource_state="candidate",
            )
            store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-20T02:00:00Z",
                    "tabs": [
                        {"id": 1, "windowId": 1, "index": 0, "title": "Known", "url": "https://example.com/known"},
                        {"id": 2, "windowId": 1, "index": 1, "title": "New again", "url": "https://example.com/new"},
                    ],
                },
                "extension_live",
                new_resource_state="candidate",
            )

            before = inventory(connection)
            pending = discovery_resources(connection)
            staged_report = report_payload(connection)
            decision = set_discovery_state(
                connection,
                "accepted",
                {pending[0]["resourceId"]},
            )
            after = inventory(connection)
            accepted = library_resources(connection)
            connection.close()

            self.assertEqual(first_live["new_resource_count"], 1)
            self.assertEqual(before["libraryResources"], 1)
            self.assertEqual(before["pendingDiscoveries"], 1)
            self.assertEqual(before["currentKnownResources"], 1)
            self.assertEqual(before["currentDiscoveryResources"], 1)
            self.assertEqual(len(staged_report["resources"]), 1)
            self.assertEqual(len(staged_report["discoveries"]), 1)
            self.assertEqual(staged_report["discoveries"][0]["libraryState"], "candidate")
            self.assertEqual(decision["updated"], 1)
            self.assertEqual(after["libraryResources"], 2)
            self.assertEqual(after["pendingDiscoveries"], 0)
            self.assertEqual({item["libraryState"] for item in accepted}, {"accepted"})

    def test_archive_plan_blocks_unreviewed_and_dismissed_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            store_snapshot(
                connection,
                state,
                {
                    "browser": "edge",
                    "capturedAt": "2026-07-20T00:00:00Z",
                    "tabs": [
                        {"id": 1, "windowId": 1, "index": 0, "title": "Candidate", "url": "https://example.com/candidate"},
                        {"id": 2, "windowId": 1, "index": 1, "title": "Candidate 2", "url": "https://example.com/dismiss"},
                        {"id": 3, "windowId": 1, "index": 2, "title": "Candidate 3", "url": "https://example.com/pending"},
                    ],
                },
                "extension_live",
                new_resource_state="candidate",
            )
            pending = {
                item["canonicalUrl"]: item["resourceId"]
                for item in discovery_resources(connection)
            }
            set_discovery_state(
                connection,
                "accepted",
                {pending["https://example.com/candidate"]},
            )
            set_discovery_state(
                connection,
                "dismissed",
                {pending["https://example.com/dismiss"]},
            )

            plan = build_archive_plan(connection, {"edge"})
            connection.close()

            self.assertEqual(plan["summary"]["plannedClosures"], 1)
            self.assertEqual(plan["summary"]["pendingDiscoveryCount"], 1)
            self.assertEqual(plan["summary"]["dismissedOpenResourceCount"], 1)
            self.assertNotIn("https://", json.dumps(plan))

    def test_dismissed_discovery_can_be_listed_and_restored_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            store_snapshot(
                connection,
                state,
                {
                    "browser": "edge",
                    "capturedAt": "2026-07-20T00:00:00Z",
                    "tabs": [{
                        "id": 7,
                        "windowId": 1,
                        "index": 0,
                        "title": "Review later",
                        "url": "https://example.com/reconsider",
                    }],
                },
                "extension_live",
                new_resource_state="candidate",
            )
            resource_id = discovery_resources(connection)[0]["resourceId"]
            set_discovery_state(connection, "dismissed", {resource_id})

            dismissed = discovery_batch(connection, 10, state="dismissed")
            restored = set_discovery_state(connection, "accepted", {resource_id})
            plan = build_archive_plan(connection, {"edge"})
            connection.close()

            self.assertEqual(dismissed["state"], "dismissed")
            self.assertEqual(dismissed["total"], 1)
            self.assertEqual(restored["updated"], 1)
            self.assertEqual(plan["summary"]["dismissedOpenResourceCount"], 0)
            self.assertEqual(plan["summary"]["plannedClosures"], 1)

    def test_close_plans_bind_to_explicit_fresh_capture_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            future = store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2099-01-01T00:00:00Z",
                    "tabs": [{
                        "id": 90,
                        "windowId": 9,
                        "index": 0,
                        "groupId": -1,
                        "title": "Future clock",
                        "url": "https://example.com/future",
                    }],
                },
                "extension_live",
            )
            fresh = store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-20T00:00:00Z",
                    "tabs": [
                        {"id": 1, "windowId": 1, "index": 0, "groupId": -1, "title": "A", "url": "https://example.com/same"},
                        {"id": 2, "windowId": 1, "index": 1, "groupId": -1, "title": "B", "url": "https://example.com/same"},
                    ],
                },
                "extension_live",
            )

            archive = build_archive_plan(
                connection,
                {"chrome"},
                {"chrome": fresh["id"]},
            )
            duplicate = build_exact_duplicate_plan(
                connection,
                {"chrome"},
                capture_ids={"chrome": fresh["id"]},
            )
            current = current_resources(connection)
            connection.close()

            self.assertNotEqual(future["id"], fresh["id"])
            self.assertEqual(archive["browsers"]["chrome"]["beforeCaptureId"], fresh["id"])
            self.assertEqual(archive["summary"]["plannedClosures"], 2)
            self.assertEqual(duplicate["browsers"]["chrome"]["beforeCaptureId"], fresh["id"])
            self.assertEqual(duplicate["summary"]["plannedClosures"], 1)
            self.assertEqual(current[0]["canonicalUrl"], "https://example.com/same")
            self.assertEqual(len(current[0]["tabs"]), 2)

    def test_collection_hierarchy_preserves_space_topic_and_focus_parents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            result = store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-20T00:00:00Z",
                    "tabs": [
                        {"id": 1, "windowId": 1, "index": 0, "title": "Agent UI", "url": "https://example.com/agent-ui"},
                    ],
                },
                "extension_live",
            )
            resource = library_resources(connection)[0]
            apply_annotations(
                connection,
                {
                    "resources": [{
                        "resourceId": resource["resourceId"],
                        "replaceCollections": True,
                        "collections": [
                            {"name": "Build Software & Agents", "kind": "space"},
                            {"name": "Coding Agents", "kind": "topic"},
                            {"name": "Review workflows", "kind": "focus", "parent": "Coding Agents"},
                        ],
                    }],
                },
            )
            with self.assertRaisesRegex(ValueError, "another parent"):
                apply_annotations(
                    connection,
                    {
                        "resources": [{
                            "resourceId": resource["resourceId"],
                            "collections": [{
                                "name": "Review workflows",
                                "kind": "focus",
                                "parent": "Product & UI",
                            }],
                        }],
                    },
                )
            payload = report_payload(connection)
            connection.close()

            self.assertEqual(result["resource_count"], 1)
            topic = next(item for item in payload["topicSummaries"] if item["name"] == "Coding Agents")
            focus = next(item for item in payload["focusSummaries"] if item["name"] == "Review workflows")
            self.assertEqual(topic["parentName"], "Build Software & Agents")
            self.assertEqual(focus["parentName"], "Coding Agents")
            self.assertEqual(payload["resources"][0]["presentation"]["focuses"], ["Review workflows"])

    def test_batch_filter_does_not_overwrite_the_state_directory(self) -> None:
        args = build_parser().parse_args(["batch", "--state", "all"])

        self.assertEqual(args.state, DEFAULT_STATE)
        self.assertEqual(args.batch_state, "all")

    def test_standing_duplicate_approval_is_private_and_revocable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            scope = "Close only policy-v1 exact HTTPS duplicates."

            path = _write_standing_approval(state, scope, True)
            self.assertEqual(_load_standing_approval(state), scope)
            self.assertTrue(path.is_relative_to(state))

            _write_standing_approval(state, "revoked", False)
            self.assertEqual(_load_standing_approval(state), "")

    def test_standing_archive_approval_is_private_and_revocable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            scope = "Archive every tab proven by a fresh trusted capture."

            path = _write_archive_approval(state, scope, True)
            self.assertEqual(_load_archive_approval(state), scope)
            self.assertTrue(path.is_relative_to(state))

            _write_archive_approval(state, "revoked", False)
            self.assertEqual(_load_archive_approval(state), "")

    def test_uppercase_legacy_rows_are_grouped_by_browser(self) -> None:
        snapshots = normalize_snapshot_document(
            [
                {"Browser": "Chrome", "Title": "One", "Url": "https://example.com/a", "Rank": "2"},
                {"Browser": "Edge", "Title": "Two", "Url": "https://example.com/b", "Rank": "3"},
            ]
        )

        self.assertEqual([item["browser"] for item in snapshots], ["chrome", "edge"])
        self.assertEqual(snapshots[0]["tabs"][0]["title"], "One")
        self.assertEqual(snapshots[1]["tabs"][0]["index"], 3)

    def test_snapshot_import_is_idempotent_and_preserves_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            snapshot = {
                "browser": "edge",
                "capturedAt": "2026-07-19T00:00:00Z",
                "windows": [{"id": 1, "focused": True, "tabCount": 2}],
                "groups": [{"id": 7, "windowId": 1, "title": "Research", "color": "blue", "collapsed": True}],
                "tabs": [
                    {"id": 10, "windowId": 1, "index": 0, "groupId": 7, "title": "A", "url": "https://example.com/page?utm_source=x"},
                    {"id": 11, "windowId": 1, "index": 1, "groupId": 7, "title": "B", "url": "https://example.com/page"},
                ],
            }

            first = store_snapshot(connection, state, snapshot, "test")
            second = store_snapshot(connection, state, snapshot, "test")
            resources = current_resources(connection)
            totals = inventory(connection)
            connection.close()

            self.assertFalse(first.get("duplicate", False))
            self.assertTrue(second["duplicate"])
            self.assertEqual(totals["currentTabs"], 2)
            self.assertEqual(totals["currentResources"], 1)
            self.assertEqual(totals["duplicateTabInstances"], 1)
            self.assertEqual(totals["groups"], 1)
            self.assertEqual({tab["groupTitle"] for tab in resources[0]["tabs"]}, {"Research"})
            self.assertTrue(all(tab["groupCollapsed"] for tab in resources[0]["tabs"]))

    def test_exact_duplicate_plan_only_closes_same_context_public_web_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-19T00:00:00Z",
                    "groups": [{"id": 7, "windowId": 1, "title": "One"}],
                    "tabs": [
                        {"id": 1, "windowId": 1, "index": 0, "groupId": -1, "active": True, "title": "A", "url": "https://example.com/a"},
                        {"id": 2, "windowId": 1, "index": 1, "groupId": -1, "title": "A copy", "url": "https://example.com/a"},
                        {"id": 3, "windowId": 1, "index": 2, "groupId": 7, "title": "A grouped", "url": "https://example.com/a"},
                        {"id": 4, "windowId": 1, "index": 3, "groupId": -1, "title": "File", "url": "file:///C:/private.txt"},
                        {"id": 5, "windowId": 1, "index": 4, "groupId": -1, "title": "File copy", "url": "file:///C:/private.txt"},
                        {"id": 6, "windowId": 1, "index": 5, "groupId": -1, "title": "Local", "url": "http://127.0.0.1:9000/app"},
                        {"id": 7, "windowId": 1, "index": 6, "groupId": -1, "title": "Local copy", "url": "http://127.0.0.1:9000/app"},
                        {"id": 8, "windowId": 2, "index": 0, "groupId": -1, "active": True, "title": "Selected", "url": "https://example.com/selected"},
                        {"id": 9, "windowId": 2, "index": 1, "groupId": -1, "highlighted": True, "title": "Selected copy", "url": "https://example.com/selected"},
                    ],
                },
                "test",
            )

            plan = build_exact_duplicate_plan(connection, {"chrome"})
            connection.close()

            self.assertEqual(plan["summary"]["plannedClosures"], 1)
            self.assertEqual(plan["summary"]["excludedInstances"], 1)
            self.assertEqual(plan["browsers"]["chrome"]["targets"][0]["targetTabId"], 2)
            self.assertNotIn("https://", json.dumps(plan))
            self.assertNotIn("file:///", json.dumps(plan))
            self.assertNotIn("127.0.0.1", json.dumps(plan))

    def test_library_survives_a_new_empty_live_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-20T00:00:00Z",
                    "tabs": [{"id": 1, "windowId": 1, "index": 0, "groupId": 4, "title": "Stored", "url": "https://example.com/stored"}],
                    "groups": [{"id": 4, "windowId": 1, "title": "Reference"}],
                },
                "test",
            )
            store_snapshot(
                connection,
                state,
                {"browser": "chrome", "capturedAt": "2026-07-20T00:01:00Z", "tabs": []},
                "test",
            )

            self.assertEqual(current_resources(connection), [])
            library = library_resources(connection)
            payload = report_payload(connection)
            connection.close()

            self.assertEqual(len(library), 1)
            self.assertEqual(library[0]["openTabCount"], 0)
            self.assertEqual(library[0]["contexts"][0]["groupTitle"], "Reference")
            self.assertEqual(len(payload["resources"]), 1)
            self.assertEqual(payload["inventory"]["currentTabs"], 0)
            self.assertEqual(payload["inventory"]["libraryResources"], 1)

    def test_archive_plan_backs_up_catalog_and_preserves_closed_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-20T00:00:00Z",
                    "tabs": [
                        {"id": 1, "windowId": 1, "index": 0, "groupId": -1, "active": True, "title": "Web", "url": "https://example.com/a"},
                        {"id": 2, "windowId": 1, "index": 1, "groupId": -1, "pinned": True, "title": "Local", "url": "file:///C:/reference.txt"},
                    ],
                },
                "test",
            )
            plan = build_archive_plan(connection, {"chrome"})
            evidence_path = record_archive_plan(
                connection,
                state,
                plan,
                "User approved closing every freshly captured tab after durable backup.",
            )
            post = store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-20T00:01:00Z",
                    "tabs": [{
                        "id": 99,
                        "windowId": 1,
                        "index": 0,
                        "groupId": -1,
                        "title": "Archive verification",
                        "url": f"chrome-extension://{EXPECTED_EXTENSION_ID}/archive_complete.html",
                    }],
                },
                "extension_live",
            )
            results = {
                "chrome": {
                    "closedCount": 2,
                    "skippedCount": 0,
                    "controlTabId": 99,
                    "controlWindowId": 1,
                    "results": [
                        {"tabId": target["targetTabId"], "status": "closed", "reason": "captured_and_archived"}
                        for target in plan["browsers"]["chrome"]["targets"]
                    ],
                }
            }
            totals = finalize_archive_plan(
                connection,
                state,
                plan,
                results,
                {"chrome": post},
            )
            library = library_resources(connection)
            live = current_resources(connection)
            statuses = {row["status"] for row in connection.execute("SELECT status FROM resources WHERE canonical_url NOT LIKE 'chrome-extension:%'")}
            connection.close()

            self.assertEqual(plan["summary"]["plannedClosures"], 2)
            self.assertNotIn("https://", json.dumps(plan))
            self.assertNotIn("file:///", json.dumps(plan))
            self.assertEqual(totals["closed"], 2)
            self.assertEqual(totals["verifiedBrowsers"], 1)
            self.assertEqual(totals["archivedResources"], 2)
            self.assertEqual(live, [])
            self.assertEqual(len(library), 2)
            self.assertEqual(statuses, {"saved"})
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            backup_path = state / evidence["durability"]["backupPath"]
            self.assertTrue(backup_path.is_file())
            self.assertEqual(evidence["durability"]["catalogIntegrity"], "ok")
            self.assertNotIn("example.com", evidence_path.read_text(encoding="utf-8"))

    def test_archive_finalization_rejects_unknown_post_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            before = store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-20T00:00:00Z",
                    "tabs": [{
                        "id": 1,
                        "windowId": 1,
                        "index": 0,
                        "groupId": -1,
                        "title": "Stored",
                        "url": "https://example.com/stored",
                    }],
                },
                "extension_live",
            )
            plan = build_archive_plan(
                connection,
                {"chrome"},
                {"chrome": before["id"]},
            )
            record_archive_plan(connection, state, plan, "Bound archive safety test")
            target = plan["browsers"]["chrome"]["targets"][0]
            totals = finalize_archive_plan(
                connection,
                state,
                plan,
                {
                    "chrome": {
                        "closedCount": 1,
                        "skippedCount": 0,
                        "controlTabId": 99,
                        "controlWindowId": 1,
                        "results": [{"tabId": target["targetTabId"], "status": "closed"}],
                    }
                },
                {"chrome": {"id": "cap_missing", "tab_count": 0}},
            )
            audit = connection.execute(
                "SELECT status, after_capture_id FROM mutation_audits "
                "WHERE action='archive_captured_tabs'"
            ).fetchone()
            resource_status = connection.execute(
                "SELECT status FROM resources WHERE id=?",
                (target["resourceId"],),
            ).fetchone()["status"]
            connection.close()

            self.assertEqual(totals["verifiedBrowsers"], 0)
            self.assertEqual(totals["archivedResources"], 0)
            self.assertEqual(audit["status"], "partial")
            self.assertIsNone(audit["after_capture_id"])
            self.assertEqual(resource_status, "open")

    def test_mutation_audit_verifies_closed_target_absent_and_keeper_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-19T00:00:00Z",
                    "tabs": [
                        {"id": 1, "windowId": 1, "index": 0, "groupId": -1, "active": True, "title": "A", "url": "https://example.com/a"},
                        {"id": 2, "windowId": 1, "index": 1, "groupId": -1, "title": "A copy", "url": "https://example.com/a"},
                    ],
                },
                "test",
            )
            plan = build_exact_duplicate_plan(connection, {"chrome"})
            evidence_path = record_mutation_plan(
                connection,
                state,
                plan,
                "User approved exact duplicate closure for this test.",
            )
            after = store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-19T00:01:00Z",
                    "tabs": [
                        {"id": 1, "windowId": 1, "index": 0, "groupId": -1, "active": True, "title": "A", "url": "https://example.com/a"}
                    ],
                },
                "extension_live",
            )
            target_id = plan["browsers"]["chrome"]["targets"][0]["targetTabId"]
            totals = finalize_mutation_plan(
                connection,
                state,
                plan,
                {"chrome": {"closedCount": 1, "skippedCount": 0, "results": [{"tabId": target_id, "status": "closed", "reason": "exact_duplicate"}]}},
                {"chrome": after},
            )
            audit = connection.execute("SELECT * FROM mutation_audits").fetchone()
            connection.close()

            self.assertEqual(totals["closed"], 1)
            self.assertEqual(totals["verifiedBrowsers"], 1)
            self.assertEqual(audit["status"], "completed")
            evidence = evidence_path.read_text(encoding="utf-8")
            self.assertNotIn("https://", evidence)

    def test_revocation_invalidates_the_server_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = connect(Path(temporary) / "atlas.sqlite")
            save_pairing(connection, "chrome", EXPECTED_EXTENSION_ID, "test-token")

            self.assertTrue(pairing_secret(connection, "chrome", EXPECTED_EXTENSION_ID)["enabled"])
            self.assertTrue(revoke_pairing(connection, "chrome"))
            self.assertFalse(pairing_secret(connection, "chrome", EXPECTED_EXTENSION_ID)["enabled"])
            self.assertFalse(revoke_pairing(connection, "chrome"))
            connection.close()

    def test_report_keeps_captured_titles_inside_the_data_script(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "assets"
            assets.mkdir()
            (assets / "app.js").write_text("", encoding="utf-8")
            (assets / "app.css").write_text("", encoding="utf-8")
            connection = connect(root / "atlas.sqlite")
            store_snapshot(
                connection,
                root,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-19T00:00:00Z",
                    "tabs": [{"title": "</script><script>bad()</script>", "url": "https://example.com/"}],
                },
                "test",
            )

            report = generate_report(connection, root / "report", assets).read_text(encoding="utf-8")
            connection.close()

            self.assertNotIn("</script><script>bad()", report)
            self.assertIn("<\\/script><script>bad()", report)

    def test_resource_keeps_an_exact_open_url_separate_from_its_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            exact = "https://example.com/app?utm_source=mail#item-42"
            store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-19T00:00:00Z",
                    "tabs": [{"title": "Example", "url": exact}],
                },
                "test",
            )

            resource = current_resources(connection)[0]
            connection.close()

            self.assertEqual(resource["canonicalUrl"], "https://example.com/app")
            self.assertEqual(resource["openUrl"], exact)

    def test_recovery_candidate_cannot_replace_the_trusted_current_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-18T00:00:00Z",
                    "tabs": [{"title": "Trusted", "url": "https://example.com/trusted"}],
                },
                "extension_live",
            )
            store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-19T00:00:00Z",
                    "tabs": [{"title": "Candidate", "url": "https://example.com/candidate"}],
                },
                "isolated_recovery",
                trust_state="candidate",
            )

            totals = inventory(connection)
            resources = current_resources(connection)
            library = library_resources(connection)
            discoveries = discovery_resources(connection)
            connection.close()

            self.assertEqual(totals["candidateCaptures"], 1)
            self.assertEqual(totals["captures"][0]["capturedAt"], "2026-07-18T00:00:00Z")
            self.assertEqual(resources[0]["title"], "Trusted")
            self.assertEqual(len(library), 1)
            self.assertEqual(library[0]["title"], "Trusted")
            self.assertEqual(len(discoveries), 1)
            self.assertEqual(discoveries[0]["title"], "Candidate")

    def test_a_brief_without_a_collection_stays_in_the_review_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            stored = store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-19T00:00:00Z",
                    "tabs": [{"title": "Opaque", "url": "https://example.com/opaque"}],
                },
                "test",
            )
            resource = connection.execute(
                "SELECT resource_id FROM tab_instances WHERE capture_id=?",
                (stored["id"],),
            ).fetchone()["resource_id"]
            apply_annotations(connection, [{"resourceId": resource, "brief": "Context is still unclear."}])

            totals = inventory(connection)
            connection.close()

            self.assertEqual(totals["unclassifiedResources"], 1)

    def test_presentation_exposes_decision_signals_without_loading_remote_media(self) -> None:
        presentation = resource_presentation(
            {
                "openUrl": "https://www.youtube.com/watch?v=abc123XYZ00&utm_source=test",
                "canonicalUrl": "https://www.youtube.com/watch?v=abc123XYZ00",
                "host": "www.youtube.com",
                "kind": "youtube",
                "title": "(12) Example video - YouTube",
                "brief": "A useful example.",
                "whyKept": "",
                "nextAction": "watch or listen",
                "status": "open",
                "collections": [],
                "tasks": [],
                "tabs": [
                    {"groupTitle": "", "browser": "chrome"},
                    {"groupTitle": "", "browser": "edge"},
                ],
            }
        )

        self.assertEqual(presentation["format"], "Video")
        self.assertEqual(presentation["displayTitle"], "Example video")
        self.assertEqual(presentation["intent"], "Watch")
        self.assertEqual(presentation["queue"], "needs_context")
        self.assertTrue(presentation["preview"]["requiresUserLoad"])
        self.assertEqual(
            presentation["preview"]["remoteImage"],
            "https://i.ytimg.com/vi/abc123XYZ00/mqdefault.jpg",
        )

    def test_report_keeps_duplicate_group_titles_as_distinct_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-19T00:00:00Z",
                    "groups": [
                        {"id": 7, "windowId": 1, "title": "Research", "color": "orange"},
                        {"id": 8, "windowId": 1, "title": "Research", "color": "cyan"},
                    ],
                    "tabs": [
                        {"id": 10, "windowId": 1, "index": 0, "groupId": 7, "title": "A", "url": "https://example.com/a"},
                        {"id": 11, "windowId": 1, "index": 1, "groupId": 8, "title": "B", "url": "https://example.com/b"},
                    ],
                },
                "test",
            )

            groups = report_payload(connection)["groups"]
            connection.close()

            self.assertEqual(len(groups), 2)
            self.assertEqual(len({group["id"] for group in groups}), 2)
            self.assertEqual({group["resourceCount"] for group in groups}, {1})
            self.assertEqual(
                {group["displayTitle"] for group in groups},
                {"Research (orange)", "Research (cyan)"},
            )

    def test_group_summary_preserves_browser_tab_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            store_snapshot(
                connection,
                state,
                {
                    "browser": "edge",
                    "capturedAt": "2026-07-19T00:00:00Z",
                    "groups": [{"id": 4, "windowId": 1, "title": "Ordered", "color": "blue"}],
                    "tabs": [
                        {"id": 10, "windowId": 1, "index": 0, "groupId": 4, "title": "Z first", "url": "https://example.com/z"},
                        {"id": 11, "windowId": 1, "index": 1, "groupId": 4, "title": "A second", "url": "https://example.com/a"},
                    ],
                },
                "test",
            )

            payload = report_payload(connection)
            connection.close()
            resources_by_id = {item["resourceId"]: item for item in payload["resources"]}
            ordered_titles = [resources_by_id[resource_id]["title"] for resource_id in payload["groups"][0]["resourceIds"]]

            self.assertEqual(ordered_titles, ["Z first", "A second"])

    def test_status_annotation_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            connection = connect(state / "atlas.sqlite")
            stored = store_snapshot(
                connection,
                state,
                {
                    "browser": "chrome",
                    "capturedAt": "2026-07-19T00:00:00Z",
                    "tabs": [{"title": "Decision", "url": "https://example.com/decision"}],
                },
                "test",
            )
            resource_id = connection.execute(
                "SELECT resource_id FROM tab_instances WHERE capture_id=?",
                (stored["id"],),
            ).fetchone()["resource_id"]

            apply_annotations(
                connection,
                {
                    "schemaVersion": 1,
                    "resources": [{"resourceId": resource_id, "status": "close_candidate"}],
                },
            )
            resource = current_resources(connection)[0]
            connection.close()

            self.assertEqual(resource["status"], "close_candidate")


class ReceiverIntegrationTests(unittest.TestCase):
    def test_pair_then_capture_is_request_bound_and_one_shot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            database = state / "atlas.sqlite"
            codes: queue.Queue[str] = queue.Queue()
            pair_result: list[object] = []
            pair_thread = threading.Thread(
                target=lambda: pair_result.extend(run_pairing(database, state, "chrome", 5, codes.put)),
                daemon=True,
            )
            pair_thread.start()
            code = codes.get(timeout=2)
            pair_nonce = "1" * 32
            pair_key = token_hash(code)
            paired = self._json_request(
                "/v1/pair",
                method="POST",
                body={
                    "browser": "chrome",
                    "extensionId": EXPECTED_EXTENSION_ID,
                    "nonce": pair_nonce,
                    "clientProof": protocol_proof(
                        pair_key,
                        "pair",
                        "chrome",
                        EXPECTED_EXTENSION_ID,
                        pair_nonce,
                    ),
                },
            )
            pair_thread.join(timeout=3)
            self.assertFalse(pair_thread.is_alive())
            self.assertTrue(pair_result[0])
            token = paired["token"]
            key = token_hash(token)
            self.assertEqual(
                paired["serverProof"],
                protocol_proof(
                    pair_key,
                    "paired",
                    "chrome",
                    EXPECTED_EXTENSION_ID,
                    pair_nonce,
                    key,
                ),
            )

            capture_result: list[object] = []
            capture_thread = threading.Thread(
                target=lambda: capture_result.extend(run_capture(database, state, {"chrome"}, 5)),
                daemon=True,
            )
            capture_thread.start()
            with self.assertRaises(HTTPError) as health_error:
                self._retry_request("/health")
            self.assertEqual(health_error.exception.code, 404)
            with self.assertRaises(HTTPError) as bearer_error:
                self._retry_request("/v1/command", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(bearer_error.exception.code, 401)

            command_nonce = "2" * 32
            command = self._retry_request(
                "/v1/command",
                headers=self._signed_headers(key, "command", command_nonce),
            )
            self.assertEqual(command["action"], "capture")
            self.assertEqual(
                command["serverProof"],
                protocol_proof(
                    key,
                    "response",
                    "chrome",
                    EXPECTED_EXTENSION_ID,
                    command_nonce,
                    command["requestId"],
                    "capture",
                    "",
                ),
            )
            snapshot = {
                "schemaVersion": 1,
                "requestId": command["requestId"],
                "browser": "chrome",
                "extensionId": EXPECTED_EXTENSION_ID,
                "capturedAt": "2026-07-19T00:00:00Z",
                "windows": [{"id": 1, "focused": False, "tabCount": 1}],
                "groups": [],
                "tabs": [{"id": 4, "windowId": 1, "index": 0, "title": "Example", "url": "https://example.com/"}],
            }
            snapshot_nonce = "3" * 32
            body_hash = hashlib.sha256(json.dumps(snapshot).encode("utf-8")).hexdigest()
            accepted = self._json_request(
                "/v1/snapshot",
                method="POST",
                headers=self._signed_headers(
                    key,
                    "snapshot",
                    snapshot_nonce,
                    command["requestId"],
                    body_hash,
                ),
                body=snapshot,
            )
            capture_thread.join(timeout=3)

            self.assertFalse(capture_thread.is_alive())
            self.assertTrue(capture_result[0])
            self.assertEqual(accepted["tabs"], 1)
            self.assertEqual(capture_result[1]["chrome"]["tab_count"], 1)
            self.assertEqual(
                accepted["serverProof"],
                protocol_proof(
                    key,
                    "accepted",
                    "chrome",
                    EXPECTED_EXTENSION_ID,
                    snapshot_nonce,
                    command["requestId"],
                    accepted["captureId"],
                ),
            )

    def test_revocation_notification_is_signed_before_the_extension_forgets_its_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            database = state / "atlas.sqlite"
            connection = connect(database)
            save_pairing(connection, "chrome", EXPECTED_EXTENSION_ID, "revocation-token")
            key = token_hash("revocation-token")
            revoke_pairing(connection, "chrome")
            connection.close()

            result: list[bool] = []
            thread = threading.Thread(
                target=lambda: result.append(run_revocation(database, state, "chrome", 5)),
                daemon=True,
            )
            thread.start()
            nonce = "4" * 32
            error = self._retry_http_error(
                "/v1/command",
                headers=self._signed_headers(key, "command", nonce),
            )
            payload = json.loads(error.read().decode("utf-8"))
            thread.join(timeout=3)

            self.assertEqual(error.code, 401)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result, [True])
            self.assertEqual(payload["action"], "revoked")
            self.assertEqual(
                payload["serverProof"],
                protocol_proof(
                    key,
                    "response",
                    "chrome",
                    EXPECTED_EXTENSION_ID,
                    nonce,
                    "",
                    "revoked",
                ),
            )

    def test_mutation_command_and_results_are_bound_to_the_exact_target_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            database = state / "atlas.sqlite"
            connection = connect(database)
            save_pairing(connection, "chrome", EXPECTED_EXTENSION_ID, "mutation-token")
            connection.close()
            key = token_hash("mutation-token")
            targets = [{
                "targetTabId": 12,
                "keeperTabId": 11,
                "expectedUrlHash": "a" * 64,
                "windowId": "1",
                "groupId": "-1",
                "targetInstanceId": "tab_target",
                "keeperInstanceId": "tab_keeper",
            }]
            targets_hash = hashlib.sha256(
                json.dumps(targets, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            plan = {
                "requestId": "request-mutation",
                "browsers": {"chrome": {"targets": targets, "targetsHash": targets_hash}},
            }
            mutation_result: list[object] = []
            thread = threading.Thread(
                target=lambda: mutation_result.extend(run_mutation(database, state, plan, 5)),
                daemon=True,
            )
            thread.start()

            command_nonce = "5" * 32
            command = self._retry_request(
                "/v1/command",
                headers=self._signed_headers(key, "command", command_nonce),
            )
            self.assertEqual(command["action"], "close_exact_duplicates")
            self.assertEqual(command["targetsHash"], targets_hash)
            self.assertEqual(
                command["serverProof"],
                protocol_proof(
                    key,
                    "response",
                    "chrome",
                    EXPECTED_EXTENSION_ID,
                    command_nonce,
                    plan["requestId"],
                    "close_exact_duplicates",
                    targets_hash,
                ),
            )

            body = {
                "requestId": plan["requestId"],
                "targetsHash": targets_hash,
                "browser": "chrome",
                "extensionId": EXPECTED_EXTENSION_ID,
                "results": [{"tabId": 12, "status": "closed", "reason": "exact_duplicate"}],
            }
            encoded = json.dumps(body).encode("utf-8")
            result_nonce = "6" * 32
            accepted = self._json_request(
                "/v1/mutation",
                method="POST",
                headers=self._signed_headers(
                    key,
                    "mutation",
                    result_nonce,
                    plan["requestId"],
                    targets_hash,
                    hashlib.sha256(encoded).hexdigest(),
                ),
                body=body,
            )
            thread.join(timeout=3)

            self.assertFalse(thread.is_alive())
            self.assertTrue(mutation_result[0])
            self.assertEqual(mutation_result[1]["chrome"]["closedCount"], 1)
            self.assertEqual(
                accepted["serverProof"],
                protocol_proof(
                    key,
                    "mutation-accepted",
                    "chrome",
                    EXPECTED_EXTENSION_ID,
                    result_nonce,
                    plan["requestId"],
                    targets_hash,
                    "1",
                    "0",
                ),
            )

    def test_archive_cleanup_records_the_actual_close_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            database = state / "atlas.sqlite"
            connection = connect(database)
            save_pairing(connection, "chrome", EXPECTED_EXTENSION_ID, "cleanup-token")
            connection.close()
            key = token_hash("cleanup-token")
            plan = build_archive_cleanup_plan(
                {"requestId": "archive-request"},
                {"chrome": {"controlTabId": 99, "controlWindowId": 4}},
            )
            cleanup_result: list[object] = []
            thread = threading.Thread(
                target=lambda: cleanup_result.extend(
                    run_archive_cleanup(database, state, plan, 5)
                ),
                daemon=True,
            )
            thread.start()

            command_nonce = "7" * 32
            command = self._retry_request(
                "/v1/command",
                headers=self._signed_headers(key, "command", command_nonce),
            )
            browser_plan = plan["browsers"]["chrome"]
            self.assertEqual(command["action"], "close_archive_control")
            self.assertEqual(command["targetsHash"], browser_plan["targetsHash"])

            body = {
                "requestId": plan["requestId"],
                "targetsHash": browser_plan["targetsHash"],
                "browser": "chrome",
                "extensionId": EXPECTED_EXTENSION_ID,
                "controlTabId": 99,
                "controlWindowId": 4,
                "status": "closed",
                "reason": "removed",
            }
            encoded = json.dumps(body).encode("utf-8")
            cleanup_nonce = "8" * 32
            accepted = self._json_request(
                "/v1/cleanup",
                method="POST",
                headers=self._signed_headers(
                    key,
                    "cleanup",
                    cleanup_nonce,
                    plan["requestId"],
                    browser_plan["targetsHash"],
                    hashlib.sha256(encoded).hexdigest(),
                ),
                body=body,
            )
            thread.join(timeout=3)

            self.assertFalse(thread.is_alive())
            self.assertTrue(cleanup_result[0])
            self.assertEqual(cleanup_result[1]["chrome"]["status"], "closed")
            self.assertEqual(
                accepted["serverProof"],
                protocol_proof(
                    key,
                    "cleanup-accepted",
                    "chrome",
                    EXPECTED_EXTENSION_ID,
                    cleanup_nonce,
                    plan["requestId"],
                    browser_plan["targetsHash"],
                    "99",
                    "4",
                    "closed",
                    "removed",
                ),
            )

    def _retry_request(
        self,
        path: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        deadline = time.monotonic() + 2
        while True:
            try:
                return self._json_request(path, headers=headers)
            except HTTPError:
                raise
            except URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)

    def _retry_http_error(
        self,
        path: str,
        headers: dict[str, str] | None = None,
    ) -> HTTPError:
        deadline = time.monotonic() + 2
        while True:
            try:
                self._json_request(path, headers=headers)
            except HTTPError as error:
                return error
            except URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
            else:
                raise AssertionError("Expected an HTTP error response")

    def _json_request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request_headers = {"Origin": ORIGIN, **(headers or {})}
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(f"http://127.0.0.1:9786{path}", data=data, headers=request_headers, method=method)
        with urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))

    def _signed_headers(
        self,
        key: str,
        purpose: str,
        nonce: str,
        *extra_parts: str,
    ) -> dict[str, str]:
        return {
            "X-TabAtlas-Browser": "chrome",
            "X-TabAtlas-Extension": EXPECTED_EXTENSION_ID,
            "X-TabAtlas-Nonce": nonce,
            "X-TabAtlas-Auth": protocol_proof(
                key,
                purpose,
                "chrome",
                EXPECTED_EXTENSION_ID,
                nonce,
                *extra_parts,
            ),
        }


if __name__ == "__main__":
    unittest.main()
