from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from ..database import (
    connect,
    open_connection,
    remember_workspace_origin,
    workspace_access_token,
)
from ..presentation import generate_report, report_payload
from .agent_worker import WorkspaceAgentWorker
from .browser_sync import BrowserSyncCoordinator
from .directory import action_list_summaries, workspace_directory_summaries
from .lease import WorkspaceLease
from .notes import note_counts
from .organization_batches import organization_batch_summaries
from .product_browsers import launch_closed_product_browsers
from .report_updates import DeferredReportPublisher
from .semantics import proposal_is_current
from .tab_closure import TabClosureCoordinator
from .server_constants import AGENT_IDLE_SECONDS, WORKSPACE_HOST
from .transcription_worker import AudioTranscriptionWorker


class TabAtlasWorkspaceServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        handler: Any,
        *,
        workspace_root: Path,
        state_dir: Path,
        database_path: Path,
        report_dir: Path,
        report_assets: Path,
        lease: "WorkspaceLease",
    ) -> None:
        super().__init__(address, handler)
        self.workspace_root = workspace_root.resolve()
        self.state_dir = state_dir.resolve()
        self.database_path = database_path.resolve()
        self.expected_host = f"{WORKSPACE_HOST}:{self.server_port}"
        self.origin = f"http://{self.expected_host}"
        initialized = connect(self.database_path)
        self.session_token = workspace_access_token(initialized)
        remember_workspace_origin(initialized, self.origin)
        initialized.close()
        self.report_dir = report_dir.resolve()
        self.report_assets = report_assets.resolve()
        self.lease = lease
        self.csrf_token = secrets.token_urlsafe(32)
        self._report_lock = threading.Lock()
        self._browser_operation_lock = threading.Lock()
        self._stop_event = threading.Event()
        self.browser_sync = BrowserSyncCoordinator(
            self.database_path,
            self.state_dir,
            self.regenerate_report,
            browser_launcher=launch_closed_product_browsers,
            operation_lock=self._browser_operation_lock,
        )
        self.tab_closure = TabClosureCoordinator(
            self.database_path,
            self.state_dir,
            self.regenerate_report,
            operation_lock=self._browser_operation_lock,
        )
        self._deferred_report = DeferredReportPublisher(self.regenerate_report)
        self._agent_jobs = WorkspaceAgentWorker(
            self.workspace_root,
            self.state_dir,
            self.database,
            self.regenerate_report,
            self._stop_event,
            lambda: AGENT_IDLE_SECONDS,
        )
        self._transcription_jobs = AudioTranscriptionWorker(
            self.workspace_root,
            self.state_dir,
            self.database,
            lambda path: self._run_local_transcriber(path),
            self._stop_event,
        )
        self._expose_worker_compatibility_attributes()
        self._agent_jobs.start()
        self._transcription_jobs.start()
        self._agent_jobs.recover()
        self._transcription_jobs.recover()

    def _expose_worker_compatibility_attributes(self) -> None:
        self._request_queue = self._agent_jobs.queue
        self._queued_ids = self._agent_jobs.queued_ids
        self._queued_lock = self._agent_jobs.queued_lock
        self._ownership_lock = self._agent_jobs.ownership_lock
        self._agent_idle_lock = self._agent_jobs.idle_lock
        self._worker = self._agent_jobs.thread
        self._transcription_queue = self._transcription_jobs.queue
        self._transcription_ids = self._transcription_jobs.queued_ids
        self._transcription_lock = self._transcription_jobs.queued_lock
        self._transcription_worker = self._transcription_jobs.thread

    @property
    def agent(self) -> Any:
        return self._agent_jobs.agent

    @agent.setter
    def agent(self, value: Any) -> None:
        self._agent_jobs.agent = value

    @property
    def agent_handed_off(self) -> bool:
        return self._agent_jobs.handed_off

    @agent_handed_off.setter
    def agent_handed_off(self, value: bool) -> None:
        self._agent_jobs.handed_off = value

    @contextmanager
    def database(self) -> Iterator[sqlite3.Connection]:
        connection = open_connection(self.database_path)
        try:
            yield connection
        finally:
            connection.close()

    def enqueue_agent_request(self, request_id: str) -> None:
        self._agent_jobs.enqueue(request_id)

    def enqueue_audio_transcription(self, note_id: str) -> None:
        self._transcription_jobs.enqueue(note_id)

    def regenerate_report(self) -> None:
        with self._report_lock, self.database() as connection:
            generate_report(
                connection, self.report_dir, self.report_assets, self.state_dir
            )

    def schedule_report_regeneration(self) -> None:
        self._deferred_report.schedule()

    def start_tab_closure(self, resource_ids: set[str]) -> dict[str, Any]:
        return self.tab_closure.start(resource_ids)

    def catalog_snapshot(self) -> dict[str, Any]:
        with self.database() as connection:
            return report_payload(connection)

    def start_browser_sync(
        self,
        browsers: tuple[str, ...] = ("chrome", "edge"),
        *,
        trigger: str = "user",
    ) -> dict[str, Any]:
        return self.browser_sync.start(browsers, trigger=trigger)

    def server_close(self) -> None:
        self._stop_event.set()
        self._deferred_report.close()
        self._agent_jobs.close()
        self._transcription_jobs.close()
        super().server_close()
        self.lease.close()

    def session_status(self) -> dict[str, Any]:
        with self.database() as connection:
            counts = note_counts(connection)
            pending = connection.execute(
                "SELECT COUNT(*) AS count FROM agent_requests WHERE status IN ('queued', 'running')"
            ).fetchone()["count"]
            failed = connection.execute(
                "SELECT COUNT(*) AS count FROM agent_requests WHERE status='failed'"
            ).fetchone()["count"]
            pending_requests = [
                {
                    "id": row["id"],
                    "resourceId": row["resource_id"] or "",
                    "kind": row["kind"],
                    "errorCode": row["error_code"] or "",
                }
                for row in connection.execute(
                    """
                    SELECT id, resource_id, kind, error_code
                    FROM agent_requests
                    WHERE status IN ('queued', 'running')
                    ORDER BY created_at, rowid LIMIT 100
                    """
                )
            ]
            action_lists = action_list_summaries(connection)
            directories = workspace_directory_summaries(connection)
            recent_audits = [
                {
                    "id": row["id"],
                    "resourceId": row["resource_id"],
                    "createdAt": row["created_at"],
                    "undoneAt": row["undone_at"] or "",
                }
                for row in connection.execute(
                    """
                    SELECT id, resource_id, created_at, undone_at
                    FROM semantic_audits ORDER BY created_at DESC, rowid DESC LIMIT 50
                    """
                )
            ]
            pending_proposals = []
            for row in connection.execute(
                """
                SELECT p.id, p.resource_id, ar.response_json
                FROM semantic_proposals p
                LEFT JOIN semantic_decisions d ON d.proposal_id=p.id
                LEFT JOIN agent_requests ar ON ar.id=p.request_id
                LEFT JOIN organization_batch_items obi ON obi.proposal_id=p.id
                WHERE d.id IS NULL AND obi.proposal_id IS NULL
                ORDER BY p.created_at DESC, p.rowid DESC LIMIT 50
                """
            ):
                if not proposal_is_current(connection, row["id"]):
                    continue
                try:
                    response = json.loads(row["response_json"] or "{}")
                except json.JSONDecodeError:
                    response = {}
                pending_proposals.append(
                    {
                        "id": row["id"],
                        "resourceId": row["resource_id"],
                        "message": str(
                            response.get("message")
                            or response.get("interpretation")
                            or "Codex proposed an organization change."
                        )[:1200],
                        "response": response,
                    }
                )
            organization_batches = organization_batch_summaries(connection)
        agent = self.agent.status()
        return {
            "interactive": True,
            "csrfToken": self.csrf_token,
            "agent": {
                **agent,
                "ownership": "codex_desktop" if self.agent_handed_off else "workspace",
                "experimentalHost": True,
            },
            "pendingAgentRequests": pending,
            "pendingRequests": pending_requests,
            "failedAgentRequests": failed,
            "resourceNoteCounts": counts,
            "actionLists": action_lists,
            "spaceSummaries": directories["spaces"],
            "projectSummaries": directories["projects"],
            "recentAudits": recent_audits,
            "pendingProposals": pending_proposals,
            "organizationBatches": organization_batches,
            "browserSync": self.browser_sync.status(),
            "voice": {
                "recording": True,
                "automaticTranscription": True,
                "engine": "local_whisper",
                "editableTranscript": True,
            },
        }

    def handoff_agent(self) -> dict[str, Any]:
        return self._agent_jobs.handoff()

    def reclaim_agent(self) -> dict[str, Any]:
        return self._agent_jobs.reclaim()

    def _process_audio_transcription(self, note_id: str) -> None:
        self._transcription_jobs.process(note_id)

    def _run_local_transcriber(self, audio_path: Path) -> dict[str, str]:
        return self._transcription_jobs.run_local_transcriber(audio_path)

    def _process_agent_request(self, request_id: str) -> None:
        self._agent_jobs.process(request_id)

    def _cancel_agent_idle_stop(self) -> None:
        self._agent_jobs.cancel_idle_stop()

    def _schedule_agent_idle_stop(self) -> None:
        self._agent_jobs.schedule_idle_stop()

    def _stop_idle_agent(self) -> None:
        self._agent_jobs.stop_idle_agent()

    def _process_agent_request_owned(self, request_id: str) -> None:
        self._agent_jobs.process_owned(request_id)
