"""Keep the latest result of a slow probe (typeperf, PowerShell) without ever blocking the sampler.

Spawning ``powershell.exe`` or ``typeperf.exe`` takes from a fraction of a second to a few
seconds. The Sampler must not wait for that every cycle, so a probe runs on its own daemon
thread every ``interval`` seconds and collectors just read ``latest()``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, Optional, Protocol, TypeVar

T = TypeVar("T")
_LOG = logging.getLogger("oscope.probe")


@dataclass(frozen=True)
class ProbeOutcome(Generic[T]):
    """What one run of a probe produced: a value, or the reason it failed."""

    value: Optional[T]
    error: Optional[str]
    taken_at: float


class LatestSource(Protocol[T]):
    def latest(self) -> Optional[ProbeOutcome[T]]:
        ...


class ProbeError(Exception):
    """Raised by a probe function for an expected failure; the message is shown as the reason."""


class BackgroundProbe(Generic[T]):
    """Runs ``function`` repeatedly on a daemon thread; ``latest()`` never blocks."""

    def __init__(
        self,
        function: Callable[[], T],
        interval: float,
        name: str = "probe",
        autostart: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._function = function
        self._interval = interval
        self._name = name
        self._autostart = autostart
        self._clock = clock
        self._lock = threading.Lock()
        self._outcome: Optional[ProbeOutcome[T]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def refresh(self) -> ProbeOutcome[T]:
        """Run the probe once, right now, on the calling thread, and store the outcome."""
        try:
            outcome: ProbeOutcome[T] = ProbeOutcome(self._function(), None, self._clock())
        except ProbeError as exc:
            outcome = ProbeOutcome(None, str(exc), self._clock())
        except Exception as exc:  # noqa: BLE001 - a probe must never take the app down
            _LOG.warning("%s probe failed: %s: %s", self._name, type(exc).__name__, exc)
            outcome = ProbeOutcome(None, f"{type(exc).__name__}", self._clock())
        with self._lock:
            self._outcome = outcome
        return outcome

    def latest(self) -> Optional[ProbeOutcome[T]]:
        """Most recent outcome, or None if the first run has not finished yet."""
        if self._autostart:
            self._ensure_started()
        with self._lock:
            return self._outcome

    def close(self) -> None:
        self._stop.set()

    def _ensure_started(self) -> None:
        if self._thread is not None or self._stop.is_set():
            return
        self._thread = threading.Thread(target=self._loop, name=f"oscope-{self._name}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            if self._stop.wait(self._interval):
                break
