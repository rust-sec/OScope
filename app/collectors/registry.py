"""Chooses which collectors run on this machine.

Platform dispatch lives here and only here. OScope is Windows-only for now; a
future Linux build would add its collectors in one more branch of this function
without touching the Sampler, analysis or GUI code.
"""

from __future__ import annotations

from app.collectors.base import Collector
from app.collectors.disk_activity import DiskActivityCollector
from app.core import platform as oscope_platform

PDH_REFRESH_SECONDS = 10.0    # typeperf counters (GPU, Pages/sec)
WMI_REFRESH_SECONDS = 30.0    # temperatures change slowly; PowerShell is expensive to start


def build_default_collectors() -> list[Collector]:
    """Collectors for the current platform (disk everywhere psutil works; the rest on Windows)."""
    collectors: list[Collector] = [DiskActivityCollector()]
    if not oscope_platform.is_windows():
        return collectors

    from app.collectors.windows.background import BackgroundProbe
    from app.collectors.windows.gpu import GpuCollector, make_gpu_reader
    from app.collectors.windows.memory import PAGES_PER_SEC_COUNTER, MemoryCollector
    from app.collectors.windows.power import PowerCollector
    from app.collectors.windows.startup import StartupCollector
    from app.collectors.windows.thermal import FanCollector, ThermalCollector, make_wmi_reader
    from app.collectors.windows.typeperf import make_counter_reader
    from app.core import windows_backend

    pages = BackgroundProbe(make_counter_reader(PAGES_PER_SEC_COUNTER), PDH_REFRESH_SECONDS, name="pages")
    gpu = BackgroundProbe(make_gpu_reader(), PDH_REFRESH_SECONDS, name="gpu")
    wmi = BackgroundProbe(make_wmi_reader(), WMI_REFRESH_SECONDS, name="wmi")
    collectors += [
        MemoryCollector(windows_backend.get_commit_charge, pages),
        GpuCollector(gpu),
        ThermalCollector(wmi),
        FanCollector(wmi),
        PowerCollector(windows_backend.get_power_status, windows_backend.get_power_overlay_guid),
        StartupCollector(),
    ]
    return collectors
