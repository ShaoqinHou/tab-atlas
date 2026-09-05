from __future__ import annotations

import threading
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..browser.receiver import run_archive_cleanup, run_capture, run_mutation
from ..capture import latest_capture_rows
from ..database import open_connection, pairing_status, utc_now
from ..mutations import (
    build_archive_cleanup_plan,
    build_archive_plan,
    finalize_archive_plan,
    record_archive_cleanup_result,
    record_archive_plan,
)


TERMINAL_PHASES = {"complete", "partial", "failed"}


class TabClosureCoordinator:
    """Queue explicit resource closures through the verified extension protocol."""

    def __init__(
        self,
        database_path: Path,
        state_dir: Path,
        publish: Callable[[], None],
        *,
        operation_lock: threading.Lock | None = None,
        mutation_runner: Callable[
            ..., tuple[bool, dict[str, dict[str, Any]]]
        ] = run_mutation,
        capture_runner: Callable[
            ..., tuple[bool, dict[str, dict[str, Any]]]
        ] = run_capture,
        cleanup_runner: Callable[
            ..., tuple[bool, dict[str, dict[str, Any]]]
        ] = run_archive_cleanup,
        timeout_seconds: int = 45,
    ) -> None:
        self.database_path = database_path.resolve()
        self.state_dir = state_dir.resolve()
        self.publish = publish
        self.operation_lock = operation_lock or threading.Lock()
        self.mutation_runner = mutation_runner
        self.capture_runner = capture_runner
        self.cleanup_runner = cleanup_runner
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._resources: dict[str, set[str]] = {}
        self._queue: deque[str] = deque()
        self._worker: threading.Thread | None = None

    def start(self, resource_ids: set[str]) -> dict[str, Any]:
        selected = {str(value) for value in resource_ids if str(value)}
        if not selected:
            raise ValueError("Select at least one accepted resource to close")
        if len(selected) > 100:
            raise ValueError("Tab-close request limit exceeded")
        now = utc_now()
        job_id = f"close_{uuid.uuid4().hex[:24]}"
        job = {
            "id": job_id,
            "phase": "queued",
            "resourceCount": len(selected),
            "plannedTabs": 0,
            "closedTabs": 0,
            "skippedTabs": 0,
            "verifiedBrowsers": 0,
            "expectedBrowsers": 0,
            "startedAt": now,
            "completedAt": "",
            "error": "",
        }
        with self._lock:
            self._jobs[job_id] = job
            self._resources[job_id] = selected
            self._queue.append(job_id)
            self._trim_jobs()
            if not self._worker or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run_queue,
                    name="tabatlas-tab-closure",
                    daemon=True,
                )
                self._worker.start()
            return _public_job(job)

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise ValueError("Unknown tab-close job")
            return _public_job(job)

    def _run_queue(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    return
                job_id = self._queue.popleft()
                resource_ids = set(self._resources.get(job_id) or set())
            with self.operation_lock:
                self._run(job_id, resource_ids)

    def _run(self, job_id: str, resource_ids: set[str]) -> None:
        try:
            self._set(job_id, phase="planning")
            connection = open_connection(self.database_path)
            try:
                captures = {
                    row["browser"]: row for row in latest_capture_rows(connection)
                }
                paired = {
                    item["browser"]
                    for item in pairing_status(connection)
                    if item.get("enabled")
                }
                browsers = _browsers_with_resources(connection, captures, resource_ids)
                if not browsers:
                    self._finish(job_id, "complete")
                    return
                unavailable = browsers - paired
                if unavailable:
                    names = ", ".join(
                        browser.title() for browser in sorted(unavailable)
                    )
                    verb = "is" if len(unavailable) == 1 else "are"
                    self._finish(
                        job_id,
                        "failed",
                        f"{names} {verb} not paired or the TabAtlas extension is off. The page was saved and its browser tab was left open.",
                    )
                    return
                capture_ids = {
                    browser: str(captures[browser]["id"]) for browser in browsers
                }
                plan = build_archive_plan(
                    connection,
                    browsers,
                    capture_ids,
                    resource_ids=resource_ids,
                )
                planned = int(plan["summary"]["plannedClosures"])
                self._set(
                    job_id,
                    plannedTabs=planned,
                    expectedBrowsers=sum(
                        bool(item["targets"]) for item in plan["browsers"].values()
                    ),
                )
                if not planned:
                    self._finish(job_id, "complete")
                    return
                record_archive_plan(
                    connection,
                    self.state_dir,
                    plan,
                    "Workspace user selected Add and close for explicit resources.",
                )
            finally:
                connection.close()

            self._set(job_id, phase="waiting_for_extension")
            mutation_complete, results = self.mutation_runner(
                self.database_path,
                self.state_dir,
                plan,
                self.timeout_seconds,
            )
            affected = {
                browser
                for browser, browser_plan in plan["browsers"].items()
                if browser_plan["targets"]
            }
            self._set(job_id, phase="verifying")
            post_complete = False
            post_captures: dict[str, dict[str, Any]] = {}
            if results:
                post_complete, post_captures = self.capture_runner(
                    self.database_path,
                    self.state_dir,
                    affected,
                    self.timeout_seconds,
                )
            error = ""
            if not mutation_complete:
                error = (
                    "The browser extension did not finish the close request in time."
                )
            elif not post_complete:
                error = "The tab was closed, but the browser did not finish verification in time."

            connection = open_connection(self.database_path)
            try:
                totals = finalize_archive_plan(
                    connection,
                    self.state_dir,
                    plan,
                    results,
                    post_captures,
                    error,
                )
            finally:
                connection.close()

            self._set(
                job_id,
                closedTabs=int(totals["closed"]),
                skippedTabs=int(totals["skipped"]),
                verifiedBrowsers=int(totals["verifiedBrowsers"]),
                expectedBrowsers=int(totals["expectedBrowsers"]),
            )
            cleanup_complete = not totals["controls"]
            if totals["controls"]:
                self._set(job_id, phase="cleaning")
                cleanup_plan = build_archive_cleanup_plan(plan, totals["controls"])
                cleanup_received, cleanup_results = self.cleanup_runner(
                    self.database_path,
                    self.state_dir,
                    cleanup_plan,
                    self.timeout_seconds,
                )
                cleanup_complete = cleanup_received and all(
                    result.get("status") == "closed"
                    for result in cleanup_results.values()
                )
                record_archive_cleanup_result(
                    Path(totals["evidencePath"]),
                    cleanup_plan,
                    cleanup_complete,
                    cleanup_results,
                )

            complete = (
                mutation_complete
                and post_complete
                and totals["skipped"] == 0
                and totals["verifiedBrowsers"] == totals["expectedBrowsers"]
                and totals["allControlsReported"]
                and cleanup_complete
            )
            if not complete and not error:
                error = "One or more browser tabs changed before they could be closed safely."
            self.publish()
            self._finish(job_id, "complete" if complete else "partial", error)
        except Exception as error:
            self._finish(job_id, "failed", _public_error(error))

    def _set(self, job_id: str, **values: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(values)

    def _finish(self, job_id: str, phase: str, error: str = "") -> None:
        self._set(
            job_id,
            phase=phase,
            completedAt=utc_now(),
            error=error[:240],
        )

    def _trim_jobs(self) -> None:
        removable = [
            job_id
            for job_id, job in self._jobs.items()
            if job["phase"] in TERMINAL_PHASES and job_id not in self._queue
        ]
        while len(self._jobs) > 50 and removable:
            job_id = removable.pop(0)
            self._jobs.pop(job_id, None)
            self._resources.pop(job_id, None)


def _browsers_with_resources(
    connection: Any,
    captures: dict[str, dict[str, Any]],
    resource_ids: set[str],
) -> set[str]:
    placeholders = ",".join("?" for _ in resource_ids)
    browsers: set[str] = set()
    for browser, capture in captures.items():
        present = connection.execute(
            f"SELECT 1 FROM tab_instances WHERE capture_id=? "
            f"AND resource_id IN ({placeholders}) LIMIT 1",
            [capture["id"], *sorted(resource_ids)],
        ).fetchone()
        if present:
            browsers.add(browser)
    return browsers


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(job.get("id") or ""),
        "phase": str(job.get("phase") or "failed"),
        "resourceCount": int(job.get("resourceCount") or 0),
        "plannedTabs": int(job.get("plannedTabs") or 0),
        "closedTabs": int(job.get("closedTabs") or 0),
        "skippedTabs": int(job.get("skippedTabs") or 0),
        "verifiedBrowsers": int(job.get("verifiedBrowsers") or 0),
        "expectedBrowsers": int(job.get("expectedBrowsers") or 0),
        "startedAt": str(job.get("startedAt") or ""),
        "completedAt": str(job.get("completedAt") or ""),
        "error": str(job.get("error") or ""),
    }


def _public_error(error: BaseException) -> str:
    if isinstance(error, ValueError):
        return str(error)[:240]
    return "The verified browser close could not be completed."
