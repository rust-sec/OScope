"""Background sampler: collects a full system snapshot every N seconds on its own thread."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Sequence

from app.collectors.base import Availability, Collector, CollectorContext, Reading
from app.collectors.registry import build_default_collectors
from app.core import diagnostics, platform_ops, system_info
from app.core.process_manager import ProcessInfo, ProcessManager
from app.core.resource_manager import MemoryInfo, ResourceMonitor
from app.core.storage_manager import StorageInfo, get_drive_usage, system_drive_path
from app.utils.constants import MIN_SAMPLE_GAP_SECONDS, SAMPLE_WINDOW_SIZE

_LOG = logging.getLogger("oscope.sampler")


@dataclass
class Snapshot:
    """Everything OScope knows about the machine at one instant."""

    taken_at: datetime
    cpu_percent: Optional[float]
    memory: Optional[MemoryInfo]
    storage: Optional[StorageInfo]
    uptime_seconds: Optional[float]
    processes: list[ProcessInfo] = field(default_factory=list)
    restricted_processes: int = 0
    findings: list[dict] = field(default_factory=list)
    readings: dict[str, Reading] = field(default_factory=dict)  # evidence from collectors, by reading name
    elevated: Optional[bool] = None                              # running as administrator? None = unknown


@dataclass(frozen=True)
class SampleRecord:
    """The few numbers from a Snapshot worth keeping for trend checks (no process list)."""

    taken_at: datetime
    cpu_percent: Optional[float]
    memory_percent: Optional[float]
    storage_percent: Optional[float]
    readings: dict[str, Reading]


class Sampler(threading.Thread):
    """Collects snapshots forever (until ``stop()``) and hands each to ``deliver``.

    ``deliver`` is called on THIS thread; the GUI passes a function that only puts
    the snapshot on a queue, so no widget is ever touched from here.

    ``collectors`` supplies extra evidence (``Snapshot.readings``); by default the
    ones registered for this platform. Tests pass fakes.
    """

    def __init__(
        self,
        deliver: Callable[[Snapshot], None],
        interval_seconds: float,
        collectors: Optional[Sequence[Collector]] = None,
        window_size: int = SAMPLE_WINDOW_SIZE,
    ) -> None:
        super().__init__(name="oscope-sampler", daemon=True)
        self._deliver = deliver
        self._interval = float(interval_seconds)
        self._stop_event = threading.Event()
        self._refresh_event = threading.Event()
        self._collectors: list[Collector] = list(build_default_collectors() if collectors is None else collectors)
        self._elevated = platform_ops.is_elevated()
        self._window: deque[SampleRecord] = deque(maxlen=window_size)
        self._window_lock = threading.Lock()
        self._previous_readings: dict[str, Reading] = {}
        self._reported_failures: set[str] = set()

    # -- control (called from the GUI thread) ---------------------------------
    def set_interval(self, seconds: float) -> None:
        self._interval = max(float(seconds), MIN_SAMPLE_GAP_SECONDS)
        self._refresh_event.set()  # wake up so the new interval applies immediately

    def request_refresh(self) -> None:
        """Take a snapshot as soon as the minimum gap since the last one has passed."""
        self._refresh_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._refresh_event.set()

    def recent_samples(self) -> list[SampleRecord]:
        """Oldest-first copy of the recent samples; safe to call from any thread."""
        with self._window_lock:
            return list(self._window)

    # -- thread body ----------------------------------------------------------
    def run(self) -> None:
        resources = ResourceMonitor()
        processes = ProcessManager()  # its constructor primes per-process CPU counters
        # CPU % needs two readings a moment apart; wait briefly so the first
        # snapshot shows a real value instead of 0%.
        if self._stop_event.wait(0.6):
            return

        while not self._stop_event.is_set():
            self._refresh_event.clear()
            started = time.monotonic()
            self._deliver(self._collect(resources, processes))

            self._refresh_event.wait(self._interval)
            if self._stop_event.is_set():
                break
            remaining = MIN_SAMPLE_GAP_SECONDS - (time.monotonic() - started)
            if remaining > 0:
                self._stop_event.wait(remaining)

    def _collect(self, resources: ResourceMonitor, processes: ProcessManager) -> Snapshot:
        """Read every source; one failing source never blocks the others."""
        cpu = self._safe_call("cpu", resources.cpu_percent)
        memory = self._safe_call("memory", resources.memory)
        storage = self._safe_call("storage", lambda: get_drive_usage(system_drive_path()))
        uptime = self._safe_call("uptime", system_info.get_uptime_seconds)
        process_list = self._safe_call("processes", processes.list_processes) or []
        readings = self._collect_readings()
        snapshot = Snapshot(
            taken_at=datetime.now(),
            cpu_percent=cpu,
            memory=memory,
            storage=storage,
            uptime_seconds=uptime,
            processes=process_list,
            restricted_processes=processes.last_restricted,
            findings=diagnostics.evaluate(
                cpu,
                memory.percent if memory else None,
                storage.percent if storage else None,
            ),
            readings=readings,
            elevated=self._elevated,
        )
        self._record(snapshot)
        return snapshot

    def _collect_readings(self) -> dict[str, Reading]:
        """Run every collector. A collector that raises yields an UNAVAILABLE reading, not a crash."""
        ctx = CollectorContext(previous=self._previous_readings, is_elevated=self._elevated)
        readings: dict[str, Reading] = {}
        for collector in self._collectors:
            try:
                readings.update(collector.collect(ctx))
            except Exception as exc:  # noqa: BLE001 - a broken collector must not stop sampling
                self._log_failure(f"collector:{collector.key}", exc)
                readings[collector.key] = Reading.missing(
                    collector.key,
                    Availability.UNAVAILABLE,
                    detail=f"collector failed ({type(exc).__name__})",
                    source=collector.key,
                )
        self._previous_readings = readings
        return readings

    def _record(self, snapshot: Snapshot) -> None:
        record = SampleRecord(
            taken_at=snapshot.taken_at,
            cpu_percent=snapshot.cpu_percent,
            memory_percent=snapshot.memory.percent if snapshot.memory else None,
            storage_percent=snapshot.storage.percent if snapshot.storage else None,
            readings=snapshot.readings,
        )
        with self._window_lock:
            self._window.append(record)

    def _safe_call(self, name: str, function: Callable[[], object]):
        """Call ``function``; on failure return ``None`` and log why (once per source, then quietly)."""
        try:
            return function()
        except Exception as exc:  # noqa: BLE001
            self._log_failure(name, exc)
            return None

    def _log_failure(self, name: str, exc: Exception) -> None:
        # First failure of a source is a warning; repeats every few seconds would flood the log.
        level = logging.DEBUG if name in self._reported_failures else logging.WARNING
        self._reported_failures.add(name)
        _LOG.log(level, "%s failed: %s: %s", name, type(exc).__name__, exc)
