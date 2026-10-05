"""GPU load from the Windows "GPU Engine" performance counters (the data Task Manager shows).

How the number is built (so it matches Task Manager's definition, and is not invented):
each counter instance is one process on one GPU engine, named like
``pid_1234_luid_0x00000000_0x0000F2A1_phys_0_eng_0_engtype_3D``. Processes share an engine,
so their percentages on the SAME engine add up (never above 100). The headline figure is the
busiest engine across all GPUs. It is NOT an average over engines and not "total GPU use".
Requires a WDDM 2.0+ driver (Windows 10 and later with a normal GPU driver).
"""

from __future__ import annotations

import re
from typing import Optional

from app.collectors.base import Availability, CollectorContext, Reading
from app.collectors.windows.background import LatestSource
from app.collectors.windows.cmd import Runner, run_command
from app.collectors.windows.typeperf import CounterSample, make_counter_reader

GPU_COUNTER = r"\GPU Engine(*)\Utilization Percentage"

_COLUMN = re.compile(r"^\\GPU Engine\((?P<instance>[^)]*)\)\\Utilization Percentage$", re.IGNORECASE)
_INSTANCE = re.compile(
    r"^pid_(?P<pid>\d+)_luid_(?P<luid>0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_(?P<phys>\d+)"
    r"_eng_(?P<eng>\d+)_engtype_(?P<engtype>.+)$"
)


def make_gpu_reader(runner: Runner = run_command):
    """Function (for a BackgroundProbe) that reads every GPU Engine counter once."""
    return make_counter_reader(GPU_COUNTER, runner)


def busiest_engine(sample: CounterSample) -> Optional[tuple[float, str]]:
    """``(percent, engine_type)`` of the busiest GPU engine, or None if no engine data exists."""
    per_engine: dict[tuple[str, str, str], float] = {}
    engine_type: dict[tuple[str, str, str], str] = {}
    for path, value in sample.values.items():
        column = _COLUMN.match(path)
        if column is None:
            continue
        instance = _INSTANCE.match(column.group("instance"))
        if instance is None:
            continue
        engine = (instance.group("luid"), instance.group("phys"), instance.group("eng"))
        per_engine[engine] = per_engine.get(engine, 0.0) + max(value, 0.0)
        engine_type[engine] = instance.group("engtype")
    if not per_engine:
        return None
    engine = max(per_engine, key=lambda key: per_engine[key])
    return min(per_engine[engine], 100.0), engine_type[engine]


class GpuCollector:
    """``gpu.utilization`` (busiest engine, %) and ``gpu.busiest_engine`` (e.g. "3D", "VideoEncode")."""

    key = "gpu"
    _SOURCE = "PDH:GPU Engine"

    def __init__(self, source: LatestSource[CounterSample]) -> None:
        self._source = source

    def collect(self, ctx: CollectorContext) -> dict[str, Reading]:
        busiest, problem = self._busiest()  # one look at the probe, so both readings agree
        if busiest is None:
            assert problem is not None
            return {
                name: Reading.missing(name, problem.status, problem.detail, self._SOURCE)
                for name in ("gpu.utilization", "gpu.busiest_engine")
            }
        percent, engine = busiest
        return {
            "gpu.utilization": Reading.available(
                "gpu.utilization", percent, "%", self._SOURCE, detail="busiest GPU engine, as in Task Manager"
            ),
            "gpu.busiest_engine": Reading.available("gpu.busiest_engine", engine, "", self._SOURCE),
        }

    def _busiest(self) -> tuple[Optional[tuple[float, str]], Optional[Reading]]:
        """The busiest-engine result, or a status reading explaining why there is none."""
        outcome = self._source.latest()
        if outcome is None:
            return None, Reading.missing("gpu", Availability.UNAVAILABLE, "first measurement still running", self._SOURCE)
        if outcome.value is None:
            return None, Reading.missing("gpu", Availability.UNAVAILABLE, outcome.error or "unavailable", self._SOURCE)
        busiest = busiest_engine(outcome.value)
        if busiest is None:
            return None, Reading.missing(
                "gpu",
                Availability.UNAVAILABLE,
                "no GPU engine counters reported (needs a WDDM 2.0+ GPU driver; names are language-specific)",
                self._SOURCE,
            )
        return busiest, None
