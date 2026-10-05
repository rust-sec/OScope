"""Wording rules for explanations: what each strength of evidence is called, and what is never said."""

from __future__ import annotations

import re

from app.analysis.models import DiagnosticResult, Evidence, EvidenceStrength
from app.collectors import labels

# Three labels are enough for a non-technical reader.
_STRENGTH_LABELS = {
    EvidenceStrength.OBSERVED: "Measured",
    EvidenceStrength.STRONG: "Measured",
    EvidenceStrength.CORRELATION: "Seen together",
    EvidenceStrength.INTERPRETATION: "Seen together",
    EvidenceStrength.UNVERIFIED: "Could not verify",
}


def strength_label(strength: EvidenceStrength) -> str:
    return _STRENGTH_LABELS[strength]


# OScope may say that two things were seen together. It may not say that one made the other happen,
# so these words must never appear in anything it writes about THIS computer.
FORBIDDEN_WORDS = re.compile(
    r"\b(because|caused|causing|causes|due to|culprit|responsible|blame|fix|fixes|fixed|result of|resulting from)\b",
    re.IGNORECASE,
)


def find_forbidden(text: str) -> list[str]:
    """Causal wording found in ``text`` (empty list when the text is acceptable)."""
    return [match.group(0) for match in FORBIDDEN_WORDS.finditer(text)]


def result_texts(result: DiagnosticResult) -> list[str]:
    """Every sentence a result shows, for wording checks and plain-text export."""
    texts = [result.summary, *result.notes]
    for finding in result.findings:
        texts += [finding.title, finding.happening, *finding.consider]
        texts += _evidence_texts(finding.contributing)
    for relation in result.relations:
        texts.append(relation.text)
        texts += _evidence_texts(relation.evidence)
    for section in result.sections:
        texts += _evidence_texts(section.rows)
    for reading in result.unchecked:
        texts.append(reading.detail)
    return [t for t in texts if t]


def _evidence_texts(rows) -> list[str]:
    return [piece for row in rows for piece in (row.label, row.value_text)]


def result_to_text(result: DiagnosticResult) -> str:
    """Plain-text version of a result (the "Copy as text" button and the report)."""
    lines = [result.question_text, "=" * len(result.question_text), ""]
    lines.append(f"Based on {result.sample_count} samples over {result.window_seconds:.0f} s; workload: {result.workload}")
    lines += ["", "WHAT WE FOUND", result.summary]
    lines += [f"  Note: {note}" for note in result.notes]

    for finding in result.findings:
        lines += ["", f"{finding.title}  [{finding.level}]", f"  What is happening: {finding.happening}"]
        if finding.contributing:
            lines.append("  What is contributing:")
            lines += [_evidence_line(row) for row in finding.contributing]
        if finding.consider:
            lines.append("  What you could consider:")
            lines += [f"    - {item}" for item in finding.consider]

    for section in result.sections:
        lines += ["", section.title.upper()]
        lines += [_evidence_line(row) for row in section.rows]

    lines += ["", "THINGS YOU MAY NOT HAVE NOTICED"]
    if result.relations:
        for relation in result.relations:
            lines.append(f"  - {relation.text}")
            lines += [_evidence_line(row, indent="      ") for row in relation.evidence]
    else:
        lines.append("  No relationships between different measurements were found.")

    if result.unchecked:
        lines += ["", "COULD NOT CHECK"]
        for reading in result.unchecked:
            detail = f": {reading.detail}" if reading.detail else ""
            lines.append(f"  - {labels.title_for(reading.name)} ({reading.status.value}){detail}")
    return "\n".join(lines) + "\n"


def _evidence_line(row: Evidence, indent: str = "    ") -> str:
    return f"{indent}{row.label}: {row.value_text}  [{strength_label(row.strength)}]"
