"""The single thread that writes to the history database.

All SQLite writes happen here so the GUI thread never waits on disk. Work arrives as small
operations on a bounded queue (the oldest is dropped, and counted, if the queue ever fills) and is
committed in batches. Any database error is contained: history just becomes "unavailable" and the
rest of OScope carries on.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from app.history import retention
from app.history.db import open_or_recreate
from app.history.records import Operation

_LOG = logging.getLogger("oscope.history")
COMMIT_INTERVAL_SECONDS = 30.0
PRUNE_INTERVAL_SECONDS = 24 * 3600.0
MAX_QUEUE = 2000


class HistoryWriter(threading.Thread):
    def __init__(
        self,
        path: Path,
        commit_interval: float = COMMIT_INTERVAL_SECONDS,
        max_queue: int = MAX_QUEUE,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(name="oscope-history", daemon=True)
        self._path = path
        self._commit_interval = commit_interval
        self._clock = clock
        self._queue: "queue.Queue[Operation]" = queue.Queue(maxsize=max_queue)
        self._stop_event = threading.Event()
        self._flush_event = threading.Event()
        self.ready = threading.Event()          # set once the database is open (or has failed to open)
        self.available = False                  # True once the database is open and usable
        self.last_error: Optional[str] = None
        self.dropped = 0
        self.batches_written = 0
        self.recreated = False
        self.thread_name_of_last_write: Optional[str] = None

    # -- called from any thread --------------------------------------------------------------
    def enqueue(self, operation: Operation) -> None:
        """Queue an operation without ever blocking the caller. If full, the oldest one is dropped."""
        while True:
            try:
                self._queue.put_nowait(operation)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass

    def flush(self, timeout: float = 5.0) -> bool:
        """Ask for an immediate commit and wait for it. Returns True if the queue was written out."""
        if not self.ready.wait(timeout) or not self.available:
            return False  # the database never opened, so there is nothing to wait for
        done = threading.Event()
        self.enqueue(lambda _conn: done.set())  # runs after everything queued before it
        self._flush_event.set()
        return done.wait(timeout)

    def close(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._flush_event.set()
        if self.is_alive():
            self.join(timeout)

    # -- thread body --------------------------------------------------------------------------
    def run(self) -> None:
        try:
            conn, self.recreated = open_or_recreate(self._path, self._clock)
        except (sqlite3.Error, OSError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            _LOG.warning("History is unavailable: %s", self.last_error)
            self.ready.set()
            return
        self.available = True
        self.ready.set()
        try:
            self._prune(conn)
            self._loop(conn)
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _loop(self, conn: sqlite3.Connection) -> None:
        pending: list[Operation] = []
        last_commit = self._clock()
        last_prune = last_commit
        while True:
            # Read and clear the flush request BEFORE draining: flush() queues its operation first and
            # sets the event second, so a request seen here always has its operation already waiting.
            flush_requested = self._flush_event.is_set()
            self._flush_event.clear()
            try:
                pending.append(self._queue.get(timeout=0.5))
            except queue.Empty:
                pass
            while True:  # drain whatever else is waiting
                try:
                    pending.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            stopping = self._stop_event.is_set()
            now = self._clock()
            due = now - last_commit >= self._commit_interval or flush_requested or stopping
            if pending and due:
                self._write(conn, pending)
                pending = []
                last_commit = now
            if now - last_prune >= PRUNE_INTERVAL_SECONDS:
                self._prune(conn)
                last_prune = now
            if stopping and self._queue.empty() and not pending:
                return

    def _write(self, conn: sqlite3.Connection, operations: list[Operation]) -> None:
        try:
            with conn:  # one transaction per batch: all of it or none of it
                for operation in operations:
                    operation(conn)
            self.batches_written += 1
            self.thread_name_of_last_write = threading.current_thread().name
        except Exception as exc:  # noqa: BLE001 - history must never take the app down
            self.last_error = f"{type(exc).__name__}: {exc}"
            _LOG.warning("Dropping a history batch of %d operations: %s", len(operations), self.last_error)
            # operations like "set an Event" must still run so nobody waits forever
            for operation in operations:
                try:
                    operation(_NullConnection())
                except Exception:  # noqa: BLE001
                    pass

    def _prune(self, conn: sqlite3.Connection) -> None:
        try:
            retention.prune(conn, int(self._clock()))
        except sqlite3.Error as exc:
            _LOG.warning("History pruning failed: %s", exc)


class _NullConnection:
    """Absorbs SQL from operations that are replayed after a failed batch (so only their side effects run)."""

    def execute(self, *args, **kwargs):  # noqa: D401
        raise sqlite3.OperationalError("history batch was discarded")

    executemany = execute
