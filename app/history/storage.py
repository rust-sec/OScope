"""Storage snapshots: remember what a folder scan found, and compare two scans of the same folder.

A snapshot keeps the totals, the exact bytes and file count per category, and the biggest folders
(down to three levels, at most 200). The comparison is deliberately careful: a folder that is missing
from a *cut-short* list is "unknown", never "zero", and scans that are not like-for-like (different
amounts readable, one elevated) say so.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from app.core import tree_utils
from app.core.tree_scanner import TreeScanResult
from app.history.records import Operation

DIR_LIST_LIMIT = 200
DIR_MAX_DEPTH = 3
CHANGES_SHOWN = 30


@dataclass(frozen=True)
class StorageSnapshot:
    id: int
    ts: int
    root: str
    total_size: int
    file_count: int
    dir_count: int
    denied_count: int
    skipped_count: int
    elevated: Optional[bool]
    dirs_truncated: bool
    categories: dict[str, tuple[int, int]] = field(default_factory=dict)   # category -> (bytes, files)
    dirs: dict[str, int] = field(default_factory=dict)                      # folder path -> bytes


@dataclass(frozen=True)
class CategoryChange:
    category: str
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before


@dataclass(frozen=True)
class DirChange:
    path: str
    before: Optional[int]
    after: Optional[int]
    delta: Optional[int]    # None = cannot be known from the stored lists
    note: str = ""


@dataclass(frozen=True)
class StorageComparison:
    older: StorageSnapshot
    newer: StorageSnapshot
    total_delta: int
    categories: list[CategoryChange]
    changed_dirs: list[DirChange]
    warnings: list[str]


def same_root(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


# --------------------------------------------------------------------------- #
# saving
# --------------------------------------------------------------------------- #
def snapshot_operation(result: TreeScanResult, ts: int, elevated: Optional[bool] = None) -> Optional[Operation]:
    """A writer operation that stores ``result``, or None when the scan is not worth remembering.

    The tree is read here, on the caller's thread, so the writer thread only runs SQL.
    """
    if result.error or result.cancelled or result.tree is None:
        return None
    candidates = tree_utils.top_dirs(result.tree, DIR_MAX_DEPTH, DIR_LIST_LIMIT + 1)
    truncated = len(candidates) > DIR_LIST_LIMIT
    dirs = [(path, size, depth) for path, size, depth in candidates[:DIR_LIST_LIMIT]]
    categories = [(name, size, count) for name, (size, count) in result.category_totals.items()]
    row = (
        ts, result.root, result.total_size, result.file_count, result.dir_count, result.denied_count,
        result.skipped_count, None if elevated is None else int(elevated), int(truncated),
    )

    def operation(conn: sqlite3.Connection) -> None:
        cursor = conn.execute(
            "INSERT INTO storage_snapshots(ts, root, total_size, file_count, dir_count, denied_count, skipped_count, "
            "elevated, dirs_truncated) VALUES (?,?,?,?,?,?,?,?,?)",
            row,
        )
        snapshot_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO storage_categories VALUES (?,?,?,?)", [(snapshot_id, n, s, c) for n, s, c in categories]
        )
        conn.executemany("INSERT INTO storage_dirs VALUES (?,?,?,?)", [(snapshot_id, p, s, d) for p, s, d in dirs])

    return operation


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def _snapshot(row: sqlite3.Row, categories: dict, dirs: dict) -> StorageSnapshot:
    return StorageSnapshot(
        id=row["id"], ts=row["ts"], root=row["root"], total_size=row["total_size"], file_count=row["file_count"],
        dir_count=row["dir_count"], denied_count=row["denied_count"], skipped_count=row["skipped_count"],
        elevated=None if row["elevated"] is None else bool(row["elevated"]),
        dirs_truncated=bool(row["dirs_truncated"]), categories=categories, dirs=dirs,
    )


def list_snapshots(conn: sqlite3.Connection, root: str, limit: int = 20) -> list[StorageSnapshot]:
    """Snapshots of ``root`` (newest first), without their per-category and per-folder detail."""
    rows = conn.execute("SELECT * FROM storage_snapshots ORDER BY ts DESC, id DESC").fetchall()
    return [_snapshot(r, {}, {}) for r in rows if same_root(r["root"], root)][:limit]


def load_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> Optional[StorageSnapshot]:
    row = conn.execute("SELECT * FROM storage_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if row is None:
        return None
    categories = {
        r["category"]: (r["size"], r["file_count"])
        for r in conn.execute("SELECT * FROM storage_categories WHERE snapshot_id = ?", (snapshot_id,))
    }
    dirs = {r["path"]: r["size"] for r in conn.execute("SELECT * FROM storage_dirs WHERE snapshot_id = ?", (snapshot_id,))}
    return _snapshot(row, categories, dirs)


# --------------------------------------------------------------------------- #
# comparing
# --------------------------------------------------------------------------- #
def compare(older: StorageSnapshot, newer: StorageSnapshot) -> StorageComparison:
    """What changed between two scans of the same folder. Pure."""
    if not same_root(older.root, newer.root):
        raise ValueError("only two scans of the same folder can be compared")

    categories = [
        CategoryChange(name, older.categories.get(name, (0, 0))[0], newer.categories.get(name, (0, 0))[0])
        for name in sorted(set(older.categories) | set(newer.categories))
    ]
    categories.sort(key=lambda change: abs(change.delta), reverse=True)

    known: list[DirChange] = []
    unknown: list[DirChange] = []
    for path in set(older.dirs) | set(newer.dirs):
        before, after = older.dirs.get(path), newer.dirs.get(path)
        if before is not None and after is not None:
            known.append(DirChange(path, before, after, after - before))
        elif after is not None:      # only in the newer scan
            if older.dirs_truncated:
                unknown.append(DirChange(path, None, after, None, "not in the older scan's list of biggest folders"))
            else:
                known.append(DirChange(path, 0, after, after, "new folder"))
        else:                        # only in the older scan
            if newer.dirs_truncated:
                unknown.append(DirChange(path, before, None, None, "not in the newer scan's list of biggest folders"))
            else:
                known.append(DirChange(path, before, 0, -before, "folder no longer present"))
    known = [change for change in known if change.delta]
    known.sort(key=lambda change: abs(change.delta or 0), reverse=True)
    unknown.sort(key=lambda change: change.after or change.before or 0, reverse=True)

    warnings: list[str] = []
    if older.denied_count != newer.denied_count:
        warnings.append(
            f"Different amounts could be read ({older.denied_count} unreadable items before, {newer.denied_count} now), "
            "so part of a change may come from access rather than from the files themselves."
        )
    if older.elevated != newer.elevated and older.elevated is not None and newer.elevated is not None:
        warnings.append("One scan ran as administrator and the other did not, so they are not like-for-like.")
    if older.dirs_truncated or newer.dirs_truncated:
        warnings.append("Only the biggest folders are kept for each scan, so small folders cannot be compared.")

    return StorageComparison(
        older=older, newer=newer, total_delta=newer.total_size - older.total_size,
        categories=categories, changed_dirs=known[:CHANGES_SHOWN] + unknown[:CHANGES_SHOWN], warnings=warnings,
    )
