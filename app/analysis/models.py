"""Data shapes for explanations: evidence, findings (the four layers) and the result of a question."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Optional

from app.collectors.base import Reading


class EvidenceStrength(IntEnum):
    """How much a statement is allowed to claim. Higher = closer to a direct measurement."""

    UNVERIFIED = 0      # something we could not check
    INTERPRETATION = 1  # a reading of the data (not used for statements about this PC in the MVP)
    CORRELATION = 2     # seen together; says nothing about which caused which
    STRONG = 3          # several consistent measurements
    OBSERVED = 4        # a direct measurement


@dataclass(frozen=True)
class Evidence:
    """One line of supporting data, e.g. ``Google Chrome  -  3.1 GB (27 processes)``."""

    id: str
    label: str
    value_text: str
    strength: EvidenceStrength = EvidenceStrength.OBSERVED
    source: str = ""


@dataclass(frozen=True)
class Relation:
    """A "you may not have noticed" link between signals. Never worded as cause and effect."""

    rule_id: str
    text: str
    evidence: tuple[Evidence, ...] = ()
    strength: EvidenceStrength = EvidenceStrength.CORRELATION


@dataclass
class Finding:
    """One observation about the PC, in the diagnostic layers.

    Layer 1 ``happening``, layer 2 ``contributing`` (evidence), layer 4 ``consider`` (optional).
    Layer 3, the relationships, lives on the ``DiagnosticResult`` because it spans findings.
    """

    id: str
    level: str                              # "normal" | "info" | "warning" | "critical"
    title: str
    happening: str
    contributing: list[Evidence] = field(default_factory=list)
    consider: list[str] = field(default_factory=list)
    goto: Optional[str] = None              # name of the view that shows more ("storage")


@dataclass(frozen=True)
class Section:
    """A titled block of evidence rows (used by "Show me everything")."""

    title: str
    rows: tuple[Evidence, ...]


@dataclass
class DiagnosticResult:
    question_id: str
    question_text: str
    workload: str
    findings: list[Finding]
    relations: list[Relation]
    unchecked: list[Reading]                # what could not be measured, shown rather than hidden
    sections: list[Section] = field(default_factory=list)
    summary: str = ""
    notes: list[str] = field(default_factory=list)
    sample_count: int = 0
    window_seconds: float = 0.0
    generated_at: datetime = field(default_factory=datetime.now)
