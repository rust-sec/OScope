"""Answer a question: gather evidence, run the detectors and rules, and assemble the layered result.

``run_question`` is a pure function of its inputs (a snapshot, the recent window, a workload).
It measures nothing itself and has no side effects, so it is easy to test and safe to call anywhere.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from app.analysis import workloads
from app.analysis.detectors import DETECTOR_READINGS, DETECTORS, group_evidence, count_suffix, missing_reading
from app.analysis.facts import Facts
from app.analysis.models import DiagnosticResult, Evidence, EvidenceStrength, Section
from app.analysis.questions import QUESTIONS
from app.analysis.rules import RELATIONSHIP_RULES
from app.analysis.trends import MIN_SUSTAINED_SAMPLES
from app.collectors import labels
from app.collectors.base import Availability, Reading
from app.core.diagnostics import LEVEL_ORDER
from app.core.sampler import SampleRecord, Snapshot
from app.utils.constants import ACTIVE_CPU_PERCENT, TOP_GROUPS_SHOWN
from app.utils.formatting import format_bytes, format_duration, format_used_of_total


def run_question(
    question_id: str,
    snapshot: Snapshot,
    window: Sequence[SampleRecord],
    workload: str = workloads.DEFAULT_WORKLOAD,
) -> DiagnosticResult:
    question = QUESTIONS[question_id]
    facts = Facts(snapshot, window, workload)

    # layers 1, 2 and 4: one finding per signal this question looks at
    findings = [DETECTORS[name](facts) for name in question.detectors]
    findings = [f for f in findings if f.level != "normal" or f.id in question.always_show]
    findings = _order(findings, workload, key=lambda f: (-LEVEL_ORDER[f.level], workloads.rank(workload, f.id)))

    # layer 3: relationships between signals
    relations = []
    for rule in RELATIONSHIP_RULES:
        if question.rules is not None and rule.id not in question.rules:
            continue
        if rule.only_for is not None and question_id not in rule.only_for:
            continue
        relation = rule.evaluate(facts)
        if relation is not None:
            relations.append(relation)
    relations = _order(relations, workload, key=lambda r: workloads.rank(workload, r.rule_id))

    result = DiagnosticResult(
        question_id=question.id,
        question_text=question.text,
        workload=workloads.label(workload),
        findings=findings,
        relations=relations,
        unchecked=_unchecked(question.detectors, snapshot),
        sections=_everything_sections(facts) if question_id == "everything" else [],
        notes=_notes(facts),
        sample_count=facts.samples,
        window_seconds=facts.seconds,
        generated_at=datetime.now(),
    )
    result.summary = _summary(result)
    return result


def _order(items: list, workload: str, key) -> list:
    """Stable sort: severity first, then the workload's emphasis, then the default order."""
    return sorted(items, key=key)


def _unchecked(detector_names: Sequence[str], snapshot: Snapshot) -> list[Reading]:
    """Everything this answer would have used but could not obtain."""
    gaps: list[Reading] = []
    if snapshot.cpu_percent is None and "cpu" in detector_names:
        gaps.append(Reading.missing("cpu.percent", Availability.UNAVAILABLE, "CPU load could not be read"))
    if snapshot.memory is None and "memory" in detector_names:
        gaps.append(Reading.missing("memory.percent", Availability.UNAVAILABLE, "memory use could not be read"))
    if snapshot.storage is None and "storage" in detector_names:
        gaps.append(Reading.missing("storage.percent", Availability.UNAVAILABLE, "drive space could not be read"))
    seen: set[str] = set()
    for detector in detector_names:
        for name in DETECTOR_READINGS.get(detector, ()):
            if name in seen:
                continue
            seen.add(name)
            gap = missing_reading(name, snapshot.readings)
            if gap is not None:
                gaps.append(gap)
    return gaps


def _notes(facts: Facts) -> list[str]:
    notes = []
    if facts.samples < MIN_SUSTAINED_SAMPLES:
        notes.append(
            "OScope has only just started collecting, so it is describing the current moment rather than a trend. "
            "Ask again in a minute for a better picture."
        )
    return notes


def _summary(result: DiagnosticResult) -> str:
    notable = [f.title for f in result.findings if f.level != "normal"]
    if notable:
        text = "Worth a look: " + "; ".join(notable) + "."
    else:
        text = "Nothing unusual was observed in the data OScope could read."
    if result.unchecked:
        count = len(result.unchecked)
        text += f" {count} thing{'s' if count != 1 else ''} could not be checked on this PC (listed below)."
    return text


def _everything_sections(facts: Facts) -> list[Section]:
    """"Show me everything": the same evidence, laid out as plain sections."""
    snap = facts.snapshot
    now: list[Evidence] = []
    if snap.cpu_percent is not None:
        now.append(Evidence("cpu.now", "CPU load", f"{snap.cpu_percent:.0f}%"))
    if snap.memory is not None:
        now.append(Evidence("mem.now", "Memory", f"{format_used_of_total(snap.memory.used, snap.memory.total)} ({snap.memory.percent:.0f}%)"))
    if snap.storage is not None:
        now.append(Evidence("storage.now", f"System drive {snap.storage.path}", f"{format_bytes(snap.storage.free)} free of {format_bytes(snap.storage.total)}"))
    if snap.uptime_seconds is not None:
        now.append(Evidence("uptime", "Up for", format_duration(snap.uptime_seconds)))

    sections = [Section("Right now", tuple(now))]
    top_memory = facts.top_by_memory(TOP_GROUPS_SHOWN)
    if top_memory:
        sections.append(Section(
            "Programs holding the most memory (working set total)",
            tuple(group_evidence(g, f"{format_bytes(g.memory_bytes)}{count_suffix(g)}") for g in top_memory),
        ))
    top_cpu = facts.top_by_cpu(TOP_GROUPS_SHOWN, ACTIVE_CPU_PERCENT)
    if top_cpu:
        sections.append(Section(
            "Programs using the most CPU",
            tuple(group_evidence(g, f"{g.cpu_percent:.0f}%{count_suffix(g)}") for g in top_cpu),
        ))

    sources = []
    for name, title in labels.EVIDENCE_ROWS:
        value, status = labels.describe(snap.readings, name)
        if value:
            sources.append(Evidence(name, title, value, EvidenceStrength.OBSERVED))
        else:
            detail = labels.detail_for(snap.readings, name)
            sources.append(Evidence(name, title, f"Could not verify. {status}" + (f": {detail}" if detail else ""), EvidenceStrength.UNVERIFIED))
    sections.append(Section("What OScope can measure on this PC", tuple(sources)))
    return sections
