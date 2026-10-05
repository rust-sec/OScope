"""One detector per signal. Each returns a Finding: what is happening, the evidence, and optional options.

Wording rules (checked by tests): state what was measured; say "seen together" rather than
cause and effect; never claim more than the data shows; say plainly when something cannot be read.
"""

from __future__ import annotations

from typing import Callable

from app.analysis.facts import Facts
from app.analysis.grouping import ProcessGroup
from app.analysis.models import Evidence, EvidenceStrength, Finding
from app.collectors.base import Availability, Reading
from app.utils.constants import (
    ACTIVE_CPU_PERCENT,
    COMMIT_HIGH_PERCENT,
    CPU_CRITICAL_PERCENT,
    CPU_HIGH_PERCENT,
    DISK_HEAVY_BYTES_PER_SEC,
    GPU_HIGH_PERCENT,
    MANY_STARTUP_ENTRIES,
    MEMORY_CRITICAL_PERCENT,
    MEMORY_HIGH_PERCENT,
    STORAGE_CRITICAL_PERCENT,
    STORAGE_FULL_PERCENT,
    STORAGE_LOW_PERCENT,
    TEMP_HOT_C,
    TEMP_WARM_C,
    TOP_GROUPS_SHOWN,
)
from app.utils.formatting import format_bytes, format_used_of_total

OBSERVED = EvidenceStrength.OBSERVED
UNVERIFIED = EvidenceStrength.UNVERIFIED


# --------------------------------------------------------------------------- #
# shared helpers (also used by the relationship rules)
# --------------------------------------------------------------------------- #
def group_evidence(group: ProcessGroup, value_text: str) -> Evidence:
    return Evidence(f"proc.group.{group.key}", group.display, value_text, OBSERVED, "process list")


def count_suffix(group: ProcessGroup) -> str:
    return f" ({group.count} processes)" if group.count > 1 else ""


def gap_text(reading: Reading | None) -> str:
    """Why a reading is missing, in one phrase: ``"Permission restricted: needs administrator rights"``."""
    if reading is None:
        return "Not supported on this OS"
    return f"{reading.status.value}: {reading.detail}" if reading.detail else reading.status.value


def unverified(evidence_id: str, label: str, reading: Reading | None) -> Evidence:
    return Evidence(evidence_id, label, f"Could not verify. {gap_text(reading)}", UNVERIFIED, reading.source if reading else "")


# --------------------------------------------------------------------------- #
# CPU
# --------------------------------------------------------------------------- #
def detect_cpu(f: Facts) -> Finding:
    if not f.has("cpu"):
        return Finding("cpu", "normal", "CPU load", "CPU load could not be read.")
    latest = float(f.snapshot.cpu_percent)
    state = f.state("cpu", CPU_HIGH_PERCENT)
    contributing = [Evidence("cpu.now", "CPU load now", f"{latest:.0f}%", OBSERVED, "psutil.cpu_percent")]
    if state.basis == "sustained":
        contributing.append(
            Evidence("cpu.mean", f"CPU load, last {f.seconds:.0f} s", f"average {state.mean:.0f}%, peak {state.maximum:.0f}%")
        )
    for group in f.top_by_cpu(TOP_GROUPS_SHOWN, ACTIVE_CPU_PERCENT):
        contributing.append(group_evidence(group, f"{group.cpu_percent:.0f}% of the CPU{count_suffix(group)}"))

    if not state.high:
        happening = f"CPU load is {latest:.0f}%" + (
            f" (average {state.mean:.0f}% over the last {f.seconds:.0f} seconds)." if state.basis == "sustained" else " right now."
        )
        return Finding("cpu", "normal", "CPU load", happening, contributing)

    if state.basis == "sustained":
        level = "critical" if state.mean >= CPU_CRITICAL_PERCENT else "warning"
        happening = (
            f"CPU load has been high: {state.mean:.0f}% on average over the last {f.seconds:.0f} seconds "
            f"(now {latest:.0f}%)."
        )
    else:
        level = "info"
        happening = (
            f"CPU load is high right now ({latest:.0f}%). OScope has only just started collecting, "
            "so it cannot yet say whether this is sustained."
        )
    consider = [
        "If you did not expect this much activity, you could look at the programs listed above, "
        "or let a running task (an export, an update, a scan) finish."
    ]
    return Finding("cpu", level, "High CPU load", happening, contributing, consider)


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
def detect_memory(f: Facts) -> Finding:
    memory = f.snapshot.memory
    if memory is None:
        return Finding("memory", "normal", "Memory", "Memory use could not be read.")
    state = f.state("mem", MEMORY_HIGH_PERCENT)
    commit = f.value("mem.commit_percent")
    commit_high = commit is not None and commit >= COMMIT_HIGH_PERCENT

    contributing = [
        Evidence("mem.percent", "Memory in use", f"{format_used_of_total(memory.used, memory.total)} ({memory.percent:.0f}%)"),
        Evidence("mem.available", "Memory available", format_bytes(memory.available)),
    ]
    if commit is not None:
        contributing.append(
            Evidence(
                "mem.commit",
                "Memory commitment",
                f"{commit:.0f}% of the limit ({format_bytes(f.value('mem.commit_used_bytes') or 0)} "
                f"of {format_bytes(f.value('mem.commit_limit_bytes') or 0)})",
            )
        )
    pages = f.value("mem.pages_per_sec")
    if pages is not None:
        contributing.append(Evidence("mem.pages", "Paging activity", f"{pages:.0f} pages per second"))
    for group in f.top_by_memory(TOP_GROUPS_SHOWN):
        contributing.append(
            group_evidence(group, f"{format_bytes(group.memory_bytes)} working set{count_suffix(group)}")
        )

    if state.high:
        sustained = state.basis == "sustained"
        level = "critical" if sustained and state.mean >= MEMORY_CRITICAL_PERCENT else "warning"
        when = (
            f"over the last {f.seconds:.0f} seconds"
            if sustained
            else "right now (OScope has only just started collecting)"
        )
        happening = f"Memory use is high: {memory.percent:.0f}% in use ({format_used_of_total(memory.used, memory.total)}) {when}."
        if commit_high:
            happening += f" Committed memory is at {commit:.0f}% of the limit."
        title = "High memory use"
    elif commit_high:
        level, title = "warning", "Memory commitment is high"
        happening = (
            f"Memory use is {memory.percent:.0f}%, but committed memory is at {commit:.0f}% of its limit. "
            "When it reaches the limit, Windows can refuse requests for more memory."
        )
    else:
        level, title = "normal", "Memory"
        happening = (
            f"Memory use is {memory.percent:.0f}% ({format_used_of_total(memory.used, memory.total)}); "
            f"{format_bytes(memory.available)} is available. A high percentage by itself is not necessarily a problem: "
            "Windows uses spare memory for caching."
        )
    consider = []
    if level != "normal":
        consider.append(
            "You could close programs or browser tabs you are not using. The list above shows which hold the most memory."
        )
    return Finding("memory", level, title, happening, contributing, consider)


# --------------------------------------------------------------------------- #
# Disk activity
# --------------------------------------------------------------------------- #
def detect_disk(f: Facts) -> Finding:
    read, write = f.value("disk.read_bps"), f.value("disk.write_bps")
    if read is None or write is None:
        reason = gap_text(f.readings.get("disk.read_bps"))
        return Finding("disk", "normal", "Disk activity", f"Disk activity is not available yet ({reason}).")
    state = f.state("disk.total_bps", DISK_HEAVY_BYTES_PER_SEC)
    contributing = [
        Evidence("disk.read", "Reading", f"{format_bytes(read)}/s", OBSERVED, "psutil.disk_io_counters"),
        Evidence("disk.write", "Writing", f"{format_bytes(write)}/s", OBSERVED, "psutil.disk_io_counters"),
        Evidence(
            "disk.limits",
            "What OScope cannot see",
            "Which program the disk traffic belongs to, and how busy the disk is (it measures how much data moves)",
            UNVERIFIED,
        ),
    ]
    if not state.high:
        return Finding(
            "disk", "normal", "Disk activity",
            f"Disk activity is {format_bytes(read)}/s reading and {format_bytes(write)}/s writing right now.",
            contributing,
        )
    when = f"over the last {f.seconds:.0f} seconds" if state.basis == "sustained" else "right now"
    return Finding(
        "disk", "info", "Heavy disk activity",
        f"The disk has been moving {format_bytes(state.mean or 0)} per second on average {when}.",
        contributing,
        [
            "Sync, update and scan tasks commonly produce this kind of activity. OScope cannot tell whether "
            "that applies here, so you could check again in a few minutes."
        ],
    )


# --------------------------------------------------------------------------- #
# GPU
# --------------------------------------------------------------------------- #
def detect_gpu(f: Facts) -> Finding:
    util = f.value("gpu.utilization")
    if util is None:
        reading = f.readings.get("gpu.utilization")
        return Finding(
            "gpu", "normal", "GPU load", f"GPU load cannot be read on this PC ({gap_text(reading)}).",
            [unverified("gpu.none", "GPU load", reading)],
        )
    engine = f.value("gpu.busiest_engine")
    state = f.state("gpu.utilization", GPU_HIGH_PERCENT)
    contributing = [
        Evidence(
            "gpu.util", "Busiest GPU engine (as in Task Manager)",
            f"{util:.0f}%" + (f", {engine} engine" if engine else ""), OBSERVED, "PDH:GPU Engine",
        )
    ]
    if not state.high:
        return Finding("gpu", "normal", "GPU load", f"The busiest GPU engine is at {util:.0f}% right now.", contributing)
    when = f"over the last {f.seconds:.0f} seconds" if state.basis == "sustained" else "right now"
    return Finding(
        "gpu", "info", "High GPU load",
        f"The busiest GPU engine has averaged {state.mean:.0f}% {when}.",
        contributing,
    )


# --------------------------------------------------------------------------- #
# Storage (system drive)
# --------------------------------------------------------------------------- #
def detect_storage(f: Facts) -> Finding:
    st = f.snapshot.storage
    if st is None:
        return Finding("storage", "normal", "System drive", "Free space on the system drive could not be read.")
    contributing = [
        Evidence("storage.used", f"Drive {st.path}", f"{format_used_of_total(st.used, st.total)} used ({st.percent:.0f}%)"),
        Evidence("storage.free", "Free space", f"{format_bytes(st.free)} ({100 - st.percent:.0f}% free)"),
    ]
    if st.percent >= STORAGE_CRITICAL_PERCENT:
        level, title = "critical", "Very low storage"
    elif st.percent >= STORAGE_LOW_PERCENT:
        level, title = "warning", "Low storage"
    elif st.percent >= STORAGE_FULL_PERCENT:
        level, title = "info", "Storage is getting full"
    else:
        level, title = "normal", "System drive"
    happening = f"The system drive ({st.path}) is {st.percent:.0f}% full, with {format_bytes(st.free)} free of {format_bytes(st.total)}."
    consider = ["Open the Storage view to see which folders take the most space."] if level != "normal" else []
    return Finding("storage", level, title, happening, contributing, consider, goto="storage" if level != "normal" else None)


# --------------------------------------------------------------------------- #
# Power
# --------------------------------------------------------------------------- #
def detect_power(f: Facts) -> Finding:
    on_battery = f.value("power.on_battery")
    if on_battery is None:
        reading = f.readings.get("power.on_battery")
        return Finding(
            "power", "normal", "Power", f"The power state cannot be read on this PC ({gap_text(reading)}).",
            [unverified("power.none", "Power source", reading)],
        )
    mode, saver, percent = f.value("power.mode"), f.value("power.battery_saver"), f.value("power.battery_percent")
    contributing = [Evidence("power.source", "Power source", "Battery" if on_battery else "Plugged in", OBSERVED, "GetSystemPowerStatus")]
    if percent is not None:
        contributing.append(Evidence("power.battery", "Battery", f"{percent}%"))
    if mode:
        contributing.append(Evidence("power.mode", "Windows power mode", str(mode), OBSERVED, "PowerGetEffectiveOverlayScheme"))
    if saver:
        contributing.append(Evidence("power.saver", "Battery saver", "On"))

    mode_text = f" The Windows power mode is {mode}." if mode else ""
    if on_battery:
        return Finding(
            "power", "info", "Running on battery",
            "This PC is running on battery" + (f" ({percent}%)" if percent is not None else "") + "." + mode_text
            + (" Battery saver is on." if saver else ""),
            contributing,
            ["Windows can run the processor more conservatively on battery. Plugging in, or choosing a different "
             "power mode in Windows Settings, changes that."],
        )
    if saver:
        return Finding("power", "info", "Battery saver is on", "Battery saver is on although this PC is plugged in." + mode_text, contributing)
    return Finding("power", "normal", "Power", "This PC is plugged in." + mode_text, contributing)


# --------------------------------------------------------------------------- #
# Temperature and fan
# --------------------------------------------------------------------------- #
def detect_thermal(f: Facts) -> Finding:
    contributing: list[Evidence] = []
    temp = f.value("temp.acpi_max")
    if temp is not None:
        if temp >= TEMP_HOT_C:
            level = "warning"
        elif temp >= TEMP_WARM_C:
            level = "info"
        else:
            level = "normal"
        contributing.append(
            Evidence("temp.acpi", "Hottest ACPI thermal zone", f"{temp:.0f} °C", OBSERVED, "WMI:MSAcpi_ThermalZoneTemperature")
        )
        happening = (
            f"An ACPI thermal zone reports {temp:.0f} °C. It is a zone sensor reported by the PC's firmware, not "
            "necessarily the CPU, and OScope does not know this PC's safe limits."
        )
        title = "Temperature"
    else:
        reading = f.readings.get("temp.acpi_max")
        level, title = "normal", "Temperature and fan"
        happening = f"OScope cannot read a temperature on this PC ({gap_text(reading)}), so it cannot say whether the PC is running hot."
        contributing.append(unverified("temp.none", "Temperature", reading))
    contributing.append(unverified("fan.none", "Fan speed", f.readings.get("fan.rpm")))

    cpu = f.state("cpu", CPU_HIGH_PERCENT)
    if cpu.latest is not None:
        contributing.append(
            Evidence("heat.cpu", "CPU load", f"{cpu.latest:.0f}% now" + (f", average {cpu.mean:.0f}%" if cpu.basis == "sustained" else ""))
        )
    gpu = f.state("gpu.utilization", GPU_HIGH_PERCENT)
    if gpu.latest is not None:
        contributing.append(
            Evidence("heat.gpu", "GPU load", f"{gpu.latest:.0f}% now" + (f", average {gpu.mean:.0f}%" if gpu.basis == "sustained" else ""))
        )
    return Finding("thermal", level, title, happening, contributing)


# --------------------------------------------------------------------------- #
# Startup programs
# --------------------------------------------------------------------------- #
def detect_startup(f: Facts) -> Finding:
    count, entries = f.value("startup.enabled_count"), f.value("startup.entries")
    if count is None or entries is None:
        reading = f.readings.get("startup.enabled_count")
        return Finding(
            "startup", "normal", "Startup programs", f"Startup programs cannot be listed on this PC ({gap_text(reading)}).",
            [unverified("startup.none", "Startup programs", reading)],
        )
    enabled = [entry for entry in entries if entry.enabled is True]
    contributing = [
        Evidence(f"startup.{index}", entry.name, entry.location, OBSERVED, "registry / Startup folders")
        for index, entry in enumerate(enabled[:8])
    ]
    contributing.append(
        Evidence("startup.scope", "Not included", "Services and scheduled tasks that also start with Windows", UNVERIFIED)
    )
    happening = f"{count} programs are set to start with Windows (registry Run keys and Startup folders only)."
    if count >= MANY_STARTUP_ENTRIES:
        return Finding(
            "startup", "info", "Many programs start with Windows", happening, contributing,
            ["You can review them in Task Manager's Startup tab."],
        )
    return Finding("startup", "normal", "Startup programs", happening, contributing)


DETECTORS: dict[str, Callable[[Facts], Finding]] = {
    "cpu": detect_cpu,
    "memory": detect_memory,
    "disk": detect_disk,
    "gpu": detect_gpu,
    "storage": detect_storage,
    "power": detect_power,
    "thermal": detect_thermal,
    "startup": detect_startup,
}

# Readings each detector depends on, so a result can list what it could not check.
DETECTOR_READINGS: dict[str, tuple[str, ...]] = {
    "cpu": (),
    "memory": ("mem.commit_percent", "mem.pages_per_sec"),
    "disk": ("disk.read_bps", "disk.write_bps"),
    "gpu": ("gpu.utilization",),
    "storage": (),
    "power": ("power.on_battery", "power.mode"),
    "thermal": ("temp.acpi_max", "fan.rpm"),
    "startup": ("startup.enabled_count",),
}


def missing_reading(name: str, readings: dict[str, Reading]) -> Reading | None:
    """The reading if it could not be obtained (absent counts as 'not supported'); None if it is available."""
    reading = readings.get(name)
    if reading is None:
        return Reading.missing(name, Availability.NOT_SUPPORTED, "not collected on this operating system")
    return None if reading.is_available else reading
