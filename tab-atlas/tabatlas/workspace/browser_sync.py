from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..browser.receiver import run_capture
from ..catalog import inventory
from ..common import normalize_browser
from ..database import open_connection, pairing_status, utc_now


CaptureRunner = Callable[
    [Path, Path, set[str], int], tuple[bool, dict[str, dict[str, Any]]]
]


class BrowserSyncCoordinator:
    """Own one bounded capture job and expose aggregate, privacy-safe progress."""

    def __init__(
        self,
        database_path: Path,
        state_dir: Path,
        publish: Callable[[], None],
        *,
        capture_runner: CaptureRunner = run_capture,
        timeout_seconds: int = 45,
    ) -> None:
        self.database_path = database_path.resolve()
        self.state_dir = state_dir.resolve()
        self.publish = publish
        self.capture_runner = capture_runner
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._job = self._idle_job()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return _public_job(self._job)

    def start(
        self,
        browsers: Iterable[str] = ("chrome", "edge"),
        *,
        trigger: str = "user",
    ) -> dict[str, Any]:
        requested = {
            normalize_browser(browser)
            for browser in browsers
            if normalize_browser(browser) in {"chrome", "edge"}
        }
        if not requested:
            raise ValueError("Select Chrome, Edge, or both")

        connection = open_connection(self.database_path)
        try:
            paired = {
                item["browser"]
                for item in pairing_status(connection)
                if item.get("enabled")
            }
        finally:
            connection.close()
        targets = requested & paired
        with self._lock:
            if self._job["phase"] in {
                "starting",
                "waiting_for_extension",
                "publishing",
            }:
                reused = _public_job(self._job)
                reused["reused"] = True
                return reused
            now = utc_now()
            self._job = {
                "id": f"sync_{uuid.uuid4().hex[:24]}",
                "phase": "starting" if targets else "failed",
                "trigger": trigger
                if trigger in {"workspace_start", "user", "agent"}
                else "user",
                "requestedBrowsers": sorted(requested),
                "startedAt": now,
                "completedAt": now if not targets else "",
                "browsers": {
                    browser: {
                        "state": "waiting" if browser in targets else "unavailable",
                        "tabs": 0,
                        "resources": 0,
                        "newResources": 0,
                    }
                    for browser in sorted(requested)
                },
                "newResources": 0,
                "pendingDiscoveries": 0,
                "error": "No requested browser is paired" if not targets else "",
            }
            result = _public_job(self._job)

        if targets:
            thread = threading.Thread(
                target=self._run,
                args=(targets,),
                name="tabatlas-browser-sync",
                daemon=True,
            )
            thread.start()
        return result

    def _run(self, targets: set[str]) -> None:
        self._set_phase("waiting_for_extension")
        try:
            complete, captured = self.capture_runner(
                self.database_path,
                self.state_dir,
                targets,
                self.timeout_seconds,
            )
            self._set_phase("publishing")
            self.publish()
            connection = open_connection(self.database_path)
            try:
                pending = int(inventory(connection)["pendingDiscoveries"])
            finally:
                connection.close()

            with self._lock:
                new_resources = 0
                for browser in targets:
                    result = captured.get(browser)
                    target = self._job["browsers"][browser]
                    if result:
                        target.update(
                            {
                                "state": "captured",
                                "tabs": int(result.get("tab_count", 0)),
                                "resources": int(result.get("resource_count", 0)),
                                "newResources": int(
                                    result.get("candidate_resource_count", 0)
                                ),
                            }
                        )
                        new_resources += target["newResources"]
                    else:
                        target["state"] = "timed_out"
                captured_count = len(captured)
                all_requested_captured = all(
                    browser_status["state"] == "captured"
                    for browser_status in self._job["browsers"].values()
                )
                self._job["phase"] = (
                    "complete"
                    if complete and all_requested_captured
                    else "partial"
                    if captured_count
                    else "failed"
                )
                self._job["newResources"] = new_resources
                self._job["pendingDiscoveries"] = pending
                self._job["completedAt"] = utc_now()
                if not captured_count:
                    self._job["error"] = "No browser extension answered before timeout"
        except Exception as error:
            with self._lock:
                self._job["phase"] = "failed"
                self._job["completedAt"] = utc_now()
                self._job["error"] = _safe_error(error)

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._job["phase"] = phase

    @staticmethod
    def _idle_job() -> dict[str, Any]:
        return {
            "id": "",
            "phase": "idle",
            "trigger": "",
            "requestedBrowsers": [],
            "startedAt": "",
            "completedAt": "",
            "browsers": {},
            "newResources": 0,
            "pendingDiscoveries": 0,
            "error": "",
        }


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(job.get("id") or ""),
        "phase": str(job.get("phase") or "idle"),
        "trigger": str(job.get("trigger") or ""),
        "requestedBrowsers": list(job.get("requestedBrowsers") or []),
        "startedAt": str(job.get("startedAt") or ""),
        "completedAt": str(job.get("completedAt") or ""),
        "browsers": {
            browser: dict(status)
            for browser, status in (job.get("browsers") or {}).items()
        },
        "newResources": int(job.get("newResources") or 0),
        "pendingDiscoveries": int(job.get("pendingDiscoveries") or 0),
        "error": str(job.get("error") or ""),
    }


def _safe_error(error: BaseException) -> str:
    name = type(error).__name__.replace("Error", "").strip().lower()
    return name or "browser_sync_failed"
