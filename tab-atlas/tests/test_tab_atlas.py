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
    connect,
    current_resources,
    generate_report,
    inventory,
    normalize_snapshot_document,
    pairing_secret,
    protocol_proof,
    report_payload,
    resource_presentation,
    revoke_pairing,
    save_pairing,
    store_snapshot,
    token_hash,
)
from tab_atlas_receiver import (  # noqa: E402
    EXPECTED_EXTENSION_ID,
    run_capture,
    run_pairing,
    run_revocation,
)
from tab_atlas import DEFAULT_STATE, build_parser  # noqa: E402


ORIGIN = f"chrome-extension://{EXPECTED_EXTENSION_ID}"


class CatalogTests(unittest.TestCase):
    def test_batch_filter_does_not_overwrite_the_state_directory(self) -> None:
        args = build_parser().parse_args(["batch", "--state", "all"])

        self.assertEqual(args.state, DEFAULT_STATE)
        self.assertEqual(args.batch_state, "all")

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
            connection.close()

            self.assertEqual(totals["candidateCaptures"], 1)
            self.assertEqual(totals["captures"][0]["capturedAt"], "2026-07-18T00:00:00Z")
            self.assertEqual(resources[0]["title"], "Trusted")

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
                "title": "Example video",
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

    def test_exported_report_decision_is_annotation_compatible(self) -> None:
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
