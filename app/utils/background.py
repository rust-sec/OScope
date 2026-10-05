"""Thread-safe hand-off from worker threads to the Tk main thread.

Tkinter widgets must only be touched from the main thread. Worker threads
therefore never call widgets; they put a ``(callback, args)`` pair on a queue,
and the main thread drains that queue every 100 ms using ``root.after``.
This is what keeps the GUI responsive while data is being collected.
"""

from __future__ import annotations

import queue
import sys
import threading
import traceback
from typing import Any, Callable, Optional


class BackgroundRunner:
    """Run work on threads and deliver the results on the Tk main thread."""

    def __init__(self, root: Any, poll_ms: int = 100) -> None:
        self._root = root
        self._poll_ms = poll_ms
        self._queue: "queue.Queue[tuple[Callable[..., None], tuple]]" = queue.Queue()
        self._closed = False
        self._after_id: Optional[str] = self._root.after(self._poll_ms, self._poll)

    def post(self, callback: Callable[..., None], *args: Any) -> None:
        """Queue ``callback(*args)`` to run on the main thread (safe from any thread)."""
        self._queue.put((callback, args))

    def submit(
        self,
        work: Callable[[], Any],
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """Run ``work()`` on a new daemon thread; deliver its result on the main thread."""

        def target() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - reported through on_error
                if on_error is not None:
                    self.post(on_error, exc)
                return
            if on_done is not None:
                self.post(on_done, result)

        threading.Thread(target=target, daemon=True).start()

    def close(self) -> None:
        """Stop polling (call when the window is closing). Also cancels the timer that is already scheduled."""
        self._closed = True
        if self._after_id is not None:
            try:
                self._root.after_cancel(self._after_id)
            except Exception:  # noqa: BLE001 - the window may already be gone
                pass
            self._after_id = None

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            while True:
                callback, args = self._queue.get_nowait()
                try:
                    callback(*args)
                except Exception:  # noqa: BLE001 - one bad callback must not stop the loop
                    traceback.print_exc(file=sys.stderr)
        except queue.Empty:
            pass
        try:
            self._after_id = self._root.after(self._poll_ms, self._poll)
        except Exception:  # window already destroyed
            self._closed = True
            self._after_id = None
