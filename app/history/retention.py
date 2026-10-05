"""Keep the history small: old rows are deleted on a schedule, and the user can clear everything."""

from __future__ import annotations

import sqlite3

from app.history.db import DATA_TABLES

METRIC_DAYS = 14
PROCESS_DAYS = 7
EVENT_DAYS = 90
STORAGE_SNAPSHOTS_PER_ROOT = 20
_DAY = 86400


def prune(
    conn: sqlite3.Connection,
    now: int,
    metric_days: int = METRIC_DAYS,
    process_days: int = PROCESS_DAYS,
    event_days: int = EVENT_DAYS,
    snapshots_per_root: int = STORAGE_SNAPSHOTS_PER_ROOT,
) -> dict[str, int]:
    """Delete rows past their retention period. Returns how many rows each table lost."""
    removed: dict[str, int] = {}
    with conn:
        removed["metric_samples"] = conn.execute(
            "DELETE FROM metric_samples WHERE ts < ?", (now - metric_days * _DAY,)
        ).rowcount
        removed["process_top"] = conn.execute(
            "DELETE FROM process_top WHERE ts < ?", (now - process_days * _DAY,)
        ).rowcount
        removed["diagnostic_events"] = conn.execute(
            "DELETE FROM diagnostic_events WHERE ts < ?", (now - event_days * _DAY,)
        ).rowcount
        # keep only the newest N snapshots of each scanned folder (children go with them via ON DELETE CASCADE)
        removed["storage_snapshots"] = conn.execute(
            """
            DELETE FROM storage_snapshots WHERE id IN (
                SELECT s.id FROM storage_snapshots s
                WHERE (SELECT COUNT(*) FROM storage_snapshots t
                       WHERE t.root = s.root AND (t.ts > s.ts OR (t.ts = s.ts AND t.id > s.id))) >= ?
            )
            """,
            (snapshots_per_root,),
        ).rowcount
    return removed


def clear_all(conn: sqlite3.Connection) -> None:
    """Delete everything OScope has remembered (the schema stays)."""
    with conn:
        for table in DATA_TABLES:
            conn.execute(f"DELETE FROM {table}")
