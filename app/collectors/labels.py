"""Plain-language names and value text for readings (shared by the UI, the report and ``--probe``)."""

from __future__ import annotations

from typing import Mapping

from app.collectors.base import Availability, Reading
from app.utils.formatting import format_bytes

# (reading name, label shown to the user), in display order
EVIDENCE_ROWS: list[tuple[str, str]] = [
    ("gpu.utilization", "GPU load"),
    ("mem.commit_percent", "Memory commitment"),
    ("mem.pages_per_sec", "Paging activity"),
    ("disk.read_bps", "Disk reads"),
    ("disk.write_bps", "Disk writes"),
    ("temp.acpi_max", "Temperature (ACPI zone)"),
    ("fan.rpm", "Fan speed"),
    ("power.on_battery", "Power source"),
    ("power.battery_percent", "Battery"),
    ("power.mode", "Power mode"),
    ("startup.enabled_count", "Startup programs"),
]

_TITLES = dict(EVIDENCE_ROWS) | {
    "mem.commit_used_bytes": "Memory commitment",
    "mem.commit_limit_bytes": "Memory commitment",
    "gpu.busiest_engine": "GPU load",
    "disk.total_bps": "Disk activity",
    "power.charging": "Battery",
    "power.battery_saver": "Battery saver",
    "startup.entries": "Startup programs",
    "cpu.percent": "CPU load",
    "memory.percent": "Memory use",
    "storage.percent": "Drive space",
}


def title_for(name: str) -> str:
    """Friendly name of a reading id (falls back to the id itself)."""
    return _TITLES.get(name, name)


_NOT_SUPPORTED = Reading.missing("", Availability.NOT_SUPPORTED, "not collected on this operating system")


def value_text(readings: Mapping[str, Reading], name: str) -> str:
    """Human text for an AVAILABLE reading; empty string for one with no value."""
    reading = readings.get(name)
    if reading is None or not reading.is_available:
        return ""
    value = reading.value
    if name == "gpu.utilization":
        engine = readings.get("gpu.busiest_engine")
        suffix = f" ({engine.value} engine)" if engine is not None and engine.is_available else ""
        return f"{value:.0f}%{suffix}"
    if name in ("mem.commit_percent", "power.battery_percent"):
        text = f"{value:.0f}%"
        if name == "power.battery_percent":
            charging = readings.get("power.charging")
            if charging is not None and charging.is_available:
                text += ", charging" if charging.value else ", not charging"
        return text
    if name == "mem.pages_per_sec":
        return f"{value:.0f} pages/s"
    if name in ("disk.read_bps", "disk.write_bps"):
        return f"{format_bytes(int(value))}/s"
    if name == "temp.acpi_max":
        return f"{value:.0f} °C"
    if name == "power.on_battery":
        return "On battery" if value else "Plugged in"
    if name == "startup.enabled_count":
        return f"{value} enabled"
    return str(value)


def describe(readings: Mapping[str, Reading], name: str) -> tuple[str, str]:
    """``(value text, status)``: the value is empty unless the reading is available.

    ``status`` is only the short state ("Available", "Permission restricted", ...); the reason
    for a gap is in ``detail_for`` / ``evidence_report``.
    """
    reading = readings.get(name, _NOT_SUPPORTED)
    return (value_text(readings, name) if reading.is_available else ""), reading.status.value


def detail_for(readings: Mapping[str, Reading], name: str) -> str:
    return readings.get(name, _NOT_SUPPORTED).detail


def evidence_report(readings: Mapping[str, Reading]) -> str:
    """Plain-text list of every evidence row with its state, reason and source (for the Details dialog)."""
    lines: list[str] = []
    for name, title in EVIDENCE_ROWS:
        reading = readings.get(name, _NOT_SUPPORTED)
        value, status = describe(readings, name)
        lines.append(f"{title}: {value or status}")
        if reading.detail:
            lines.append(f"    {reading.detail}")
        if reading.source:
            lines.append(f"    Source: {reading.source}")
        lines.append("")
    lines.append("A reading OScope cannot obtain is shown with the reason, never as a guessed number.")
    lines.append("Startup programs counts registry Run keys and Startup folders only (not services or scheduled tasks).")
    lines.append("Temperature is an ACPI thermal zone reported by the PC's firmware; it is not necessarily the CPU.")
    return "\n".join(lines)
