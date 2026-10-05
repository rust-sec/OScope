"""Run a read-only helper program (typeperf, powershell) safely.

Rules: never through a shell, always with a timeout, no console window flash,
output decoded leniently. A failure is reported as a return code, never raised,
so a missing tool just becomes an UNAVAILABLE reading.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable, Sequence

RC_LAUNCH_FAILED = -1  # the program could not be started at all (not installed, blocked...)
RC_TIMEOUT = -2        # it ran longer than the timeout and was stopped

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# runner(args, timeout_seconds) -> (return_code, combined stdout+stderr text)
Runner = Callable[[Sequence[str], float], tuple[int, str]]


def run_command(args: Sequence[str], timeout: float = 5.0) -> tuple[int, str]:
    """Run ``args`` and return ``(return_code, output)``; output has stderr merged in."""
    try:
        completed = subprocess.run(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return RC_TIMEOUT, ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return RC_LAUNCH_FAILED, ""
    return completed.returncode, completed.stdout.decode("utf-8", errors="replace")
