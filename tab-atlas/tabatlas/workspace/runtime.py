from __future__ import annotations

import json
import os
import queue
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from ..database import connect, open_connection
from ..presentation import generate_report, report_payload
from .agent import (
    AgentBusy,
    AgentUnavailable,
    CodexAppServer,
    build_note_prompt,
    build_workspace_prompt,
)
from .agent_requests import (
    complete_agent_request,
    defer_agent_request,
    mark_agent_request_failed,
    mark_agent_request_running,
    queued_agent_requests,
    recover_agent_requests,
)
from .browser_sync import BrowserSyncCoordinator
from .context import _note_agent_context, _safe_error_code, _workspace_agent_context
from .directory import action_list_summaries, workspace_directory_summaries
from .lease import WorkspaceLease
from .notes import note_counts
from .transcription import (
    audio_note_source,
    complete_audio_transcription,
    fail_audio_transcription,
    mark_audio_transcription_running,
    queued_audio_transcriptions,
)
from .semantics import proposal_is_current
from .server_constants import (
    AGENT_IDLE_SECONDS,
    TRANSCRIPTION_TIMEOUT_SECONDS,
    WORKSPACE_HOST,
)
from .support import ConflictError


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
        initialized = connect(self.database_path)
        initialized.close()
        self.report_dir = report_dir.resolve()
        self.report_assets = report_assets.resolve()
        self.lease = lease
        self.session_token = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        self.expected_host = f"{WORKSPACE_HOST}:{self.server_port}"
        self.origin = f"http://{self.expected_host}"
        self.agent = CodexAppServer(self.workspace_root, self.state_dir)
        self.agent_handed_off = False
        self._ownership_lock = threading.Lock()
        self._agent_idle_lock = threading.Lock()
        self._agent_idle_timer: threading.Timer | None = None
        self._report_lock = threading.Lock()
        self.browser_sync = BrowserSyncCoordinator(
            self.database_path,
            self.state_dir,
            self.regenerate_report,
        )
        self._request_queue: queue.Queue[str | None] = queue.Queue()
        self._queued_ids: set[str] = set()
        self._queued_lock = threading.Lock()
        self._transcription_queue: queue.Queue[str | None] = queue.Queue()
        self._transcription_ids: set[str] = set()
        self._transcription_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker = threading.Thread(
            target=self._agent_worker,
            name="tabatlas-agent-worker",
            daemon=True,
        )
        self._worker.start()
        self._transcription_worker = threading.Thread(
            target=self._audio_transcription_worker,
            name="tabatlas-transcription-worker",
            daemon=True,
        )
        self._transcription_worker.start()
        with self.database() as connection:
            for request in recover_agent_requests(connection, 100):
                self.enqueue_agent_request(request["id"])
            for note in queued_audio_transcriptions(connection, 100):
                self.enqueue_audio_transcription(note["id"])

    @contextmanager
    def database(self) -> Iterator[sqlite3.Connection]:
        connection = open_connection(self.database_path)
        try:
            yield connection
        finally:
            connection.close()

    def enqueue_agent_request(self, request_id: str) -> None:
        self._cancel_agent_idle_stop()
        with self._queued_lock:
            if request_id in self._queued_ids:
                return
            self._queued_ids.add(request_id)
        self._request_queue.put(request_id)

    def enqueue_audio_transcription(self, note_id: str) -> None:
        with self._transcription_lock:
            if note_id in self._transcription_ids:
                return
            self._transcription_ids.add(note_id)
        self._transcription_queue.put(note_id)

    def regenerate_report(self) -> None:
        with self._report_lock, self.database() as connection:
            generate_report(
                connection, self.report_dir, self.report_assets, self.state_dir
            )

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
        self._cancel_agent_idle_stop()
        self._request_queue.put(None)
        self._transcription_queue.put(None)
        self.agent.stop()
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
                WHERE d.id IS NULL
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
            "browserSync": self.browser_sync.status(),
            "voice": {
                "recording": True,
                "automaticTranscription": True,
                "engine": "local_whisper",
                "editableTranscript": True,
            },
        }

    def handoff_agent(self) -> dict[str, Any]:
        self._cancel_agent_idle_stop()
        if not self._ownership_lock.acquire(blocking=False):
            raise ConflictError(
                "Wait for the current Codex turn before opening it in desktop"
            )
        try:
            if not self.agent.thread_id:
                self.agent.start()
            if self.agent.busy:
                raise ConflictError(
                    "Wait for the current Codex turn before opening it in desktop"
                )
            deep_link = self.agent.deep_link
            self.agent.stop()
            self.agent_handed_off = True
            return {"ownership": "codex_desktop", "deepLink": deep_link}
        finally:
            self._ownership_lock.release()

    def reclaim_agent(self) -> dict[str, Any]:
        with self._ownership_lock:
            self.agent_handed_off = False
            status = self.agent.start()
        with self.database() as connection:
            for request in queued_agent_requests(connection, 100):
                self.enqueue_agent_request(request["id"])
        self._schedule_agent_idle_stop()
        return {**status, "ownership": "workspace"}

    def _audio_transcription_worker(self) -> None:
        while not self._stop_event.is_set():
            note_id = self._transcription_queue.get()
            if note_id is None:
                return
            try:
                self._process_audio_transcription(note_id)
            finally:
                with self._transcription_lock:
                    self._transcription_ids.discard(note_id)

    def _process_audio_transcription(self, note_id: str) -> None:
        try:
            with self.database() as connection:
                note = mark_audio_transcription_running(connection, note_id)
                if (
                    note.get("processing", {}).get("transcription", {}).get("state")
                    == "succeeded"
                ):
                    return
                source = audio_note_source(connection, self.state_dir, note_id)
            result = self._run_local_transcriber(source["path"])
            with self.database() as connection:
                complete_audio_transcription(
                    connection,
                    note_id,
                    result["text"],
                    result["model"],
                )
        except Exception as error:
            if self._stop_event.is_set():
                return
            with self.database() as connection:
                try:
                    fail_audio_transcription(
                        connection, note_id, _safe_error_code(error)
                    )
                except ValueError:
                    pass

    def _run_local_transcriber(self, audio_path: Path) -> dict[str, str]:
        script = self.workspace_root / "scripts" / "tab_atlas_transcribe.py"
        if not script.is_file():
            raise RuntimeError("local_transcriber_missing")
        environment = os.environ.copy()
        environment.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        environment.setdefault("TOKENIZERS_PARALLELISM", "false")
        creation_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        )
        process = subprocess.Popen(
            [sys.executable, str(script), str(audio_path)],
            cwd=str(self.workspace_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=creation_flags,
        )
        deadline = time.monotonic() + TRANSCRIPTION_TIMEOUT_SECONDS
        while process.poll() is None:
            if self._stop_event.wait(0.25):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise RuntimeError("workspace_stopped")
            if time.monotonic() >= deadline:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError("local_transcription_timeout")
        stdout, _stderr = process.communicate()
        try:
            payload = json.loads(stdout or "{}")
        except json.JSONDecodeError as error:
            raise RuntimeError("local_transcriber_invalid_output") from error
        if (
            process.returncode
            or not isinstance(payload, dict)
            or not payload.get("text")
        ):
            code = str(payload.get("error") or "local_transcription_failed")
            raise RuntimeError(code)
        return {
            "text": str(payload["text"]),
            "model": str(payload.get("model") or "local_whisper"),
        }

    def _agent_worker(self) -> None:
        while not self._stop_event.is_set():
            request_id = self._request_queue.get()
            if request_id is None:
                return
            try:
                self._process_agent_request(request_id)
            finally:
                with self._queued_lock:
                    self._queued_ids.discard(request_id)

    def _process_agent_request(self, request_id: str) -> None:
        with self._ownership_lock:
            if self.agent_handed_off:
                return
            self._process_agent_request_owned(request_id)
            self._schedule_agent_idle_stop()

    def _cancel_agent_idle_stop(self) -> None:
        with self._agent_idle_lock:
            timer = self._agent_idle_timer
            self._agent_idle_timer = None
        if timer:
            timer.cancel()

    def _schedule_agent_idle_stop(self) -> None:
        self._cancel_agent_idle_stop()
        if self._stop_event.is_set():
            return
        timer = threading.Timer(AGENT_IDLE_SECONDS, self._stop_idle_agent)
        timer.daemon = True
        with self._agent_idle_lock:
            self._agent_idle_timer = timer
        timer.start()

    def _stop_idle_agent(self) -> None:
        with self._agent_idle_lock:
            self._agent_idle_timer = None
        if not self._ownership_lock.acquire(blocking=False):
            return
        try:
            if (
                not self.agent_handed_off
                and not self.agent.busy
                and self._request_queue.empty()
            ):
                self.agent.stop()
        finally:
            self._ownership_lock.release()

    def _process_agent_request_owned(self, request_id: str) -> None:
        request = None
        with self.database() as connection:
            try:
                request = connection.execute(
                    "SELECT * FROM agent_requests WHERE id=?",
                    (request_id,),
                ).fetchone()
                if not request or request["status"] != "queued":
                    return
                mark_agent_request_running(connection, request_id)
                if request["kind"] == "interpret_note":
                    context = _note_agent_context(
                        connection,
                        request["resource_id"],
                        request["note_id"],
                    )
                    prompt = build_note_prompt(context)
                else:
                    context = _workspace_agent_context(
                        connection,
                        request["resource_id"],
                        str(request["request_text"] or ""),
                    )
                    prompt = build_workspace_prompt(
                        context, str(request["request_text"] or "")
                    )
            except Exception as error:
                if request and request["status"] == "queued":
                    try:
                        mark_agent_request_failed(
                            connection, request_id, _safe_error_code(error)
                        )
                    except ValueError:
                        pass
                return

        try:
            response = self.agent.turn(prompt)
            with self.database() as connection:
                complete_agent_request(
                    connection,
                    request_id,
                    response,
                    self.agent.thread_id,
                    self.agent.model,
                )
            self.regenerate_report()
        except (AgentUnavailable, AgentBusy) as error:
            self.agent.stop()
            with self.database() as connection:
                defer_agent_request(connection, request_id, _safe_error_code(error))
        except (ValueError, json.JSONDecodeError) as error:
            with self.database() as connection:
                mark_agent_request_failed(
                    connection, request_id, _safe_error_code(error)
                )
