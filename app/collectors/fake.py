"""Scriptable collector for tests and developer runs on machines without the real sensors."""

from __future__ import annotations

from typing import Mapping, Optional

from app.collectors.base import CollectorContext, Reading


class FakeCollector:
    """Returns fixed readings, or raises ``error`` to simulate a broken collector."""

    def __init__(
        self,
        key: str = "fake",
        readings: Optional[Mapping[str, Reading]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        self.key = key
        self._readings = dict(readings or {})
        self._error = error
        self.calls = 0

    def collect(self, ctx: CollectorContext) -> dict[str, Reading]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return dict(self._readings)
