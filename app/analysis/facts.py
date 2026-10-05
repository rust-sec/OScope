"""The data a diagnostic looks at: the latest snapshot plus the recent window, with safe accessors."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.analysis import trends
from app.analysis.grouping import BROWSER_KEYS, ProcessGroup
from app.analysis.trends import MetricState
from app.collectors.base import Reading
from app.core.sampler import SampleRecord, Snapshot
from app.history.queries import HistoryContext

MIN_HISTORY_ROWS = 4  # fewer recorded measurements than this is not enough to say anything about change


class Facts:
    """Read-only view over one snapshot and its history. Nothing here measures anything new."""

    def __init__(
        self,
        snapshot: Snapshot,
        window: Sequence[SampleRecord],
        workload: str = "general",
        history: Optional[HistoryContext] = None,
    ) -> None:
        self.snapshot = snapshot
        self.window = list(window)
        self.workload = workload
        self.history = history
        self._states: dict[tuple[str, float], MetricState] = {}

    # -- history -----------------------------------------------------------------
    @property
    def samples(self) -> int:
        return len(self.window)

    @property
    def seconds(self) -> float:
        """Length of the history in seconds."""
        if len(self.window) < 2:
            return 0.0
        return max((self.window[-1].taken_at - self.window[0].taken_at).total_seconds(), 0.0)

    def state(self, key: str, threshold: float) -> MetricState:
        cache_key = (key, threshold)
        if cache_key not in self._states:
            self._states[cache_key] = trends.assess(self.window, key, threshold)
        return self._states[cache_key]

    def basis_text(self, state: MetricState) -> str:
        """Phrase saying what a statement rests on: recent history or just this moment."""
        if state.basis == "sustained":
            return f"over the last {self.seconds:.0f} seconds"
        return "right now (OScope has only just started collecting)"

    # -- readings ----------------------------------------------------------------
    @property
    def readings(self) -> dict[str, Reading]:
        return self.snapshot.readings

    def reading(self, name: str) -> Optional[Reading]:
        """The reading if it is AVAILABLE, else None."""
        reading = self.snapshot.readings.get(name)
        return reading if reading is not None and reading.is_available else None

    def value(self, name: str) -> Optional[Any]:
        reading = self.reading(name)
        return reading.value if reading is not None else None

    def has(self, token: str) -> bool:
        """True when the named input exists: a snapshot field or an AVAILABLE reading."""
        snap = self.snapshot
        if token == "cpu":
            return snap.cpu_percent is not None
        if token == "memory":
            return snap.memory is not None
        if token == "storage":
            return snap.storage is not None
        if token == "groups":
            return bool(snap.process_groups)
        if token == "history":
            return self.history is not None and len(self.history.metrics) >= MIN_HISTORY_ROWS
        return self.reading(token) is not None

    # -- programs ----------------------------------------------------------------
    @property
    def groups(self) -> list[ProcessGroup]:
        return self.snapshot.process_groups

    def group(self, key: str) -> Optional[ProcessGroup]:
        return next((g for g in self.groups if g.key == key), None)

    def browser_groups(self) -> list[ProcessGroup]:
        return [g for g in self.groups if g.key in BROWSER_KEYS]

    def top_by_memory(self, count: int, include_system: bool = True) -> list[ProcessGroup]:
        pool = [g for g in self.groups if include_system or not g.is_system]
        return sorted(pool, key=lambda g: g.memory_bytes, reverse=True)[:count]

    def top_by_cpu(self, count: int, minimum: float = 0.0) -> list[ProcessGroup]:
        pool = [g for g in self.groups if g.cpu_percent >= minimum]
        return sorted(pool, key=lambda g: g.cpu_percent, reverse=True)[:count]
