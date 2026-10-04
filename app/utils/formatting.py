"""Small helpers that turn raw numbers into human-readable text."""

from __future__ import annotations

from datetime import datetime

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_bytes(num_bytes: float) -> str:
    """Format a byte count, e.g. ``6657193984`` -> ``"6.2 GB"``.

    Uses binary units (1 KB = 1024 B), the same convention Windows Explorer uses.
    """
    value = float(max(num_bytes, 0))
    unit = 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    if unit == 0:
        return f"{int(value)} B"
    if unit == 1 or (unit == 2 and value >= 100):
        return f"{value:.0f} {_UNITS[unit]}"
    return f"{value:.1f} {_UNITS[unit]}"


def format_used_of_total(used: float, total: float) -> str:
    """Format two byte counts in one shared unit, e.g. ``"6.1 / 8.0 GB"``."""
    scale, unit = 1, 0
    while total / scale >= 1024 and unit < len(_UNITS) - 1:
        scale *= 1024
        unit += 1
    if unit == 0:
        return f"{int(used)} / {int(total)} B"
    decimals = 0 if total / scale >= 100 else 1
    return f"{used / scale:.{decimals}f} / {total / scale:.{decimals}f} {_UNITS[unit]}"


def format_percent(value: float) -> str:
    """``42.4`` -> ``"42%"``."""
    return f"{value:.0f}%"


def format_count(value: int) -> str:
    """``1248`` -> ``"1,248"``."""
    return f"{value:,}"


def format_duration(total_seconds: float) -> str:
    """Format an uptime, e.g. ``16320`` -> ``"4h 32m"``."""
    seconds = int(max(total_seconds, 0))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def format_clock(moment: datetime) -> str:
    """``13:42:18`` (used in the header)."""
    return moment.strftime("%H:%M:%S")


def format_timestamp(moment: datetime) -> str:
    """``2026-09-29 13:45:22`` (used in reports)."""
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def format_file_stamp(moment: datetime) -> str:
    """``2026-09-29_134522`` (used in report file names)."""
    return moment.strftime("%Y-%m-%d_%H%M%S")
