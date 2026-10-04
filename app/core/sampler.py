"""Background sampler: collects a full system snapshot every N seconds on its own thread."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from app.core import diagnostics, system_info
from app.core.process_manager import ProcessInfo, ProcessManager
from app.core.resource_manager import MemoryInfo, ResourceMonitor
from app.core.storage_manager import StorageInfo, get_drive_usage, system_drive_path
from app.utils.constants import MIN_SAMPLE_GAP_SECONDS


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


class Sampler(threading.Thread):
    """Collects snapshots forever (until ``stop()``) and hands each to ``deliver``.

    ``deliver`` is called on THIS thread; the GUI passes a function that only puts
    the snapshot on a queue, so no widget is ever touched from here.
    """

    def __init__(self, deliver: Callable[[Snapshot], None], interval_seconds: float) -> None:
        super().__init__(name="oscope-sampler", daemon=True)
        self._deliver = deliver
        self._interval = float(interval_seconds)
        self._stop_event = threading.Event()
        self._refresh_event = threading.Event()

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

    @staticmethod
    def _collect(resources: ResourceMonitor, processes: ProcessManager) -> Snapshot:
        """Read every source; one failing source never blocks the others."""
        cpu = _safe(resources.cpu_percent)
        memory = _safe(resources.memory)
        storage = _safe(lambda: get_drive_usage(system_drive_path()))
        uptime = _safe(system_info.get_uptime_seconds)
        process_list = _safe(processes.list_processes) or []
        return Snapshot(
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
        )


def _safe(function: Callable[[], object]):
    """Call ``function``; return ``None`` instead of raising."""
    try:
        return function()
    except Exception:
        return None
