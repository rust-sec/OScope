"""SQLite storage for OScope's local history: schema, versioned migrations, safe opening.

Everything stays in one file in the per-user app-data folder. Nothing here talks to a network.
Only names and numbers are stored (process names, totals, folder paths the user chose to scan);
never command lines, window titles, user names or file contents.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional, Sequence

_LOG = logging.getLogger("oscope.history")
DB_FILENAME = "history.db"

SCHEMA_V1 = """
CREATE TABLE metric_samples(
    ts INTEGER PRIMARY KEY,               -- unix seconds, one row per ~30 s of recording
    cpu REAL, mem_pct REAL, commit_pct REAL,
    disk_read_bps REAL, disk_write_bps REAL,
    gpu_pct REAL, temp_c REAL,
    on_battery INTEGER,
    free_bytes INTEGER, total_bytes INTEGER,   -- system drive
    samples INTEGER NOT NULL               -- how many live samples were averaged into this row
);
CREATE TABLE process_top(
    ts INTEGER NOT NULL, rank INTEGER NOT NULL,   -- rank 1 = biggest working-set total
    name TEXT NOT NULL, proc_count INTEGER NOT NULL,
    cpu REAL, mem_bytes INTEGER,
    PRIMARY KEY(ts, rank)
);
CREATE INDEX ix_process_top_ts ON process_top(ts);
CREATE TABLE storage_snapshots(
    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, root TEXT NOT NULL,
    total_size INTEGER NOT NULL, file_count INTEGER NOT NULL, dir_count INTEGER NOT NULL,
    denied_count INTEGER NOT NULL, skipped_count INTEGER NOT NULL,
    elevated INTEGER
);
CREATE INDEX ix_storage_root_ts ON storage_snapshots(root, ts);
CREATE TABLE storage_categories(
    snapshot_id INTEGER NOT NULL REFERENCES storage_snapshots(id) ON DELETE CASCADE,
    category TEXT NOT NULL, size INTEGER NOT NULL, file_count INTEGER NOT NULL,
    PRIMARY KEY(snapshot_id, category)
);
CREATE TABLE storage_dirs(
    snapshot_id INTEGER NOT NULL REFERENCES storage_snapshots(id) ON DELETE CASCADE,
    path TEXT NOT NULL, size INTEGER NOT NULL, depth INTEGER NOT NULL,
    PRIMARY KEY(snapshot_id, path)
);
CREATE TABLE diagnostic_events(
    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL,
    question TEXT NOT NULL, workload TEXT NOT NULL,
    finding_id TEXT NOT NULL, level TEXT NOT NULL, title TEXT NOT NULL,
    evidence_json TEXT NOT NULL
);
CREATE INDEX ix_events_ts ON diagnostic_events(ts);
"""

# v2: remember whether a snapshot's list of biggest folders was cut short, so a folder missing from it is
# understood as "unknown", not "zero".
SCHEMA_V2 = """
ALTER TABLE storage_snapshots ADD COLUMN dirs_truncated INTEGER NOT NULL DEFAULT 0;
"""

# (version, script). Append new versions; never edit an old one.
MIGRATIONS: list[tuple[int, str]] = [(1, SCHEMA_V1), (2, SCHEMA_V2)]

DATA_TABLES = ("metric_samples", "process_top", "storage_categories", "storage_dirs", "storage_snapshots", "diagnostic_events")


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection, migrations: Sequence[tuple[int, str]] = ()) -> int:
    """Bring the database up to the newest schema, one transaction per version. Returns the version."""
    chosen = list(migrations or MIGRATIONS)
    current = schema_version(conn)
    for version, script in sorted(chosen):
        if version <= current:
            continue
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {int(version)};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise
        current = version
    return current


def open_connection(path: Path, read_only: bool = False) -> sqlite3.Connection:
    """Open the database with OScope's settings. Raises sqlite3.Error if the file is unusable."""
    if read_only:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2.0)
    else:
        conn = sqlite3.connect(str(path), timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def open_or_recreate(path: Path, clock=time.time) -> tuple[sqlite3.Connection, bool]:
    """Open and migrate ``path``. A corrupt file is renamed aside and replaced; returns (conn, recreated)."""
    try:
        conn = open_connection(path)
        migrate(conn)
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()  # touches the file: corruption shows up here
        return conn, False
    except sqlite3.OperationalError:
        # "database is locked" (another OScope instance), "unable to open" (folder missing/unwritable):
        # the data may be perfectly healthy, so it is never moved or deleted for these.
        raise
    except sqlite3.DatabaseError as exc:  # "file is not a database", "disk image is malformed": real corruption
        _LOG.warning("History database %s is corrupt (%s); starting a new one", path, exc)
        _set_aside(path, clock)
        conn = open_connection(path)
        migrate(conn)
        return conn, True


def _set_aside(path: Path, clock) -> Optional[Path]:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        path.replace(target)
    except OSError:
        return None
    for suffix in ("-wal", "-shm"):  # stale WAL files belong to the broken database
        try:
            Path(str(path) + suffix).unlink()
        except OSError:
            pass
    return target
