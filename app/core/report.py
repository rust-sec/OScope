"""Human-readable text report."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core import diagnostics
from app.core.sampler import Snapshot
from app.core.storage_manager import ScanResult
from app.core.system_info import StaticInfo
from app.utils.constants import ACCESS_DENIED_MESSAGE, TOP_PROCESSES_SHOWN, UNAVAILABLE
from app.utils.formatting import (
    format_bytes,
    format_count,
    format_duration,
    format_file_stamp,
    format_percent,
    format_timestamp,
)

_WIDTH = 50
_HEAVY = "=" * _WIDTH
_LIGHT = "-" * _WIDTH

# reports/ sits next to main.py:  <project>/app/core/report.py -> parents[2] == <project>
REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"


def _section(title: str) -> list[str]:
    return ["", title, _LIGHT]


def build_report(
    info: StaticInfo,
    snapshot: Snapshot,
    scan: Optional[ScanResult] = None,
    large_file_threshold_mb: int = 500,
    now: Optional[datetime] = None,
) -> str:
    """Assemble the report text from data already collected (no new system reads)."""
    now = now or datetime.now()
    lines = [_HEAVY, "OSCOPE SYSTEM REPORT", _HEAVY, "", "Generated:", format_timestamp(now)]

    lines += _section("OPERATING SYSTEM")
    lines.append(f"OS: {info.os_name}")
    if info.os_detail:
        lines.append(f"Details: {info.os_detail}")
    lines.append(f"Hostname: {info.hostname}")
    lines.append(f"Processor: {info.cpu_name or UNAVAILABLE}")
    lines.append(
        f"CPUs: {info.logical_cpus if info.logical_cpus is not None else UNAVAILABLE} logical, "
        f"{info.physical_cpus if info.physical_cpus is not None else UNAVAILABLE} physical"
    )
    lines.append(
        "Uptime: " + (format_duration(snapshot.uptime_seconds) if snapshot.uptime_seconds is not None else UNAVAILABLE)
    )

    lines += _section("SYSTEM HEALTH")
    lines.append("CPU Usage: " + (format_percent(snapshot.cpu_percent) if snapshot.cpu_percent is not None else UNAVAILABLE))
    lines.append(
        "Memory Usage: " + (format_percent(snapshot.memory.percent) if snapshot.memory else UNAVAILABLE)
    )
    lines.append(
        "Disk Usage: " + (format_percent(snapshot.storage.percent) if snapshot.storage else UNAVAILABLE)
    )

    lines += _section("MEMORY")
    if snapshot.memory:
        lines.append(f"Total: {format_bytes(snapshot.memory.total)}")
        lines.append(f"Used: {format_bytes(snapshot.memory.used)}")
        lines.append(f"Available: {format_bytes(snapshot.memory.available)}")
    else:
        lines.append(UNAVAILABLE)

    processes = [p for p in snapshot.processes if p.memory_bytes is not None]
    lines += _section("TOP PROCESSES (BY MEMORY)")
    if processes:
        for proc in sorted(processes, key=lambda p: p.memory_bytes or 0, reverse=True)[:TOP_PROCESSES_SHOWN]:
            lines.append(f"{proc.name[:24]:<26}PID {proc.pid:<8}Memory {format_bytes(proc.memory_bytes or 0)}")
    else:
        lines.append(UNAVAILABLE)

    lines += _section("TOP PROCESSES (BY CPU)")
    if snapshot.processes:
        for proc in sorted(snapshot.processes, key=lambda p: p.cpu_percent, reverse=True)[:TOP_PROCESSES_SHOWN]:
            lines.append(f"{proc.name[:24]:<26}PID {proc.pid:<8}CPU {format_percent(proc.cpu_percent)}")
    else:
        lines.append(UNAVAILABLE)

    lines += _section("STORAGE")
    if snapshot.storage:
        lines.append(f"Drive: {snapshot.storage.path}")
        lines.append(f"Total: {format_bytes(snapshot.storage.total)}")
        lines.append(f"Used: {format_bytes(snapshot.storage.used)}")
        lines.append(f"Free: {format_bytes(snapshot.storage.free)}")
    else:
        lines.append(UNAVAILABLE)

    if scan is not None and scan.error is None:
        lines += _section("STORAGE ANALYSIS (LAST FOLDER SCAN)")
        lines.append(f"Folder: {scan.root}")
        lines.append(f"Total size: {format_bytes(scan.total_size)}")
        lines.append(f"Files: {format_count(scan.file_count)}")
        lines.append(f"Directories: {format_count(scan.dir_count)}")
        if scan.cancelled:
            lines.append("Note: the scan was stopped early; totals are partial.")
        if scan.denied_count:
            lines.append(f"Access denied: {format_count(scan.denied_count)} item(s). {ACCESS_DENIED_MESSAGE}")
        lines.append("")
        lines.append("Largest directories:")
        for name, _path, size in scan.largest_dirs[:5]:
            lines.append(f"  {name[:30]:<32}{format_bytes(size)}")
        threshold = large_file_threshold_mb * 1024 * 1024
        big = [f for f in scan.large_files if f[2] >= threshold][:5]
        lines.append("")
        lines.append(f"Large files (>= {large_file_threshold_mb} MB):")
        if big:
            for name, path, size in big:
                lines.append(f"  {name[:30]:<32}{format_bytes(size)}   {path}")
        else:
            lines.append("  No files above the selected size threshold were found.")

    lines += _section("DIAGNOSTIC SUMMARY")
    lines += diagnostics.summary_lines(snapshot.findings)

    lines += ["", _HEAVY, "Generated by OScope. Read-only report: no system settings were changed.", _HEAVY]
    return "\n".join(lines) + "\n"


def save_report(text: str, reports_dir: Optional[Path] = None, now: Optional[datetime] = None) -> Path:
    """Write the report to ``reports/oscope_report_<timestamp>.txt`` and return its path.

    If the reports folder is not writable, falls back to ``~/OScope Reports``.
    """
    now = now or datetime.now()
    filename = f"oscope_report_{format_file_stamp(now)}.txt"
    primary = reports_dir or REPORTS_DIR
    try:
        primary.mkdir(parents=True, exist_ok=True)
        target = primary / filename
        target.write_text(text, encoding="utf-8")
        return target
    except OSError:
        fallback = Path.home() / "OScope Reports"
        fallback.mkdir(parents=True, exist_ok=True)
        target = fallback / filename
        target.write_text(text, encoding="utf-8")
        return target
