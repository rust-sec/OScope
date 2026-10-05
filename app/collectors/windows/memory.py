"""Memory pressure evidence beyond "percent used": commit charge and paging activity."""

from __future__ import annotations

from typing import Callable, Optional

from app.collectors.base import Availability, CollectorContext, Reading
from app.collectors.windows.background import LatestSource
from app.collectors.windows.typeperf import CounterSample

PAGES_PER_SEC_COUNTER = r"\Memory\Pages/sec"


class MemoryCollector:
    """Commit charge (promised memory vs. its limit) and Pages/sec (hard paging).

    * ``mem.commit_percent`` / ``mem.commit_used_bytes`` / ``mem.commit_limit_bytes``
      from ``GetPerformanceInfo``.
    * ``mem.pages_per_sec`` from the PDH counter ``\\Memory\\Pages/sec`` (a measure of
      reads/writes to the pagefile to resolve hard faults).
    """

    key = "memory"

    def __init__(
        self,
        commit_fn: Callable[[], Optional[tuple[int, int]]],
        pages_source: Optional[LatestSource[CounterSample]] = None,
    ) -> None:
        self._commit_fn = commit_fn
        self._pages = pages_source

    def collect(self, ctx: CollectorContext) -> dict[str, Reading]:
        readings: dict[str, Reading] = {}
        source = "GetPerformanceInfo"

        commit = self._commit_fn()
        if commit is None:
            for name in ("mem.commit_percent", "mem.commit_used_bytes", "mem.commit_limit_bytes"):
                readings[name] = Reading.missing(name, Availability.UNAVAILABLE, "commit charge could not be read", source)
        else:
            used, limit = commit
            readings["mem.commit_used_bytes"] = Reading.available("mem.commit_used_bytes", used, "bytes", source)
            readings["mem.commit_limit_bytes"] = Reading.available("mem.commit_limit_bytes", limit, "bytes", source)
            readings["mem.commit_percent"] = Reading.available(
                "mem.commit_percent", used / limit * 100.0, "%", source
            )

        readings["mem.pages_per_sec"] = self._pages_reading()
        return readings

    def _pages_reading(self) -> Reading:
        name, source = "mem.pages_per_sec", "PDH:Memory\\Pages/sec"
        if self._pages is None:
            return Reading.missing(name, Availability.NOT_SUPPORTED, "no performance-counter source", source)
        outcome = self._pages.latest()
        if outcome is None:
            return Reading.missing(name, Availability.UNAVAILABLE, "first measurement still running", source)
        if outcome.value is None:
            return Reading.missing(name, Availability.UNAVAILABLE, outcome.error or "unavailable", source)
        value = outcome.value.values.get(PAGES_PER_SEC_COUNTER)
        if value is None:
            return Reading.missing(name, Availability.UNAVAILABLE, "counter missing from typeperf output", source)
        return Reading.available(name, value, "pages/s", source)
