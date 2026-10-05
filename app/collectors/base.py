"""Shared types for evidence collectors.

The central rule of this layer: **a number is only ever present when the
measurement really succeeded.** ``Reading`` enforces it, so nothing downstream
(diagnostics, UI, reports) can accidentally display an invented value.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


class Availability(Enum):
    """Whether a piece of information could be obtained, and if not, why."""

    AVAILABLE = "Available"
    UNAVAILABLE = "Unavailable"                      # we tried and failed, or got no data
    PERMISSION_RESTRICTED = "Permission restricted"  # the OS refused access
    NOT_EXPOSED = "Not exposed by hardware"          # the probe ran; the device reports no such sensor
    NOT_SUPPORTED = "Not supported on this OS"       # no collector exists for this platform


@dataclass(frozen=True)
class Reading:
    """One named measurement plus the honest state of that measurement."""

    name: str                      # dotted id, e.g. "gpu.utilization"
    status: Availability
    value: Optional[Any] = None    # set ONLY when status is AVAILABLE
    unit: str = ""
    detail: str = ""               # short human reason, e.g. "WMI returned 0 thermal zones"
    source: str = ""               # where it came from, e.g. "PDH:GPU Engine"
    taken_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.value is not None and self.status is not Availability.AVAILABLE:
            raise ValueError(f"Reading {self.name!r}: a value is only allowed when status is AVAILABLE")

    @property
    def is_available(self) -> bool:
        return self.status is Availability.AVAILABLE and self.value is not None

    @classmethod
    def available(cls, name: str, value: Any, unit: str = "", source: str = "", detail: str = "") -> "Reading":
        return cls(name, Availability.AVAILABLE, value, unit=unit, source=source, detail=detail)

    @classmethod
    def missing(
        cls, name: str, status: Availability, detail: str = "", source: str = ""
    ) -> "Reading":
        """A reading that has no value. ``status`` must not be AVAILABLE."""
        if status is Availability.AVAILABLE:
            raise ValueError("missing() needs a non-AVAILABLE status")
        return cls(name, status, None, detail=detail, source=source)


@dataclass(frozen=True)
class CollectorContext:
    """What a collector may know about the world around one collection round."""

    now: float = field(default_factory=time.time)
    previous: Mapping[str, Reading] = field(default_factory=dict)  # last round, for delta-style readings
    is_elevated: Optional[bool] = None                             # None = unknown / not applicable


@runtime_checkable
class Collector(Protocol):
    """Reads one family of evidence. Must never raise for expected failures."""

    key: str

    def collect(self, ctx: CollectorContext) -> dict[str, Reading]:
        ...
