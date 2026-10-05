"""``python main.py --probe``: print what every collector reports on THIS machine.

Run it on a real Windows PC and paste the output when something looks wrong (or to turn
real samples into test fixtures). Read-only, takes a few seconds.
"""

from __future__ import annotations

import sys
import time
from typing import Optional, Sequence, TextIO

from app.collectors.base import Availability, Collector, CollectorContext, Reading
from app.collectors.registry import build_default_collectors
from app.core import platform_ops


def run_probe(
    collectors: Optional[Sequence[Collector]] = None,
    out: TextIO = sys.stdout,
    rounds: int = 3,
    pause: float = 2.5,
) -> int:
    """Collect ``rounds`` times (rate-style readings need two; slow probes need a moment) and print the last."""
    chosen = list(build_default_collectors() if collectors is None else collectors)
    elevated = platform_ops.is_elevated()
    readings: dict[str, Reading] = {}
    for round_number in range(rounds):
        if round_number:
            time.sleep(pause)
        ctx = CollectorContext(previous=readings, is_elevated=elevated)
        readings = {}
        for collector in chosen:
            try:
                readings.update(collector.collect(ctx))
            except Exception as exc:  # noqa: BLE001
                readings[collector.key] = Reading.missing(
                    collector.key, Availability.UNAVAILABLE, f"collector raised {type(exc).__name__}: {exc}"
                )

    print("OScope probe", file=out)
    print(f"Elevated: {'unknown' if elevated is None else elevated}", file=out)
    print(f"Collectors: {', '.join(c.key for c in chosen) or '(none)'}", file=out)
    print("", file=out)
    for name in sorted(readings):
        reading = readings[name]
        value = "" if reading.value is None else _short(reading.value)
        unit = f" {reading.unit}" if reading.unit and reading.value is not None else ""
        print(f"{name:<24} {reading.status.value:<24} {value}{unit}", file=out)
        if reading.detail or reading.source:
            print(f"{'':<24} {'':<24} [{reading.source}] {reading.detail}", file=out)
    return 0


def _short(value: object, limit: int = 120) -> str:
    text = repr(value) if isinstance(value, (tuple, list)) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
