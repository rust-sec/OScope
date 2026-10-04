"""Static facts about this computer (things that do not change while OScope runs)."""

from __future__ import annotations

import platform as _stdlib_platform
import socket
from dataclasses import dataclass
from typing import Optional

import psutil

from app.core import windows_backend as win


@dataclass
class StaticInfo:
    """Machine facts. ``None`` means "not obtainable on this platform"."""

    os_name: str
    os_detail: Optional[str]
    hostname: str
    cpu_name: Optional[str]
    logical_cpus: Optional[int]
    physical_cpus: Optional[int]


def get_static_info() -> StaticInfo:
    """Collect OS name, hostname and CPU description once at start-up."""
    version = win.get_windows_version()
    if version is not None:
        os_name, os_detail = version
    else:
        # Not Windows (developer mode) or the registry could not be read.
        os_name = f"{_stdlib_platform.system()} {_stdlib_platform.release()}".strip() or "Unknown OS"
        os_detail = None

    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = "Unknown"

    try:
        logical = psutil.cpu_count(logical=True)
        physical = psutil.cpu_count(logical=False)
    except Exception:
        logical = physical = None

    return StaticInfo(
        os_name=os_name,
        os_detail=os_detail,
        hostname=hostname,
        cpu_name=win.get_cpu_name(),
        logical_cpus=logical,
        physical_cpus=physical,
    )


def get_uptime_seconds() -> Optional[float]:
    """Seconds since the computer booted (GetTickCount64, psutil boot time as fallback)."""
    seconds = win.get_uptime_seconds()
    if seconds is not None:
        return seconds
    try:
        import time

        return max(time.time() - psutil.boot_time(), 0.0)
    except Exception:
        return None
