from __future__ import annotations

import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, ContextManager

from .context import _safe_error_code
from .server_constants import TRANSCRIPTION_TIMEOUT_SECONDS
from .transcription import (
    audio_note_source,
    complete_audio_transcription,
    fail_audio_transcription,
    mark_audio_transcription_running,
    queued_audio_transcriptions,
)

DatabaseFactory = Callable[[], ContextManager[sqlite3.Connection]]
Transcriber = Callable[[Path], dict[str, str]]


class AudioTranscriptionWorker:
    def __init__(
        self,
        workspace_root: Path,
        state_dir: Path,
        database: DatabaseFactory,
        transcribe: Transcriber,
        stop_event: threading.Event,
    ) -> None:
        self.queue: queue.Queue[str | None] = queue.Queue()
        self.queued_ids: set[str] = set()
        self.queued_lock = threading.Lock()
        self.thread = threading.Thread(
            target=self.run,
            name="tabatlas-transcription-worker",
            daemon=True,
        )
        self._workspace_root = workspace_root
        self._state_dir = state_dir
        self._database = database
        self._transcribe = transcribe
        self._stop_event = stop_event

    def start(self) -> None:
        self.thread.start()

    def recover(self) -> None:
        with self._database() as connection:
            notes = queued_audio_transcriptions(connection, 100)
        for note in notes:
            self.enqueue(note["id"])

    def close(self) -> None:
        self.queue.put(None)

    def enqueue(self, note_id: str) -> None:
        with self.queued_lock:
            if note_id in self.queued_ids:
                return
            self.queued_ids.add(note_id)
        self.queue.put(note_id)

    def run(self) -> None:
        while not self._stop_event.is_set():
            note_id = self.queue.get()
            if note_id is None:
                return
            try:
                self.process(note_id)
            finally:
                with self.queued_lock:
                    self.queued_ids.discard(note_id)

    def process(self, note_id: str) -> None:
        try:
            with self._database() as connection:
                note = mark_audio_transcription_running(connection, note_id)
                if (
                    note.get("processing", {}).get("transcription", {}).get("state")
                    == "succeeded"
                ):
                    return
                source = audio_note_source(connection, self._state_dir, note_id)
            result = self._transcribe(source["path"])
            with self._database() as connection:
                complete_audio_transcription(
                    connection,
                    note_id,
                    result["text"],
                    result["model"],
                )
        except Exception as error:
            if self._stop_event.is_set():
                return
            with self._database() as connection:
                try:
                    fail_audio_transcription(
                        connection, note_id, _safe_error_code(error)
                    )
                except ValueError:
                    pass

    def run_local_transcriber(self, audio_path: Path) -> dict[str, str]:
        script = self._workspace_root / "scripts" / "tab_atlas_transcribe.py"
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
            cwd=str(self._workspace_root),
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
