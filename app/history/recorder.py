"""Turn the live stream of snapshots (every ~2 s) into a few rows (one per ~30 s) worth remembering."""

from __future__ import annotations

import time
from typing import Callable, Optional

from app.core.sampler import Snapshot
from app.history.records import MetricRow, Operation, ProcessTopRow, insert_metric, insert_process_top

METRIC_INTERVAL_SECONDS = 30
PROCESS_EVERY_N_METRICS = 2       # top programs are kept every other metric row (~60 s)
TOP_PROGRAMS_KEPT = 10


class HistoryRecorder:
    """Averages snapshots over a window and hands finished rows to ``sink`` as writer operations.

    Pure bookkeeping: no I/O, no threads. ``sink`` receives callables that the history writer thread
    runs, so the GUI thread never touches SQLite.
    """

    def __init__(
        self,
        sink: Callable[[Operation], None],
        clock: Callable[[], float] = time.time,
        interval: float = METRIC_INTERVAL_SECONDS,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._interval = interval
        self._window_start: Optional[float] = None
        self._rows_written = 0
        self._reset()

    def _reset(self) -> None:
        self._cpu: list[float] = []
        self._mem: list[float] = []
        self._commit: list[float] = []
        self._read: list[float] = []
        self._write: list[float] = []
        self._gpu: list[float] = []
        self._temp: list[float] = []
        self._on_battery: Optional[bool] = None
        self._storage = None
        self._groups: list = []
        self._count = 0

    def on_snapshot(self, snapshot: Snapshot) -> None:
        now = self._clock()
        if self._window_start is None:
            self._window_start = now
        self._add(snapshot)
        if now - self._window_start >= self._interval:
            self._flush(int(now))
            self._window_start = now

    def _add(self, snap: Snapshot) -> None:
        self._count += 1
        if snap.cpu_percent is not None:
            self._cpu.append(snap.cpu_percent)
        if snap.memory is not None:
            self._mem.append(snap.memory.percent)
        if snap.storage is not None:
            self._storage = snap.storage
        self._groups = snap.process_groups
        for bucket, name in (
            (self._commit, "mem.commit_percent"), (self._read, "disk.read_bps"), (self._write, "disk.write_bps"),
            (self._gpu, "gpu.utilization"), (self._temp, "temp.acpi_max"),
        ):
            reading = snap.readings.get(name)
            if reading is not None and reading.is_available:
                bucket.append(float(reading.value))
        battery = snap.readings.get("power.on_battery")
        if battery is not None and battery.is_available:
            self._on_battery = bool(battery.value)

    def _flush(self, ts: int) -> None:
        if not self._count:
            return
        row = MetricRow(
            ts=ts,
            cpu=_mean(self._cpu),
            mem_pct=_mean(self._mem),
            commit_pct=_mean(self._commit),
            disk_read_bps=_mean(self._read),
            disk_write_bps=_mean(self._write),
            gpu_pct=_mean(self._gpu),
            temp_c=max(self._temp) if self._temp else None,  # the hottest reading in the window
            on_battery=self._on_battery,
            free_bytes=self._storage.free if self._storage else None,
            total_bytes=self._storage.total if self._storage else None,
            samples=self._count,
        )
        self._sink(lambda conn, r=row: insert_metric(conn, r))
        self._rows_written += 1

        if self._rows_written % PROCESS_EVERY_N_METRICS == 0 and self._groups:
            top = sorted(self._groups, key=lambda g: g.memory_bytes, reverse=True)[:TOP_PROGRAMS_KEPT]
            rows = [
                ProcessTopRow(ts, rank, g.display, g.count, g.cpu_percent, g.memory_bytes)
                for rank, g in enumerate(top, start=1)
            ]
            self._sink(lambda conn, rs=rows: insert_process_top(conn, rs))
        self._reset()


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None
