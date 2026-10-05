"""Rows OScope keeps, and the small functions that write them. Writers run on the history thread only."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

# An "operation" is anything the writer thread runs inside its transaction.
Operation = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class MetricRow:
    ts: int
    cpu: Optional[float]
    mem_pct: Optional[float]
    commit_pct: Optional[float]
    disk_read_bps: Optional[float]
    disk_write_bps: Optional[float]
    gpu_pct: Optional[float]
    temp_c: Optional[float]
    on_battery: Optional[bool]
    free_bytes: Optional[int]
    total_bytes: Optional[int]
    samples: int


@dataclass(frozen=True)
class ProcessTopRow:
    ts: int
    rank: int
    name: str
    proc_count: int
    cpu: Optional[float]
    mem_bytes: Optional[int]


@dataclass(frozen=True)
class EventRow:
    ts: int
    question: str
    workload: str
    finding_id: str
    level: str
    title: str
    evidence: list  # decoded evidence_json: [{"label": ..., "value": ...}]


def insert_metric(conn: sqlite3.Connection, row: MetricRow) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO metric_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            row.ts, row.cpu, row.mem_pct, row.commit_pct, row.disk_read_bps, row.disk_write_bps, row.gpu_pct,
            row.temp_c, None if row.on_battery is None else int(row.on_battery), row.free_bytes, row.total_bytes,
            row.samples,
        ),
    )


def insert_process_top(conn: sqlite3.Connection, rows: list[ProcessTopRow]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO process_top VALUES (?,?,?,?,?,?)",
        [(r.ts, r.rank, r.name, r.proc_count, r.cpu, r.mem_bytes) for r in rows],
    )


def insert_event(conn: sqlite3.Connection, row: EventRow) -> None:
    conn.execute(
        "INSERT INTO diagnostic_events(ts, question, workload, finding_id, level, title, evidence_json) VALUES (?,?,?,?,?,?,?)",
        (row.ts, row.question, row.workload, row.finding_id, row.level, row.title, json.dumps(row.evidence)),
    )
