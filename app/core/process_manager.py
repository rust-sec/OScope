"""Process list and per-process details (read-only: nothing here can end a process)."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Callable, Optional

import psutil

from app.core import windows_backend as win
from app.utils.constants import UNAVAILABLE
from app.utils.formatting import format_bytes, format_percent

ACCESS_DENIED = "Access denied"

_STATUS_LABELS = {
    psutil.STATUS_RUNNING: "Running",
    psutil.STATUS_SLEEPING: "Running",  # sleeping = waiting for work; still a live process
    psutil.STATUS_STOPPED: "Suspended",
    psutil.STATUS_ZOMBIE: "Zombie",
}


@dataclass
class ProcessInfo:
    """One row of the process table."""

    pid: int
    name: str
    cpu_percent: float
    memory_bytes: Optional[int]  # working set on Windows; None if the OS denied access
    status: str
    ppid: Optional[int]


class ProcessManager:
    """Reads the process list through psutil, which wraps the Windows process APIs.

    psutil reuses the same ``Process`` objects between calls (inside
    ``process_iter``), which is what makes per-process CPU % possible: CPU % is
    the change in a process's CPU time divided by the elapsed wall-clock time.
    """

    def __init__(self) -> None:
        self._logical_cpus = psutil.cpu_count(logical=True) or 1
        self.last_restricted = 0  # processes whose memory could not be read (access denied)
        self.list_processes()      # prime per-process CPU counters

    def list_processes(self) -> list[ProcessInfo]:
        """Return every process we are allowed to see (PID 0, the idle pseudo-process, is skipped)."""
        result: list[ProcessInfo] = []
        restricted = 0
        attrs = ["pid", "name", "ppid", "status", "memory_info", "cpu_percent"]
        for proc in psutil.process_iter(attrs):
            try:
                info = proc.info
                pid = info["pid"]
                if pid == 0:
                    continue  # "System Idle Process": its "CPU" is the idle time, not real work
                memory = info.get("memory_info")
                if memory is None:
                    restricted += 1
                cpu = info.get("cpu_percent") or 0.0
                result.append(
                    ProcessInfo(
                        pid=pid,
                        name=info.get("name") or "Unknown",
                        # psutil sums over all cores (max = 100 x cores); Task Manager
                        # shows the share of the whole machine, so divide by core count.
                        cpu_percent=min(cpu / self._logical_cpus, 100.0),
                        memory_bytes=int(memory.rss) if memory is not None else None,
                        status=_STATUS_LABELS.get(info.get("status") or "", (info.get("status") or "Unknown").title()),
                        ppid=info.get("ppid"),
                    )
                )
            except (psutil.NoSuchProcess, psutil.ZombieProcess, KeyError):
                continue  # process ended while we were reading it
            except psutil.AccessDenied:
                restricted += 1
        self.last_restricted = restricted
        return result

    @staticmethod
    def get_details(pid: int) -> list[tuple[str, str]]:
        """Return ``[(label, value), ...]`` for one process.

        Slow parts (user name lookup, ``tasklist``) live here, so call this from
        a background thread. Anything the OS refuses shows as "Access denied".
        """
        try:
            proc = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return [("Status", "This process is no longer running.")]

        fields: list[tuple[str, str]] = []

        def query(getter: Callable[[], Any]) -> Any:
            try:
                return getter()
            except psutil.AccessDenied:
                return ACCESS_DENIED
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                return None
            except (OSError, ValueError, AttributeError, NotImplementedError):
                return UNAVAILABLE

        with proc.oneshot():
            parent_id = query(proc.ppid)
            if isinstance(parent_id, int):
                parent_name = query(lambda: psutil.Process(parent_id).name())
                if isinstance(parent_name, str) and parent_name not in (ACCESS_DENIED, UNAVAILABLE):
                    fields.append(("Parent", f"{parent_name} (PID {parent_id})"))

            exe = query(proc.exe)
            fields.append(("Executable", exe if exe else UNAVAILABLE))

            user = query(proc.username)
            fields.append(("User", user if user else UNAVAILABLE))

            threads = query(proc.num_threads)
            fields.append(("Threads", str(threads) if threads is not None else UNAVAILABLE))

            created = query(proc.create_time)
            if isinstance(created, float):
                fields.append(("Started", _dt.datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S")))
            else:
                fields.append(("Started", created or UNAVAILABLE))

            memory = query(proc.memory_info)
            private = getattr(memory, "private", None)
            if private is not None:  # Windows: private bytes committed to this process
                fields.append(("Private memory", format_bytes(private)))

        # Windows-only extras from tasklist (session, window title, hosted services)
        for label, value in win.query_tasklist(pid).items():
            fields.append((label, value))

        return fields

    @staticmethod
    def basic_fields(proc: ProcessInfo) -> list[tuple[str, str]]:
        """The always-available fields shown instantly when a row is selected."""
        return [
            ("Name", proc.name),
            ("PID", str(proc.pid)),
            ("Parent PID", str(proc.ppid) if proc.ppid is not None else UNAVAILABLE),
            ("Memory", format_bytes(proc.memory_bytes) if proc.memory_bytes is not None else ACCESS_DENIED),
            ("CPU", format_percent(proc.cpu_percent)),
            ("Status", proc.status),
        ]
