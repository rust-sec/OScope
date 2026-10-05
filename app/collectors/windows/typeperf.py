"""Read Windows performance counters through ``typeperf.exe`` (ships with Windows).

Known limit, shown to the user rather than hidden: ``typeperf`` takes counter names in the
language of the installed Windows. On a non-English Windows the English names used here are
not found and the reading becomes UNAVAILABLE (never a guessed number).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Callable

from app.collectors.windows.background import ProbeError
from app.collectors.windows.cmd import RC_LAUNCH_FAILED, RC_TIMEOUT, Runner, run_command

_HOST_PREFIX = re.compile(r"^\\\\[^\\]+")  # "\\MYPC" in front of each counter path
TYPEPERF_TIMEOUT = 10.0


@dataclass(frozen=True)
class CounterSample:
    """Counter path (host prefix removed) -> value, plus typeperf's own message if it had one."""

    values: dict[str, float]
    message: str = ""


def parse_typeperf_csv(text: str) -> CounterSample:
    """Parse ``typeperf <counter> -sc 1`` output.

    Expected shape::

        "(PDH-CSV 4.0)","\\\\PC\\Memory\\Pages/sec"
        "10/05/2026 12:00:00.123","12.345"
        The command completed successfully.

    Anything that does not look like that yields no values and keeps the text as ``message``.
    """
    lines = [line for line in text.replace("﻿", "").splitlines() if line.strip()]
    header_index = next((i for i, line in enumerate(lines) if line.lstrip().startswith('"(PDH-CSV')), None)
    if header_index is None:
        return CounterSample({}, " ".join(lines)[:200])

    rows = list(csv.reader(io.StringIO("\n".join(lines[header_index : header_index + 2]))))
    if len(rows) < 2:
        return CounterSample({}, "typeperf returned no data row")
    header, data = rows[0], rows[1]
    values: dict[str, float] = {}
    for path, raw in zip(header[1:], data[1:]):  # column 0 is the timestamp
        try:
            values[_HOST_PREFIX.sub("", path)] = float(raw)
        except ValueError:
            continue  # blank / "-" cell: counter had no value this round
    return CounterSample(values)


def make_counter_reader(counter: str, runner: Runner = run_command) -> Callable[[], CounterSample]:
    """A function that reads ``counter`` once; raises ProbeError for tool-level failures."""

    def read() -> CounterSample:
        code, output = runner(["typeperf", counter, "-sc", "1"], TYPEPERF_TIMEOUT)
        if code == RC_LAUNCH_FAILED:
            raise ProbeError("typeperf could not be started")
        if code == RC_TIMEOUT:
            raise ProbeError("typeperf timed out")
        sample = parse_typeperf_csv(output)
        if not sample.values:
            detail = sample.message or "no counter values returned"
            raise ProbeError(f"counter not available ({detail}); counter names are language-specific")
        return sample

    return read
