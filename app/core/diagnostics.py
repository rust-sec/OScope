"""Rule-based diagnostic engine.

Each rule compares one measurement with a threshold from ``constants.py`` and
produces a *finding*: ``{"level": ..., "title": ..., "message": ...}``.

Levels: ``normal`` < ``info`` < ``warning`` < ``critical``.

The wording is deliberately cautious. A high reading tells us *that* a resource
is under pressure, not *why*, so messages say "possible" and "consider
reviewing" and never name a specific cause.
"""

from __future__ import annotations

from typing import Optional

from app.utils import constants as c

Finding = dict  # {"level": str, "title": str, "message": str}

LEVEL_ORDER = {"normal": 0, "info": 1, "warning": 2, "critical": 3}


def _finding(level: str, title: str, message: str) -> Finding:
    return {"level": level, "title": title, "message": message}


def evaluate(
    cpu_percent: Optional[float],
    memory_percent: Optional[float],
    storage_percent: Optional[float],
) -> list[Finding]:
    """Return every finding that applies, most severe first.

    A metric passed as ``None`` (unavailable) is skipped, never guessed.
    If nothing is wrong, a single ``normal`` finding is returned.
    """
    findings: list[Finding] = []

    if cpu_percent is not None and cpu_percent >= c.CPU_HIGH_PERCENT:
        level = "critical" if cpu_percent >= c.CPU_CRITICAL_PERCENT else "warning"
        findings.append(
            _finding(
                level,
                "High CPU Usage",
                f"CPU usage is currently {cpu_percent:.0f}% (threshold {c.CPU_HIGH_PERCENT}%). "
                "Possible resource-heavy processes may be running. "
                "Consider reviewing the Processes view.",
            )
        )

    if memory_percent is not None and memory_percent >= c.MEMORY_HIGH_PERCENT:
        level = "critical" if memory_percent >= c.MEMORY_CRITICAL_PERCENT else "warning"
        findings.append(
            _finding(
                level,
                "High Memory Usage",
                f"Memory usage is currently {memory_percent:.0f}% (threshold {c.MEMORY_HIGH_PERCENT}%). "
                "Consider reviewing the processes using the most memory.",
            )
        )

    if storage_percent is not None:
        free_percent = max(100.0 - storage_percent, 0.0)
        if storage_percent >= c.STORAGE_LOW_PERCENT:
            level = "critical" if storage_percent >= c.STORAGE_CRITICAL_PERCENT else "warning"
            findings.append(
                _finding(
                    level,
                    "Low Storage",
                    f"Only {free_percent:.0f}% of the system drive is available. "
                    "Potential storage pressure. Consider reviewing large files and folders.",
                )
            )
        elif storage_percent >= c.STORAGE_FULL_PERCENT:
            findings.append(
                _finding(
                    "info",
                    "Storage Getting Full",
                    f"{storage_percent:.0f}% of the system drive is in use "
                    f"({free_percent:.0f}% free). Consider reviewing large files and folders.",
                )
            )

    if not findings:
        return [_finding("normal", "System Normal", "Your system is currently operating normally.")]

    findings.sort(key=lambda f: LEVEL_ORDER[f["level"]], reverse=True)
    return findings


def worst_level(findings: list[Finding]) -> str:
    """Highest severity in a list of findings (``normal`` if empty)."""
    if not findings:
        return "normal"
    return max((f["level"] for f in findings), key=lambda lvl: LEVEL_ORDER[lvl])


def summary_lines(findings: list[Finding]) -> list[str]:
    """Plain-text lines for the report's DIAGNOSTIC SUMMARY section."""
    lines = []
    for finding in findings:
        if finding["level"] == "normal":
            lines.append("System is currently operating normally.")
        else:
            lines.append(f"[{finding['level'].upper()}] {finding['title']}: {finding['message']}")
    return lines
