"""The questions an ordinary user actually asks, and which evidence answers each one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.analysis.detectors import DETECTORS


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    hint: str                                   # one line under the button
    detectors: tuple[str, ...]                  # which signals to look at
    always_show: tuple[str, ...]                # findings shown even when nothing is wrong
    rules: Optional[frozenset[str]] = None      # relationship rules that belong here (None = all that apply)


QUESTIONS: dict[str, Question] = {
    "slow": Question(
        "slow",
        "Why is my PC slow?",
        "Looks at CPU, memory, disk, GPU, storage, power and startup together.",
        ("cpu", "memory", "disk", "gpu", "storage", "power", "startup"),
        (),
    ),
    "ram": Question(
        "ram",
        "Why is my RAM full?",
        "What is using memory, and whether Windows is running short.",
        ("memory",),
        ("memory",),
        frozenset({"browser_memory", "ram_and_disk", "many_startup"}),
    ),
    "heat": Question(
        "heat",
        "Why is my laptop hot or loud?",
        "Temperature and fan readings if this PC exposes them, plus the load that usually goes with heat.",
        ("thermal", "cpu", "gpu", "power"),
        ("thermal",),
        frozenset({"gpu_heat", "cpu_heat", "load_without_sensors", "battery_load"}),
    ),
    "storage": Question(
        "storage",
        "Where did my storage go?",
        "Free space on the system drive; the Storage view shows exactly which folders take it.",
        ("storage",),
        ("storage",),
        frozenset({"low_free_scratch_app"}),
    ),
    "everything": Question(
        "everything",
        "I don't know what's wrong. Show me everything.",
        "A broad snapshot of everything OScope can see, and what it cannot.",
        tuple(DETECTORS),
        tuple(DETECTORS),
    ),
}

QUESTION_ORDER = ("slow", "ram", "heat", "storage", "everything")
