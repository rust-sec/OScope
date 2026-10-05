"""Reading the history back. Read-only; every function takes an open connection."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from app.history.records import EventRow, MetricRow
from app.utils.constants import MEMORY_HIGH_PERCENT

_HOUR = 3600


@dataclass(frozen=True)
class GroupCorrelation:
    """How often a program was among the biggest memory users while memory use was high."""

    name: str
    hits: int       # recorded moments with high memory where this program was in the top N
    samples: int    # recorded moments with high memory that have program data


@dataclass(frozen=True)
class HistoryContext:
    """Everything the "What changed?" answer may use. Plain data, so the analysis stays pure."""

    now: int
    metrics: tuple[MetricRow, ...]              # oldest first
    events: tuple[EventRow, ...]                # oldest first
    memory_groups: tuple[GroupCorrelation, ...]
    memory_high_samples: int


def _metric(row: sqlite3.Row) -> MetricRow:
    return MetricRow(
        ts=row["ts"], cpu=row["cpu"], mem_pct=row["mem_pct"], commit_pct=row["commit_pct"],
        disk_read_bps=row["disk_read_bps"], disk_write_bps=row["disk_write_bps"], gpu_pct=row["gpu_pct"],
        temp_c=row["temp_c"], on_battery=None if row["on_battery"] is None else bool(row["on_battery"]),
        free_bytes=row["free_bytes"], total_bytes=row["total_bytes"], samples=row["samples"],
    )


def recent_metrics(conn: sqlite3.Connection, since: int) -> list[MetricRow]:
    rows = conn.execute("SELECT * FROM metric_samples WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
    return [_metric(r) for r in rows]


def recent_events(conn: sqlite3.Connection, since: int) -> list[EventRow]:
    rows = conn.execute("SELECT * FROM diagnostic_events WHERE ts >= ? ORDER BY ts, id", (since,)).fetchall()
    events = []
    for r in rows:
        try:
            evidence = json.loads(r["evidence_json"])
        except ValueError:
            evidence = []
        events.append(EventRow(r["ts"], r["question"], r["workload"], r["finding_id"], r["level"], r["title"], evidence))
    return events


def memory_high_groups(
    conn: sqlite3.Connection, since: int, threshold: float = MEMORY_HIGH_PERCENT, top_n: int = 3, limit: int = 5
) -> tuple[list[GroupCorrelation], int]:
    """Programs most often in the top ``top_n`` memory users at recorded moments when memory use was high."""
    samples = conn.execute(
        """SELECT COUNT(DISTINCT m.ts) FROM metric_samples m JOIN process_top p ON p.ts = m.ts
           WHERE m.ts >= ? AND m.mem_pct >= ?""",
        (since, threshold),
    ).fetchone()[0]
    rows = conn.execute(
        """SELECT p.name AS name, COUNT(*) AS hits FROM process_top p JOIN metric_samples m ON m.ts = p.ts
           WHERE m.ts >= ? AND m.mem_pct >= ? AND p.rank <= ?
           GROUP BY p.name ORDER BY hits DESC, p.name LIMIT ?""",
        (since, threshold, top_n, limit),
    ).fetchall()
    return [GroupCorrelation(r["name"], r["hits"], samples) for r in rows], samples


def build_context(conn: sqlite3.Connection, now: int, hours: int = 24) -> HistoryContext:
    since = now - hours * _HOUR
    groups, samples = memory_high_groups(conn, since)
    return HistoryContext(
        now=now,
        metrics=tuple(recent_metrics(conn, since)),
        events=tuple(recent_events(conn, since)),
        memory_groups=tuple(groups),
        memory_high_samples=samples,
    )
