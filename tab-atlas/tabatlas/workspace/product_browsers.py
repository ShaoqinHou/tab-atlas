from __future__ import annotations

import csv
import os
import shutil
import subprocess
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..common import normalize_browser
from ..constants import TABATLAS_EXTENSION_ID


BrowserLaunchManager = Any


@contextmanager
def no_product_browser_launch(_browsers: Iterable[str]) -> Iterator[dict[str, Any]]:
    yield {}


@contextmanager
def launch_closed_product_browsers(
    browsers: Iterable[str],
) -> Iterator[dict[str, Any]]:
    if os.name != "nt":
        yield {}
        return

    launched: dict[str, dict[str, Any]] = {}
    try:
        for browser in sorted({normalize_browser(browser) for browser in browsers}):
            spec = _browser_spec(browser)
            if not spec:
                continue
            before = _process_ids(spec["image"])
            if before:
                launched[browser] = {"launched": False, "reason": "already_running"}
                continue
            executable = _browser_executable(spec)
            if not executable:
                launched[browser] = {"launched": False, "reason": "not_found"}
                continue
            try:
                subprocess.Popen(
                    [
                        str(executable),
                        "--start-minimized",
                        "--no-first-run",
                        "--disable-default-browser-check",
                        "--restore-last-session",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(3)
                subprocess.Popen(
                    [
                        str(executable),
                        f"chrome-extension://{TABATLAS_EXTENSION_ID}/popup.html?capture=1",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(1)
                launched[browser] = {
                    "launched": True,
                    "image": spec["image"],
                    "before": sorted(before),
                    "pids": sorted(_process_ids(spec["image"]) - before),
                }
            except OSError:
                launched[browser] = {"launched": False, "reason": "launch_failed"}
        yield launched
    finally:
        for browser, details in launched.items():
            if details.get("launched"):
                spec = _browser_spec(browser)
                if spec:
                    _close_new_processes(
                        spec["image"], set(details.get("before") or [])
                    )


def _browser_spec(browser: str) -> dict[str, Any] | None:
    if browser == "chrome":
        return {
            "image": "chrome.exe",
            "commands": ["chrome"],
            "paths": [
                Path(os.environ.get("ProgramFiles", ""))
                / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("ProgramFiles(x86)", ""))
                / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("LocalAppData", ""))
                / "Google/Chrome/Application/chrome.exe",
            ],
        }
    if browser == "edge":
        return {
            "image": "msedge.exe",
            "commands": ["msedge"],
            "paths": [
                Path(os.environ.get("ProgramFiles(x86)", ""))
                / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("ProgramFiles", ""))
                / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("LocalAppData", ""))
                / "Microsoft/Edge/Application/msedge.exe",
            ],
        }
    return None


def _browser_executable(spec: dict[str, Any]) -> Path | None:
    for command in spec["commands"]:
        resolved = shutil.which(command)
        if resolved:
            return Path(resolved)
    for candidate in spec["paths"]:
        if candidate.is_file():
            return candidate
    return None


def _process_ids(image_name: str) -> set[int]:
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    ids: set[int] = set()
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) >= 2 and row[0].lower() == image_name.lower():
            try:
                ids.add(int(row[1]))
            except ValueError:
                pass
    return ids


def _close_new_processes(image_name: str, before: set[int]) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        current = _process_ids(image_name) - before
        if not current:
            return
        for pid in sorted(current):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        time.sleep(1)
    for pid in sorted(_process_ids(image_name) - before):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
