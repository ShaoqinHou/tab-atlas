from __future__ import annotations

import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tab_atlas_core import connect, generate_report, utc_now  # noqa: E402
from tab_atlas_workspace import (  # noqa: E402
    ConflictError,
    add_audio_transcript,
    agent_request_detail,
    apply_semantic_proposal,
    complete_audio_transcription,
    complete_agent_request,
    create_audio_note,
    create_note_analysis_request,
    create_text_note,
    create_workspace_chat_request,
    fail_audio_transcription,
    mark_audio_transcription_running,
    mark_agent_request_running,
    note_detail,
    proposal_is_current,
    queued_audio_transcriptions,
    reject_semantic_proposal,
    set_note_active,
    undo_semantic_audit,
    update_action_progress,
    workspace_directory_summaries,
)
import tab_atlas_workspace_server as workspace_server_module  # noqa: E402
from tab_atlas_workspace_server import (  # noqa: E402
    _workspace_agent_context,
    create_workspace_server,
)


RESOURCE_ID = "res_" + "a" * 24
CANDIDATE_ID = "res_" + "b" * 24
DISMISSED_ID = "res_" + "c" * 24


def seed_resource(connection) -> None:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO resources(
          id, canonical_url, host, kind, title, first_seen_at, last_seen_at,
          brief, status, library_state, accepted_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'saved', 'accepted', ?)
        """,
        (
            RESOURCE_ID,
            "https://example.com/course",
            "example.com",
            "web_page",
            "Example course",
            now,
            now,
            "A visual mathematics course.",
            now,
        ),
    )
    connection.commit()


def seed_candidate_resource(connection, resource_id: str, title: str) -> None:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO resources(
          id, canonical_url, host, kind, title, first_seen_at, last_seen_at,
          brief, status, library_state
        ) VALUES(?, ?, 'example.com', 'web_page', ?, ?, ?, '', 'open', 'candidate')
        """,
        (resource_id, f"https://example.com/{resource_id}", title, now, now),
    )
    connection.commit()


def agent_response() -> dict:
    return {
        "message": "This is a learning resource with explicit follow-through.",
        "interpretation": "Watch this course to learn mathematics used for large language models.",
        "confidence": 0.96,
        "needsReview": False,
        "memberships": [
            {
                "name": "Understand AI Models",
                "kind": "space",
                "parentName": "",
                "role": "primary",
                "reason": "The note explicitly connects it to language-model mathematics.",
            },
            {
                "name": "Models & Evaluation",
                "kind": "topic",
                "parentName": "Understand AI Models",
                "role": "reference",
                "reason": "It supplies model foundations.",
            },
            {
                "name": "Must Watch",
                "kind": "action_list",
                "parentName": "",
                "role": "queue",
                "reason": "The user said it must be watched.",
            },
        ],
        "actionItem": {
            "listName": "Must Watch",
            "state": "queued",
            "priority": 2,
            "estimatedMinutes": 120,
            "dueAt": "",
        },
        "navigation": {
            "command": "open_resource",
            "resourceId": RESOURCE_ID,
            "collectionName": "",
            "filter": "",
        },
    }


class WorkspaceDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.database = self.state / "atlas.sqlite"
        self.connection = connect(self.database)
        seed_resource(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def test_text_note_is_exact_idempotent_and_private_from_report(self) -> None:
        text = "  Must watch this for LLM mathematics.  "
        first = create_text_note(
            self.connection,
            RESOURCE_ID,
            text,
            "note-idempotency-0001",
        )
        second = create_text_note(
            self.connection,
            RESOURCE_ID,
            text,
            "note-idempotency-0001",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(note_detail(self.connection, first["id"])["text"], text)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM resource_notes").fetchone()[0],
            1,
        )
        with self.assertRaises(ConflictError):
            create_text_note(
                self.connection,
                RESOURCE_ID,
                "Different note text",
                "note-idempotency-0001",
            )

        report = self.root / "report"
        generate_report(self.connection, report, ROOT / "assets" / "report", self.state)
        html = (report / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("Must watch this for LLM mathematics", html)
        self.assertIn('"noteCount":1', html)

    def test_voice_note_stays_local_until_editable_transcript(self) -> None:
        wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"fmt " + b"\x00" * 24
        note = create_audio_note(
            self.connection,
            self.state,
            RESOURCE_ID,
            wav,
            "audio/wav",
            1500,
            "audio-idempotency-01",
        )
        self.assertTrue((self.state / "notes" / "audio" / f"{note['id']}.wav").is_file())
        self.assertEqual(note["processing"]["transcription"]["state"], "queued")
        add_audio_transcript(
            self.connection,
            note["id"],
            "Learn the math behind language models.",
            "transcript-idempotency-01",
        )
        detail = note_detail(self.connection, note["id"])
        self.assertEqual(detail["processing"]["transcription"]["state"], "succeeded")
        self.assertIsNone(detail["analysis"])
        request = create_note_analysis_request(
            self.connection,
            note["id"],
            "analysis-idempotency-voice-01",
        )
        self.assertEqual(request["status"], "queued")
        with self.assertRaises(ConflictError):
            add_audio_transcript(
                self.connection,
                note["id"],
                "A different transcript.",
                "transcript-idempotency-01",
            )

        with self.assertRaisesRegex(ValueError, "declared audio type"):
            create_audio_note(
                self.connection,
                self.state,
                RESOURCE_ID,
                b"not audio",
                "audio/wav",
                10,
                "audio-idempotency-02",
            )

    def test_retract_note_keeps_evidence_and_cancels_queued_interpretation(self) -> None:
        note = create_text_note(
            self.connection,
            RESOURCE_ID,
            "This is private durable evidence.",
            "note-idempotency-retract-01",
        )
        request = create_note_analysis_request(
            self.connection,
            note["id"],
            "analysis-idempotency-retract-01",
        )
        retracted = set_note_active(
            self.connection,
            note["id"],
            False,
            "note-retract-request-01",
        )
        self.assertFalse(retracted["active"])
        self.assertEqual(note_detail(self.connection, note["id"])["text"], note["text"])
        request = self.connection.execute(
            "SELECT status, error_code FROM agent_requests WHERE id=?",
            (request["id"],),
        ).fetchone()
        self.assertEqual(tuple(request), ("failed", "note_retracted"))

    def test_proposal_is_inert_then_applies_and_undoes(self) -> None:
        note = create_text_note(
            self.connection,
            RESOURCE_ID,
            "Must watch this to learn LLM mathematics.",
            "note-idempotency-0002",
        )
        request_id = create_note_analysis_request(
            self.connection,
            note["id"],
            "analysis-idempotency-0002",
        )["id"]
        mark_agent_request_running(self.connection, request_id)
        completed = complete_agent_request(
            self.connection,
            request_id,
            agent_response(),
            "00000000-0000-0000-0000-000000000001",
            "gpt-test",
        )
        proposal_id = completed["proposalId"]
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM resource_collections").fetchone()[0],
            0,
        )
        self.assertTrue(proposal_is_current(self.connection, proposal_id))
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM semantic_decisions").fetchone()[0],
            0,
        )
        decision = apply_semantic_proposal(
            self.connection,
            proposal_id,
            "workspace_user",
            "decision-idempotency-01",
        )
        names = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT c.name FROM resource_collections rc
                JOIN collections c ON c.id=rc.collection_id
                WHERE rc.resource_id=?
                """,
                (RESOURCE_ID,),
            )
        }
        self.assertEqual(names, {"Understand AI Models", "Models & Evaluation", "Must Watch"})
        self.assertFalse(note_detail(self.connection, note["id"])["analysis"]["inputCurrent"])
        directories = workspace_directory_summaries(self.connection)
        self.assertEqual(directories["spaces"][0]["name"], "Understand AI Models")
        self.assertEqual(directories["spaces"][0]["resourceIds"], [RESOURCE_ID])
        progress = self.connection.execute(
            "SELECT state, total_units FROM resource_collection_progress"
        ).fetchone()
        self.assertEqual(tuple(progress), ("queued", 120))

        undo_semantic_audit(self.connection, decision["auditId"])
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM resource_collections").fetchone()[0],
            0,
        )

    def test_action_progress_is_audited_idempotent_and_undoable(self) -> None:
        note = create_text_note(
            self.connection,
            RESOURCE_ID,
            "Must watch this to learn LLM mathematics.",
            "note-idempotency-progress-01",
        )
        request_id = create_note_analysis_request(
            self.connection,
            note["id"],
            "analysis-idempotency-progress-01",
        )["id"]
        mark_agent_request_running(self.connection, request_id)
        completed = complete_agent_request(
            self.connection,
            request_id,
            agent_response(),
            "00000000-0000-0000-0000-000000000003",
            "gpt-test",
        )
        apply_semantic_proposal(
            self.connection,
            completed["proposalId"],
            "workspace_user",
            "decision-idempotency-progress-01",
        )
        item = self.connection.execute(
            "SELECT collection_id, revision FROM resource_collection_progress"
        ).fetchone()
        updated = update_action_progress(
            self.connection,
            RESOURCE_ID,
            item["collection_id"],
            state="in_progress",
            priority=1,
            completed_units=30,
            total_units=120,
            due_at=None,
            expected_revision=item["revision"],
            request_id="progress-request-01",
        )
        repeated = update_action_progress(
            self.connection,
            RESOURCE_ID,
            item["collection_id"],
            state="in_progress",
            priority=1,
            completed_units=30,
            total_units=120,
            due_at=None,
            expected_revision=item["revision"],
            request_id="progress-request-01",
        )
        self.assertEqual(updated["auditId"], repeated["auditId"])
        progress = self.connection.execute(
            "SELECT state, priority, completed_units, total_units, revision FROM resource_collection_progress"
        ).fetchone()
        self.assertEqual(tuple(progress), ("in_progress", 1, 30, 120, 1))
        undo_semantic_audit(self.connection, updated["auditId"])
        restored = self.connection.execute(
            "SELECT state, priority, completed_units, total_units, revision FROM resource_collection_progress"
        ).fetchone()
        self.assertEqual(tuple(restored), ("queued", 2, 0, 120, 0))

    def test_low_confidence_note_proposal_remains_current_and_inert(self) -> None:
        note = create_text_note(
            self.connection,
            RESOURCE_ID,
            "Maybe this relates to my work.",
            "note-idempotency-confidence-01",
        )
        response = agent_response()
        response["confidence"] = 0.6
        request_id = create_note_analysis_request(
            self.connection,
            note["id"],
            "analysis-idempotency-confidence-01",
        )["id"]
        mark_agent_request_running(self.connection, request_id)
        completed = complete_agent_request(
            self.connection,
            request_id,
            response,
            "00000000-0000-0000-0000-000000000004",
            "gpt-test",
        )
        self.assertTrue(proposal_is_current(self.connection, completed["proposalId"]))
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM semantic_decisions").fetchone()[0],
            0,
        )
        rejected = reject_semantic_proposal(
            self.connection,
            completed["proposalId"],
            "workspace_user",
            "decision-idempotency-reject-01",
        )
        self.assertEqual(rejected["decision"], "rejected")
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM resource_collections").fetchone()[0],
            0,
        )

    def test_workspace_context_retrieves_a_bounded_relevant_subset(self) -> None:
        now = utc_now()
        for number in range(1, 31):
            resource_id = "res_" + format(number, "024x")
            title = "Linear algebra visual course" if number == 17 else f"Unrelated item {number}"
            self.connection.execute(
                """
                INSERT INTO resources(
                  id, canonical_url, host, kind, title, first_seen_at, last_seen_at,
                  status, library_state, accepted_at
                ) VALUES(?, ?, 'example.org', 'web_page', ?, ?, ?, 'saved', 'accepted', ?)
                """,
                (resource_id, f"https://example.org/{number}", title, now, now, now),
            )
        self.connection.commit()
        context = _workspace_agent_context(self.connection, None, "find linear algebra")
        self.assertEqual(len(context["resourceIndex"]), 1)
        self.assertEqual(context["resourceIndex"][0]["title"], "Linear algebra visual course")
        self.assertEqual(context["libraryResourceCount"], 31)
        broad = _workspace_agent_context(self.connection, None, "help organize my library")
        self.assertEqual(broad["resourceIndex"], [])

    def test_newer_note_makes_older_proposal_stale(self) -> None:
        note = create_text_note(
            self.connection,
            RESOURCE_ID,
            "Maybe watch this.",
            "note-idempotency-0003",
        )
        request_id = create_note_analysis_request(
            self.connection,
            note["id"],
            "analysis-idempotency-0003",
        )["id"]
        mark_agent_request_running(self.connection, request_id)
        completed = complete_agent_request(
            self.connection,
            request_id,
            agent_response(),
            "00000000-0000-0000-0000-000000000002",
            "gpt-test",
        )
        create_text_note(
            self.connection,
            RESOURCE_ID,
            "This is not relevant to my work.",
            "note-idempotency-0004",
        )
        self.assertFalse(agent_request_detail(self.connection, request_id)["proposalCurrent"])
        with self.assertRaises(ConflictError):
            apply_semantic_proposal(
                self.connection,
                completed["proposalId"],
                "workspace_user",
                "decision-idempotency-02",
            )

    def test_local_transcription_queue_completes_without_starting_codex(self) -> None:
        wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"fmt " + b"\x00" * 24
        note = create_audio_note(
            self.connection,
            self.state,
            RESOURCE_ID,
            wav,
            "audio/wav",
            1500,
            "audio-idempotency-queue-01",
        )
        self.assertEqual([item["id"] for item in queued_audio_transcriptions(self.connection)], [note["id"]])
        running = mark_audio_transcription_running(self.connection, note["id"])
        self.assertEqual(running["processing"]["transcription"]["state"], "running")
        completed = complete_audio_transcription(
            self.connection,
            note["id"],
            "Reorganize this as an animation production reference.",
            "openai/whisper-base",
        )
        self.assertEqual(completed["processing"]["transcription"]["state"], "succeeded")
        self.assertEqual(completed["processing"]["interpretation"]["state"], "not_required")
        self.assertEqual(queued_audio_transcriptions(self.connection), [])
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM agent_requests").fetchone()[0],
            0,
        )

        second = create_audio_note(
            self.connection,
            self.state,
            RESOURCE_ID,
            wav + b"\x00",
            "audio/wav",
            1500,
            "audio-idempotency-queue-02",
        )
        failed = fail_audio_transcription(
            self.connection,
            second["id"],
            "local_whisper_dependencies_missing",
        )
        self.assertEqual(failed["processing"]["transcription"]["state"], "failed")

    def test_transcript_correction_invalidates_pending_proposal(self) -> None:
        wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"fmt " + b"\x00" * 24
        note = create_audio_note(
            self.connection,
            self.state,
            RESOURCE_ID,
            wav,
            "audio/wav",
            1500,
            "audio-idempotency-stale-01",
        )
        add_audio_transcript(
            self.connection,
            note["id"],
            "Put this in my AI model learning queue.",
            "transcript-idempotency-stale-01",
        )
        request_id = create_note_analysis_request(
            self.connection,
            note["id"],
            "analysis-idempotency-stale-01",
        )["id"]
        mark_agent_request_running(self.connection, request_id)
        completed = complete_agent_request(
            self.connection,
            request_id,
            agent_response(),
            "00000000-0000-0000-0000-000000000005",
            "gpt-test",
        )
        self.assertTrue(proposal_is_current(self.connection, completed["proposalId"]))
        add_audio_transcript(
            self.connection,
            note["id"],
            "This is actually a visual animation reference, not an AI course.",
            "transcript-idempotency-stale-02",
        )
        self.assertFalse(proposal_is_current(self.connection, completed["proposalId"]))
        with self.assertRaisesRegex(ConflictError, "transcript changed"):
            apply_semantic_proposal(
                self.connection,
                completed["proposalId"],
                "workspace_user",
                "decision-idempotency-stale-01",
            )

    def test_discussed_proposal_is_bound_to_latest_authoritative_note(self) -> None:
        note = create_text_note(
            self.connection,
            RESOURCE_ID,
            "Treat this as an animation-production learning reference.",
            "note-idempotency-discussion-01",
        )
        request = create_workspace_chat_request(
            self.connection,
            "Refine the pending suggestion.",
            RESOURCE_ID,
        )
        mark_agent_request_running(self.connection, request["id"])
        completed = complete_agent_request(
            self.connection,
            request["id"],
            agent_response(),
            "00000000-0000-0000-0000-000000000006",
            "gpt-test",
        )
        proposal = self.connection.execute(
            "SELECT note_id FROM semantic_proposals WHERE id=?",
            (completed["proposalId"],),
        ).fetchone()
        self.assertEqual(proposal["note_id"], note["id"])
        create_text_note(
            self.connection,
            RESOURCE_ID,
            "This is no longer relevant.",
            "note-idempotency-discussion-02",
        )
        self.assertFalse(proposal_is_current(self.connection, completed["proposalId"]))

    def test_schema_upgrade_creates_integrity_checked_backup(self) -> None:
        self.connection.execute(
            "UPDATE meta SET value='8' WHERE key='schema_version'"
        )
        self.connection.commit()
        self.connection.close()
        self.connection = connect(self.database)
        backups = list((self.state / "backups").glob("pre-schema-v8-*.sqlite"))
        manifests = list((self.state / "backups").glob("pre-schema-v8-*.sqlite.sha256"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(manifests), 1)
        backup = sqlite3.connect(backups[0])
        try:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            backup.close()


class WorkspaceServerTests(unittest.TestCase):
    def test_server_transcription_worker_persists_local_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            database = state / "atlas.sqlite"
            report = root / "report"
            connection = connect(database)
            seed_resource(connection)
            generate_report(connection, report, ROOT / "assets" / "report", state)
            connection.close()
            server, _url = create_workspace_server(
                ROOT,
                state,
                database,
                report,
                ROOT / "assets" / "report",
                0,
            )
            server._run_local_transcriber = lambda _path: {
                "text": "A locally transcribed animation production note.",
                "model": "test-whisper",
            }
            try:
                wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"fmt " + b"\x00" * 24
                with server.database() as connection:
                    note = create_audio_note(
                        connection,
                        state,
                        RESOURCE_ID,
                        wav,
                        "audio/wav",
                        1500,
                        "audio-worker-idempotency-01",
                    )
                server.enqueue_audio_transcription(note["id"])
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    with server.database() as connection:
                        detail = note_detail(connection, note["id"])
                    if detail["processing"]["transcription"]["state"] == "succeeded":
                        break
                    time.sleep(0.02)
                self.assertEqual(
                    detail["processing"]["transcription"]["outputText"],
                    "A locally transcribed animation production note.",
                )
                self.assertIsNone(detail["analysis"])
            finally:
                server.server_close()

    def test_idle_agent_child_stops_without_a_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            database = state / "atlas.sqlite"
            report = root / "report"
            connection = connect(database)
            seed_resource(connection)
            generate_report(connection, report, ROOT / "assets" / "report", state)
            connection.close()
            server, _url = create_workspace_server(
                ROOT,
                state,
                database,
                report,
                ROOT / "assets" / "report",
                0,
            )
            stopped = threading.Event()

            class FakeAgent:
                busy = False

                def stop(self) -> None:
                    stopped.set()

            server.agent = FakeAgent()
            original_delay = workspace_server_module.AGENT_IDLE_SECONDS
            workspace_server_module.AGENT_IDLE_SECONDS = 0.05
            try:
                server._schedule_agent_idle_stop()
                self.assertTrue(stopped.wait(1))
            finally:
                workspace_server_module.AGENT_IDLE_SECONDS = original_delay
                server.server_close()

    def test_bootstrap_origin_csrf_and_note_api(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            database = state / "atlas.sqlite"
            report = root / "report"
            connection = connect(database)
            seed_resource(connection)
            seed_candidate_resource(connection, CANDIDATE_ID, "Accept this discovery")
            seed_candidate_resource(connection, DISMISSED_ID, "Dismiss this discovery")
            generate_report(connection, report, ROOT / "assets" / "report", state)
            connection.close()
            server, _url = create_workspace_server(
                ROOT,
                state,
                database,
                report,
                ROOT / "assets" / "report",
                0,
            )
            server.agent_handed_off = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                client.request(
                    "GET",
                    f"/?bootstrap={server.session_token}",
                    headers={"Host": server.expected_host},
                )
                response = client.getresponse()
                cookie = response.getheader("Set-Cookie").split(";", 1)[0]
                response.read()
                self.assertEqual(response.status, 303)

                client.request(
                    "GET",
                    "/api/v1/session",
                    headers={"Host": server.expected_host, "Cookie": cookie},
                )
                response = client.getresponse()
                session = json.loads(response.read())
                self.assertEqual(response.status, 200)

                decision_headers = {
                    "Host": server.expected_host,
                    "Cookie": cookie,
                    "Origin": server.origin,
                    "X-TabAtlas-CSRF": session["csrfToken"],
                    "Idempotency-Key": "server-idempotency-accept-discovery-01",
                    "Content-Type": "application/json",
                }
                decision_body = json.dumps({"resourceIds": [CANDIDATE_ID]}).encode("utf-8")
                decision_headers["Content-Length"] = str(len(decision_body))
                client.request("POST", "/api/v1/discoveries/accept", decision_body, decision_headers)
                response = client.getresponse()
                accepted = json.loads(response.read())
                self.assertEqual(response.status, 200, accepted)
                self.assertEqual(accepted["state"], "accepted")
                self.assertEqual(accepted["updated"], 1)

                decision_body = json.dumps({"resourceIds": [DISMISSED_ID]}).encode("utf-8")
                decision_headers["Content-Length"] = str(len(decision_body))
                decision_headers["Idempotency-Key"] = "server-idempotency-dismiss-discovery-01"
                client.request("POST", "/api/v1/discoveries/dismiss", decision_body, decision_headers)
                response = client.getresponse()
                dismissed = json.loads(response.read())
                self.assertEqual(response.status, 200, dismissed)
                self.assertEqual(dismissed["state"], "dismissed")
                self.assertEqual(dismissed["updated"], 1)

                decision_headers["Idempotency-Key"] = "server-idempotency-restore-discovery-01"
                client.request("POST", "/api/v1/discoveries/accept", decision_body, decision_headers)
                response = client.getresponse()
                restored = json.loads(response.read())
                self.assertEqual(response.status, 200, restored)
                self.assertEqual(restored["state"], "accepted")
                self.assertEqual(restored["updated"], 1)

                body = json.dumps({"text": "A private note"}).encode("utf-8")
                headers = {
                    "Host": server.expected_host,
                    "Cookie": cookie,
                    "Origin": "https://attacker.example",
                    "X-TabAtlas-CSRF": session["csrfToken"],
                    "Idempotency-Key": "server-idempotency-01",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                }
                client.request("POST", f"/api/v1/resources/{RESOURCE_ID}/notes", body, headers)
                response = client.getresponse()
                response.read()
                self.assertEqual(response.status, 403)

                headers["Origin"] = server.origin
                headers["X-TabAtlas-CSRF"] = "wrong"
                client.request("POST", f"/api/v1/resources/{RESOURCE_ID}/notes", body, headers)
                response = client.getresponse()
                response.read()
                self.assertEqual(response.status, 403)

                headers["X-TabAtlas-CSRF"] = session["csrfToken"]
                client.request("POST", f"/api/v1/resources/{RESOURCE_ID}/notes", body, headers)
                response = client.getresponse()
                document = json.loads(response.read())
                self.assertEqual(response.status, 201)
                self.assertEqual(document["text"], "A private note")

                empty_headers = {
                    "Host": server.expected_host,
                    "Cookie": cookie,
                    "Origin": server.origin,
                    "X-TabAtlas-CSRF": session["csrfToken"],
                    "Idempotency-Key": "server-idempotency-retract-01",
                    "Content-Length": "0",
                }
                empty_headers["Idempotency-Key"] = "server-idempotency-analyze-01"
                client.request(
                    "POST",
                    f"/api/v1/notes/{document['id']}/analyze",
                    b"",
                    empty_headers,
                )
                response = client.getresponse()
                analysis = json.loads(response.read())
                self.assertEqual(response.status, 202)
                self.assertEqual(analysis["status"], "queued")

                empty_headers["Idempotency-Key"] = "server-idempotency-retract-01"
                client.request(
                    "POST",
                    f"/api/v1/notes/{document['id']}/retract",
                    b"",
                    empty_headers,
                )
                response = client.getresponse()
                retracted = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertFalse(retracted["active"])

                empty_headers["Idempotency-Key"] = "server-idempotency-remove-01"
                client.request(
                    "POST",
                    f"/api/v1/resources/{RESOURCE_ID}/remove",
                    b"",
                    empty_headers,
                )
                response = client.getresponse()
                removed = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(removed["state"], "dismissed")
            finally:
                client.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
