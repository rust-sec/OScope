"""Relationships between measurements that are easy to miss ("things you may not have noticed").

Each rule is data: the inputs it needs, a condition, and wording. A rule never fires unless every
input it needs is really available, and no rule may claim more than "seen together": the
constructor rejects anything stronger, and the wording is checked by tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.analysis.detectors import count_suffix, group_evidence
from app.analysis.facts import Facts
from app.analysis.models import Evidence, EvidenceStrength, Relation
from app.utils.constants import (
    ACTIVE_CPU_PERCENT,
    CPU_HIGH_PERCENT,
    DISK_HEAVY_BYTES_PER_SEC,
    GPU_HIGH_PERCENT,
    LOW_FREE_BYTES,
    LOW_FREE_PERCENT,
    MANY_BROWSER_PROCESSES,
    MANY_STARTUP_ENTRIES,
    MEMORY_HIGH_PERCENT,
    TEMP_WARM_C,
)
from app.utils.formatting import format_bytes, format_duration

# Programs that keep large temporary working files (scratch / cache) and sync clients that touch many files.
SCRATCH_APP_KEYS = frozenset({"photoshop.exe", "adobe premiere pro.exe", "premiere pro.exe", "afterfx.exe", "resolve.exe"})
SYNC_APP_KEYS = frozenset({"onedrive.exe", "dropbox.exe", "googledrivefs.exe"})
RECENT_BOOT_SECONDS = 15 * 60

Built = tuple[str, list[Evidence]]  # (sentence, supporting evidence)


@dataclass(frozen=True)
class Rule:
    id: str
    requires: tuple[str, ...]                 # inputs that must exist; "!name" means "must NOT be available"
    when: Callable[[Facts], bool]
    build: Callable[[Facts], Built]
    strength: EvidenceStrength = EvidenceStrength.CORRELATION
    only_for: Optional[frozenset[str]] = None  # question ids this rule belongs to (None = all)

    def __post_init__(self) -> None:
        if self.strength > EvidenceStrength.CORRELATION:
            raise ValueError(f"rule {self.id!r}: a relationship may claim at most 'seen together'")

    def evaluate(self, facts: Facts) -> Optional[Relation]:
        if not all(facts.has(token) if not token.startswith("!") else not facts.has(token[1:]) for token in self.requires):
            return None  # an input is missing: stay silent rather than guess
        if not self.when(facts):
            return None
        text, evidence = self.build(facts)
        return Relation(self.id, text, tuple(evidence), self.strength)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _mem_state(f: Facts):
    return f.state("mem", MEMORY_HIGH_PERCENT)


def _disk_state(f: Facts):
    return f.state("disk.total_bps", DISK_HEAVY_BYTES_PER_SEC)


def _mem_evidence(f: Facts) -> Evidence:
    memory = f.snapshot.memory
    return Evidence("mem.percent", "Memory in use", f"{memory.percent:.0f}% ({format_bytes(memory.used)} of {format_bytes(memory.total)})")


def _disk_evidence(f: Facts) -> Evidence:
    return Evidence(
        "disk.rate", "Disk activity",
        f"{format_bytes(float(f.value('disk.read_bps')) + float(f.value('disk.write_bps')))} per second (read + write)",
    )


def _names(groups) -> str:
    return ", ".join(group.display for group in groups)


def _load_text(f: Facts) -> str:
    cpu, gpu = f.state("cpu", CPU_HIGH_PERCENT).high, f.state("gpu.utilization", GPU_HIGH_PERCENT).high
    return "CPU and GPU" if cpu and gpu else "GPU" if gpu else "CPU"


def _load_is_high(f: Facts) -> bool:
    return f.state("cpu", CPU_HIGH_PERCENT).high or f.state("gpu.utilization", GPU_HIGH_PERCENT).high


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #
def _browser_when(f: Facts) -> bool:
    return any(g.count >= MANY_BROWSER_PROCESSES for g in f.browser_groups()) and _mem_state(f).high


def _browser_build(f: Facts) -> Built:
    group = max(f.browser_groups(), key=lambda g: g.count)
    text = (
        f"{group.display} has {group.count} processes running while memory use is high ({f.snapshot.memory.percent:.0f}%). "
        "Browsers normally run a separate process for each tab, extension and helper, so a long list is expected "
        "when many tabs are open."
    )
    return text, [group_evidence(group, f"{format_bytes(group.memory_bytes)} working set{count_suffix(group)}"), _mem_evidence(f)]


def _ram_disk_when(f: Facts) -> bool:
    return _mem_state(f).high and _disk_state(f).high


def _ram_disk_build(f: Facts) -> Built:
    text = (
        f"Memory use is high ({f.snapshot.memory.percent:.0f}%) and the disk has been busy at the same time. "
        "When memory runs short, Windows can move data between memory and disk, so the two are often seen together. "
        "OScope cannot tell which came first."
    )
    evidence = [_mem_evidence(f), _disk_evidence(f)]
    pages = f.value("mem.pages_per_sec")
    if pages is not None:
        evidence.append(Evidence("mem.pages", "Paging activity", f"{pages:.0f} pages per second"))
    return text, evidence


def _scratch_apps(f: Facts):
    return [g for g in f.groups if g.key in SCRATCH_APP_KEYS]


def _scratch_when(f: Facts) -> bool:
    storage = f.snapshot.storage
    low = storage.free < LOW_FREE_BYTES or (100 - storage.percent) < LOW_FREE_PERCENT
    return low and bool(_scratch_apps(f))


def _scratch_build(f: Facts) -> Built:
    storage = f.snapshot.storage
    apps = _scratch_apps(f)
    text = (
        f"{_names(apps)} is running and the system drive has {format_bytes(storage.free)} free ({100 - storage.percent:.0f}%). "
        "Photoshop and some video tools keep temporary working files (scratch or cache) on a drive; if that is this drive, "
        "little free space can slow them down or stop them working, as Adobe documents for Photoshop's scratch disks. "
        "OScope cannot see which drive your tool uses."
    )
    evidence = [group_evidence(g, f"{format_bytes(g.memory_bytes)} working set{count_suffix(g)}") for g in apps]
    evidence.append(Evidence("storage.free", f"Free space on {storage.path}", f"{format_bytes(storage.free)} ({100 - storage.percent:.0f}% free)"))
    return text, evidence


def _battery_when(f: Facts) -> bool:
    return f.value("power.on_battery") is True and _load_is_high(f)


def _battery_build(f: Facts) -> Built:
    mode = f.value("power.mode")
    when = f"over the last {f.seconds:.0f} seconds" if f.state("cpu", CPU_HIGH_PERCENT).basis == "sustained" else "right now"
    text = (
        f"This PC is on battery with the Windows power mode set to {mode}, while {_load_text(f)} load has been high {when}. "
        "Laptops often run slower on battery, depending on the power mode."
    )
    evidence = [Evidence("power.source", "Power source", "Battery"), Evidence("power.mode", "Windows power mode", str(mode))]
    cpu = f.state("cpu", CPU_HIGH_PERCENT)
    if cpu.latest is not None:
        evidence.append(Evidence("cpu.now", "CPU load", f"{cpu.latest:.0f}% now" + (f", average {cpu.mean:.0f}%" if cpu.basis == "sustained" else "")))
    gpu = f.state("gpu.utilization", GPU_HIGH_PERCENT)
    if gpu.latest is not None:
        evidence.append(Evidence("gpu.now", "GPU load", f"{gpu.latest:.0f}% now" + (f", average {gpu.mean:.0f}%" if gpu.basis == "sustained" else "")))
    return text, evidence


def _sync_apps(f: Facts):
    return [g for g in f.groups if g.key in SYNC_APP_KEYS and g.cpu_percent >= ACTIVE_CPU_PERCENT]


def _sync_when(f: Facts) -> bool:
    return _disk_state(f).high and bool(_sync_apps(f))


def _sync_build(f: Facts) -> Built:
    apps = _sync_apps(f)
    text = (
        f"{_names(apps)} is running and active while disk activity is high. "
        "OScope cannot see which program the disk traffic belongs to, so this shows the two together, nothing more."
    )
    return text, [group_evidence(g, f"{g.cpu_percent:.0f}% of the CPU") for g in apps] + [_disk_evidence(f)]


def _startup_when(f: Facts) -> bool:
    count = f.value("startup.enabled_count")
    uptime = f.snapshot.uptime_seconds
    recent = uptime is not None and uptime < RECENT_BOOT_SECONDS
    return count >= MANY_STARTUP_ENTRIES and (recent or _mem_state(f).high)


def _startup_build(f: Facts) -> Built:
    count = f.value("startup.enabled_count")
    uptime = f.snapshot.uptime_seconds
    if uptime is not None and uptime < RECENT_BOOT_SECONDS:
        context = f"this PC started only {format_duration(uptime)} ago"
    else:
        context = f"memory use is high ({f.snapshot.memory.percent:.0f}%)" if f.snapshot.memory else "memory use is high"
    text = (
        f"{count} programs are set to start with Windows (registry Run keys and Startup folders only), and {context}. "
        "Programs that start with Windows run in the background until they are closed."
    )
    return text, [Evidence("startup.count", "Programs set to start with Windows", str(count))]


def _temp_load_build(kind: str, state_key: str, threshold: float):
    def build(f: Facts) -> Built:
        temp = f.value("temp.acpi_max")
        state = f.state(state_key, threshold)
        when = f"over the last {f.seconds:.0f} seconds" if state.basis == "sustained" else "right now"
        text = (
            f"{kind} load has been high ({state.mean:.0f}% on average {when}) while an ACPI thermal zone reads {temp:.0f} °C. "
            f"That zone is not necessarily the {kind}, so this shows the two together, not the {kind}'s own temperature."
        )
        return text, [
            Evidence("load", f"{kind} load", f"{state.latest:.0f}% now, average {state.mean:.0f}%"),
            Evidence("temp.acpi", "Hottest ACPI thermal zone", f"{temp:.0f} °C"),
        ]

    return build


def _load_without_sensors_build(f: Facts) -> Built:
    state = f.state("cpu", CPU_HIGH_PERCENT)
    when = f"over the last {f.seconds:.0f} seconds" if state.basis == "sustained" else "right now"
    text = (
        f"{_load_text(f)} load has been high {when}. Sustained heavy load generally goes with a warmer PC and louder fans, "
        "but OScope cannot read this PC's temperature or fan speed, so it cannot confirm that here."
    )
    return text, [Evidence("heat.load", "CPU load", f"{state.latest:.0f}% now" + (f", average {state.mean:.0f}%" if state.basis == "sustained" else ""))]


MIN_MEMORY_MOMENTS = 10
MIN_HIT_SHARE = 0.6


def _history_groups_when(f: Facts) -> bool:
    history = f.history
    if history is None or history.memory_high_samples < MIN_MEMORY_MOMENTS or not history.memory_groups:
        return False
    top = history.memory_groups[0]
    return top.hits / top.samples >= MIN_HIT_SHARE


def _history_groups_build(f: Facts) -> Built:
    history = f.history
    top = history.memory_groups[0]
    text = (
        f"{top.name} was among the 3 biggest memory users in {top.hits} of {top.samples} recorded moments when memory use "
        f"was above {MEMORY_HIGH_PERCENT}%. This is an association in the recorded data; OScope cannot tell which came first."
    )
    evidence = [
        Evidence(
            f"history.group.{g.name}", g.name,
            f"in the top 3 in {g.hits} of {g.samples} moments with high memory use", EvidenceStrength.CORRELATION, "local history",
        )
        for g in history.memory_groups
    ]
    return text, evidence


RELATIONSHIP_RULES: tuple[Rule, ...] = (
    Rule("browser_memory", ("memory", "groups"), _browser_when, _browser_build),
    Rule("ram_and_disk", ("memory", "disk.read_bps", "disk.write_bps"), _ram_disk_when, _ram_disk_build),
    Rule("low_free_scratch_app", ("storage", "groups"), _scratch_when, _scratch_build),
    Rule("battery_load", ("power.on_battery", "power.mode"), _battery_when, _battery_build),
    Rule("sync_disk", ("disk.read_bps", "disk.write_bps", "groups"), _sync_when, _sync_build),
    Rule("many_startup", ("startup.enabled_count",), _startup_when, _startup_build),
    Rule(
        "gpu_heat", ("gpu.utilization", "temp.acpi_max"),
        lambda f: f.state("gpu.utilization", GPU_HIGH_PERCENT).high and f.value("temp.acpi_max") >= TEMP_WARM_C,
        _temp_load_build("GPU", "gpu.utilization", GPU_HIGH_PERCENT),
    ),
    Rule(
        "cpu_heat", ("cpu", "temp.acpi_max"),
        lambda f: f.state("cpu", CPU_HIGH_PERCENT).high and f.value("temp.acpi_max") >= TEMP_WARM_C,
        _temp_load_build("CPU", "cpu", CPU_HIGH_PERCENT),
    ),
    Rule("history_memory_groups", ("history",), _history_groups_when, _history_groups_build, only_for=frozenset({"changed"})),
    Rule(
        "load_without_sensors", ("cpu", "!temp.acpi_max"), _load_is_high, _load_without_sensors_build,
        only_for=frozenset({"heat"}),
    ),
)
