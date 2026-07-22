from __future__ import annotations

import json
import queue
import sqlite3
import threading
from pathlib import Path
from typing import Callable, ContextManager

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
from .context import _note_agent_context, _safe_error_code, _workspace_agent_context
from .support import ConflictError

DatabaseFactory = Callable[[], ContextManager[sqlite3.Connection]]


class WorkspaceAgentWorker:
    def __init__(
        self,
        workspace_root: Path,
        state_dir: Path,
        database: DatabaseFactory,
        regenerate_report: Callable[[], None],
        stop_event: threading.Event,
        idle_seconds: Callable[[], float],
    ) -> None:
        self.agent = CodexAppServer(workspace_root, state_dir)
        self.handed_off = False
        self.queue: queue.Queue[str | None] = queue.Queue()
        self.queued_ids: set[str] = set()
        self.queued_lock = threading.Lock()
        self.ownership_lock = threading.Lock()
        self.idle_lock = threading.Lock()
        self.idle_timer: threading.Timer | None = None
        self.thread = threading.Thread(
            target=self.run,
            name="tabatlas-agent-worker",
            daemon=True,
        )
        self._database = database
        self._regenerate_report = regenerate_report
        self._stop_event = stop_event
        self._idle_seconds = idle_seconds

    def start(self) -> None:
        self.thread.start()

    def recover(self) -> None:
        with self._database() as connection:
            requests = recover_agent_requests(connection, 100)
        for request in requests:
            self.enqueue(request["id"])

    def close(self) -> None:
        self.cancel_idle_stop()
        self.queue.put(None)
        self.agent.stop()

    def enqueue(self, request_id: str) -> None:
        self.cancel_idle_stop()
        with self.queued_lock:
            if request_id in self.queued_ids:
                return
            self.queued_ids.add(request_id)
        self.queue.put(request_id)

    def handoff(self) -> dict[str, object]:
        self.cancel_idle_stop()
        if not self.ownership_lock.acquire(blocking=False):
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
            self.handed_off = True
            return {"ownership": "codex_desktop", "deepLink": deep_link}
        finally:
            self.ownership_lock.release()

    def reclaim(self) -> dict[str, object]:
        with self.ownership_lock:
            self.handed_off = False
            status = self.agent.start()
        with self._database() as connection:
            requests = queued_agent_requests(connection, 100)
        for request in requests:
            self.enqueue(request["id"])
        self.schedule_idle_stop()
        return {**status, "ownership": "workspace"}

    def run(self) -> None:
        while not self._stop_event.is_set():
            request_id = self.queue.get()
            if request_id is None:
                return
            try:
                self.process(request_id)
            finally:
                with self.queued_lock:
                    self.queued_ids.discard(request_id)

    def process(self, request_id: str) -> None:
        with self.ownership_lock:
            if self.handed_off:
                return
            self.process_owned(request_id)
            self.schedule_idle_stop()

    def cancel_idle_stop(self) -> None:
        with self.idle_lock:
            timer = self.idle_timer
            self.idle_timer = None
        if timer:
            timer.cancel()

    def schedule_idle_stop(self) -> None:
        self.cancel_idle_stop()
        if self._stop_event.is_set():
            return
        timer = threading.Timer(self._idle_seconds(), self.stop_idle_agent)
        timer.daemon = True
        with self.idle_lock:
            self.idle_timer = timer
        timer.start()

    def stop_idle_agent(self) -> None:
        with self.idle_lock:
            self.idle_timer = None
        if not self.ownership_lock.acquire(blocking=False):
            return
        try:
            if not self.handed_off and not self.agent.busy and self.queue.empty():
                self.agent.stop()
        finally:
            self.ownership_lock.release()

    def process_owned(self, request_id: str) -> None:
        request = None
        with self._database() as connection:
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
            with self._database() as connection:
                complete_agent_request(
                    connection,
                    request_id,
                    response,
                    self.agent.thread_id,
                    self.agent.model,
                )
            self._regenerate_report()
        except (AgentUnavailable, AgentBusy) as error:
            self.agent.stop()
            with self._database() as connection:
                defer_agent_request(connection, request_id, _safe_error_code(error))
        except (ValueError, json.JSONDecodeError) as error:
            with self._database() as connection:
                mark_agent_request_failed(
                    connection, request_id, _safe_error_code(error)
                )
