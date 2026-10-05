"""Disk throughput (read / write bytes per second) from psutil's OS disk counters."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from app.collectors.base import Availability, CollectorContext, Reading

_SOURCE = "psutil.disk_io_counters"


def _read_counters() -> Optional[Any]:
    import psutil

    return psutil.disk_io_counters()


class DiskActivityCollector:
    """``disk.read_bps`` and ``disk.write_bps``: change in the OS byte counters between two collections.

    The first collection only records a starting point, so it reports "warming up". Counters are
    whole-system totals; they say nothing about which program caused the activity.
    """

    key = "disk"

    def __init__(
        self,
        counters_fn: Callable[[], Optional[Any]] = _read_counters,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._counters_fn = counters_fn
        self._clock = clock
        self._last: Optional[tuple[float, int, int]] = None  # (time, read_bytes, write_bytes)

    def collect(self, ctx: CollectorContext) -> dict[str, Reading]:
        names = ("disk.read_bps", "disk.write_bps")
        try:
            counters = self._counters_fn()
        except Exception as exc:  # noqa: BLE001 - psutil raises OSError/RuntimeError on some systems
            return self._all_missing(names, Availability.UNAVAILABLE, f"disk counters failed ({type(exc).__name__})")
        if counters is None:
            return self._all_missing(names, Availability.UNAVAILABLE, "the OS has disk performance counters disabled")

        now = self._clock()
        current = (now, int(counters.read_bytes), int(counters.write_bytes))
        previous, self._last = self._last, current
        if previous is None:
            return self._all_missing(names, Availability.UNAVAILABLE, "warming up: needs two measurements")

        elapsed = now - previous[0]
        if elapsed <= 0 or current[1] < previous[1] or current[2] < previous[2]:
            # clock did not advance, or the counters were reset: skip this round rather than invent a rate
            return self._all_missing(names, Availability.UNAVAILABLE, "counters reset; waiting for the next measurement")
        return {
            "disk.read_bps": Reading.available("disk.read_bps", (current[1] - previous[1]) / elapsed, "B/s", _SOURCE),
            "disk.write_bps": Reading.available("disk.write_bps", (current[2] - previous[2]) / elapsed, "B/s", _SOURCE),
        }

    @staticmethod
    def _all_missing(names: tuple[str, ...], status: Availability, detail: str) -> dict[str, Reading]:
        return {name: Reading.missing(name, status, detail, _SOURCE) for name in names}
