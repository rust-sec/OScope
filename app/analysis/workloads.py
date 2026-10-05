"""Workload contexts: what the user is mostly doing decides which evidence is shown FIRST.

A workload only changes ordering. It never changes a threshold, never hides a finding, and never
turns a measurement into a verdict: a critical finding always comes before a merely interesting one.
"""

from __future__ import annotations

DEFAULT_WORKLOAD = "general"

WORKLOADS: dict[str, str] = {
    "general": "General heavy use",
    "gaming": "Gaming",
    "video": "Video editing",
    "photoshop": "Photoshop / design",
    "audio": "Music / audio",
    "rendering": "Rendering / 3D",
}

# Finding ids and rule ids to show first (within the same severity), most important first.
_PRIORITY: dict[str, tuple[str, ...]] = {
    "general": (),
    "gaming": ("gpu", "thermal", "gpu_heat", "cpu_heat", "battery_load", "sync_disk", "browser_memory"),
    "video": ("disk", "storage", "gpu", "memory", "low_free_scratch_app", "ram_and_disk"),
    "photoshop": ("memory", "storage", "low_free_scratch_app", "browser_memory", "ram_and_disk"),
    "audio": ("cpu", "power", "battery_load", "sync_disk", "many_startup"),
    "rendering": ("cpu", "gpu", "thermal", "cpu_heat", "gpu_heat", "battery_load"),
}


def rank(workload: str, item_id: str) -> int:
    """Sort key: lower comes first. Items the workload does not mention keep their default order, after the rest."""
    priority = _PRIORITY.get(workload, ())
    return priority.index(item_id) if item_id in priority else len(priority)


def label(workload: str) -> str:
    return WORKLOADS.get(workload, WORKLOADS[DEFAULT_WORKLOAD])
