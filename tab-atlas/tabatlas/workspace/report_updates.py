from __future__ import annotations

import threading
from collections.abc import Callable


class DeferredReportPublisher:
    """Coalesce static report writes without delaying interactive API responses."""

    def __init__(
        self, publish: Callable[[], None], delay_seconds: float = 0.25
    ) -> None:
        self.publish = publish
        self.delay_seconds = delay_seconds
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def schedule(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            timer = threading.Timer(self.delay_seconds, self._run)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def close(self) -> None:
        with self._lock:
            timer = self._timer
            self._timer = None
        if timer:
            timer.cancel()

    def _run(self) -> None:
        with self._lock:
            self._timer = None
        self.publish()
