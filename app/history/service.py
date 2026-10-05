"""The one object the app talks to for history: record, read back, clear, report status."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional

from app.analysis.models import DiagnosticResult
from app.core import platform_ops
from app.core.sampler import Snapshot
from app.history import queries, retention
from app.history.db import DB_FILENAME, open_connection
from app.history.queries import HistoryContext
from app.history.records import EventRow, Operation, insert_event
from app.history.recorder import HistoryRecorder
from app.history.writer import HistoryWriter

_LOG = logging.getLogger("oscope.history")
EVENT_REPEAT_SECONDS = 600  # the same finding is not logged again for 10 minutes
EVIDENCE_ROWS_KEPT = 8


class HistoryService:
    """Local history on/off switch plus everything behind it. Safe to call when history is unavailable."""

    def __init__(
        self,
        directory: Optional[Path],
        enabled: bool = True,
        clock: Callable[[], float] = time.time,
        commit_interval: Optional[float] = None,
    ) -> None:
        self._path = directory / DB_FILENAME if directory is not None else None
        self._enabled = enabled
        self._clock = clock
        self._commit_interval = commit_interval
        self._writer: Optional[HistoryWriter] = None
        self._recorder: Optional[HistoryRecorder] = None
        self._last_event: dict[tuple[str, str], float] = {}

    @classmethod
    def default(cls, enabled: bool = True) -> "HistoryService":
        return cls(platform_ops.app_data_dir(), enabled)

    # -- state ---------------------------------------------------------------------------------
    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Turn recording on or off. Turning it off keeps what is already stored (use ``clear`` to delete)."""
        self._enabled = enabled

    @property
    def writer(self) -> Optional[HistoryWriter]:
        return self._writer

    def status_text(self) -> str:
        if self._path is None:
            return "History is unavailable: there is no writable folder for it."
        if not self._enabled:
            return "History is off. Nothing new is being remembered."
        if self._writer is not None and self._writer.ready.is_set() and not self._writer.available:
            return f"History is unavailable ({self._writer.last_error or 'unknown error'})."
        return f"History is on. Kept on this PC only, in {self._path}."

    # -- recording -------------------------------------------------------------------------------
    def on_snapshot(self, snapshot: Snapshot) -> None:
        if not self._enabled or not self._ensure_started():
            return
        assert self._recorder is not None
        self._recorder.on_snapshot(snapshot)

    def record_result(self, result: DiagnosticResult) -> int:
        """Remember the notable findings of an answer (not the normal ones). Returns how many were queued."""
        if not self._enabled or not self._ensure_started():
            return 0
        now = self._clock()
        queued = 0
        for finding in result.findings:
            if finding.level == "normal":
                continue
            key = (finding.id, finding.level)
            if now - self._last_event.get(key, -1e18) < EVENT_REPEAT_SECONDS:
                continue
            self._last_event[key] = now
            row = EventRow(
                int(now), result.question_id, result.workload, finding.id, finding.level, finding.title,
                [{"label": e.label, "value": e.value_text} for e in finding.contributing[:EVIDENCE_ROWS_KEPT]],
            )
            self._enqueue(lambda conn, r=row: insert_event(conn, r))
            queued += 1
        return queued

    def _enqueue(self, operation: Operation) -> None:
        assert self._writer is not None
        self._writer.enqueue(operation)

    def _ensure_started(self) -> bool:
        if self._path is None:
            return False
        if self._writer is None:
            kwargs = {"clock": self._clock}
            if self._commit_interval is not None:
                kwargs["commit_interval"] = self._commit_interval
            self._writer = HistoryWriter(self._path, **kwargs)
            self._writer.start()
            self._recorder = HistoryRecorder(self._enqueue, self._clock)
        return not (self._writer.ready.is_set() and not self._writer.available)

    # -- reading -----------------------------------------------------------------------------------
    def context(self, hours: int = 24) -> Optional[HistoryContext]:
        """What happened recently, for "What changed?". May block briefly: call from a worker thread."""
        if self._path is None:
            return None
        if self._writer is not None:
            self._writer.flush(timeout=3.0)  # waits for the database to open, then makes the newest rows readable
        if not self._path.is_file():
            return None
        try:
            conn = open_connection(self._path, read_only=True)
        except sqlite3.Error:
            return None
        try:
            return queries.build_context(conn, int(self._clock()), hours)
        except sqlite3.Error as exc:
            _LOG.warning("Could not read history: %s", exc)
            return None
        finally:
            conn.close()

    # -- housekeeping ------------------------------------------------------------------------------
    def clear(self, timeout: float = 5.0) -> bool:
        """Delete everything remembered so far. Returns True when it is gone."""
        if self._path is None:
            return False
        if self._writer is None and self._path.is_file():
            self._ensure_started()
        if self._writer is None:
            return True  # nothing was ever stored
        self._last_event.clear()
        self._writer.enqueue(lambda conn: retention.clear_all(conn))
        return self._writer.flush(timeout)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
