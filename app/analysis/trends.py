"""Look at a short history of samples instead of a single instant.

"High" in OScope means high for most of the recent window, so one spike never raises a flag.
With too few samples we say so and fall back to the current value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from app.core.sampler import SampleRecord

MIN_SUSTAINED_SAMPLES = 5   # fewer than this: not enough history to call anything "sustained"
SUSTAINED_FRACTION = 0.6    # share of recent samples that must be over the threshold


@dataclass(frozen=True)
class MetricState:
    latest: Optional[float]
    mean: Optional[float]
    maximum: Optional[float]
    samples: int
    high: bool
    basis: str  # "sustained" (enough history) | "now" (too little history) | "none" (no data)


def series(window: Sequence[SampleRecord], key: str) -> list[float]:
    """Values of one metric across the window, oldest first, skipping samples without a value.

    Keys: ``cpu``, ``mem``, ``storage`` (percentages), ``disk.total_bps`` (read + write),
    or the name of any collector reading, e.g. ``gpu.utilization``.
    """
    values: list[float] = []
    for record in window:
        value = _value(record, key)
        if value is not None:
            values.append(value)
    return values


def assess(window: Sequence[SampleRecord], key: str, threshold: float) -> MetricState:
    values = series(window, key)
    if not values:
        return MetricState(None, None, None, 0, False, "none")
    mean = sum(values) / len(values)
    if len(values) >= MIN_SUSTAINED_SAMPLES:
        over = sum(1 for v in values if v >= threshold) / len(values)
        return MetricState(values[-1], mean, max(values), len(values), over >= SUSTAINED_FRACTION, "sustained")
    return MetricState(values[-1], mean, max(values), len(values), values[-1] >= threshold, "now")


def _value(record: SampleRecord, key: str) -> Optional[float]:
    if key == "cpu":
        return record.cpu_percent
    if key == "mem":
        return record.memory_percent
    if key == "storage":
        return record.storage_percent
    if key == "disk.total_bps":
        read, write = record.readings.get("disk.read_bps"), record.readings.get("disk.write_bps")
        if read is not None and write is not None and read.is_available and write.is_available:
            return float(read.value) + float(write.value)
        return None
    reading = record.readings.get(key)
    if reading is not None and reading.is_available and isinstance(reading.value, (int, float)) \
            and not isinstance(reading.value, bool):
        return float(reading.value)
    return None
