"""Live CPU and memory readings."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import psutil

from app.core import windows_backend as win


@dataclass
class MemoryInfo:
    total: int
    used: int
    available: int
    percent: float


class ResourceMonitor:
    """Samples CPU utilisation and physical memory.

    CPU utilisation is a *rate* (busy time / elapsed time), so the first call
    only primes the counters; later calls report the average since the previous
    call. Calls closer than 0.5 s apart return the previous value, because a
    percentage over a few milliseconds is just noise.
    """

    def __init__(self) -> None:
        self._last_cpu = 0.0
        self._last_time = 0.0
        try:
            psutil.cpu_percent(interval=None)  # prime the counters
        except Exception:
            pass

    def cpu_percent(self) -> Optional[float]:
        """Overall CPU utilisation in percent (all logical CPUs combined)."""
        now = time.monotonic()
        if self._last_time and now - self._last_time < 0.5:
            return self._last_cpu
        try:
            self._last_cpu = float(psutil.cpu_percent(interval=None))
        except Exception:
            return None
        self._last_time = now
        return self._last_cpu

    def memory(self) -> Optional[MemoryInfo]:
        """Physical memory. "Used" means total minus available, like Task Manager's "In use"."""
        status = win.get_memory_status()  # direct Win32 call: GlobalMemoryStatusEx
        if status is not None:
            total, available = status
        else:
            try:
                vm = psutil.virtual_memory()
                total, available = int(vm.total), int(vm.available)
            except Exception:
                return None
        if total <= 0:
            return None
        used = max(total - available, 0)
        return MemoryInfo(total=total, used=used, available=available, percent=used / total * 100.0)
