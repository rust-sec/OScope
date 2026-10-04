"""Every Windows-specific operation in OScope lives in this one file.

Rules for this module:
  * Each function is read-only (it observes the OS, never changes it).
  * Each function returns ``None`` (or an empty result) if the information is not
    available, for example when not running on Windows or when access is denied.
  * Nothing here raises to the caller.

Windows interfaces used
-----------------------
  GlobalMemoryStatusEx  (kernel32)  physical memory total / available
  GetTickCount64        (kernel32)  milliseconds since boot -> uptime
  Registry (HKLM)                   Windows version and CPU name
  DwmSetWindowAttribute (dwmapi)    dark title bar (cosmetic)
  SetProcessDpiAwareness (shcore)   sharp text on high-DPI screens (cosmetic)
  tasklist.exe                      session, window title, hosted services of one PID
"""

from __future__ import annotations

import csv
import ctypes
import io
import os
import subprocess
import sys
from typing import Optional

IS_WINDOWS = sys.platform == "win32"
_CREATE_NO_WINDOW = 0x08000000  # do not flash a console window when running tasklist


# --------------------------------------------------------------------------- #
# Cosmetic helpers
# --------------------------------------------------------------------------- #
def enable_dpi_awareness() -> None:
    """Ask Windows not to bitmap-scale our window (avoids blurry text)."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:
            pass


def apply_dark_title_bar(window_id: int) -> None:
    """Switch the native title bar to dark mode (Windows 10 1809+ / Windows 11)."""
    if not IS_WINDOWS:
        return
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetParent(window_id) or window_id
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old id)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# System information
# --------------------------------------------------------------------------- #
def _read_registry(subkey: str, value_name: str) -> Optional[object]:
    if not IS_WINDOWS:
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
            return winreg.QueryValueEx(key, value_name)[0]
    except Exception:
        return None


def get_windows_version() -> Optional[tuple[str, str]]:
    """Return ``("Windows 11", "Pro, version 23H2, build 22631.3007")`` or ``None``.

    The registry ``ProductName`` value still says "Windows 10" on Windows 11, so
    the marketing name is derived from the build number (>= 22000 means Windows 11).
    """
    if not IS_WINDOWS:
        return None
    try:
        info = sys.getwindowsversion()  # type: ignore[attr-defined]
        if info.major >= 10:
            name = "Windows 11" if info.build >= 22000 else "Windows 10"
        else:
            name = str(_read_registry(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName") or "Windows")

        base = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        edition = _read_registry(base, "EditionID")
        display = _read_registry(base, "DisplayVersion") or _read_registry(base, "ReleaseId")
        ubr = _read_registry(base, "UBR")

        parts = []
        if edition:
            parts.append(str(edition))
        if display:
            parts.append(f"version {display}")
        parts.append(f"build {info.build}" + (f".{ubr}" if ubr is not None else ""))
        return name, ", ".join(parts)
    except Exception:
        return None


def get_cpu_name() -> Optional[str]:
    """CPU model string from the registry, e.g. ``"Intel(R) Core(TM) i5-1135G7"``."""
    value = _read_registry(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    return None


class _MemoryStatusEx(ctypes.Structure):
    """The MEMORYSTATUSEX structure from the Win32 API."""

    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def get_memory_status() -> Optional[tuple[int, int]]:
    """Return ``(total_physical_bytes, available_physical_bytes)`` via GlobalMemoryStatusEx."""
    if not IS_WINDOWS:
        return None
    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return None
        return int(status.ullTotalPhys), int(status.ullAvailPhys)
    except Exception:
        return None


def get_uptime_seconds() -> Optional[float]:
    """Seconds since boot via GetTickCount64."""
    if not IS_WINDOWS:
        return None
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return kernel32.GetTickCount64() / 1000.0
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Explorer (Storage Analyzer treemap: "Show in File Explorer")
# --------------------------------------------------------------------------- #
def reveal_in_explorer(path: str) -> bool:
    """Open Explorer with ``path`` selected. Read-only: never launches/executes the file.

    Uses Popen (fire-and-forget), not run: explorer.exe often returns a nonzero
    exit code even on success, so waiting on/checking its return code would be
    misleading.

    The command line is built as a single pre-quoted string, not an argv list:
    explorer's ``/select,`` switch needs the quote immediately after the comma
    (``/select,"C:\\a b\\file.txt"``). Passing ``["explorer", "/select," + path]``
    as a list lets Python's own argv-quoting wrap the *whole* token in quotes
    whenever the path has a space, landing the quote before ``/select`` instead
    - explorer then fails to recognize the switch and silently opens a default
    location instead of the requested file/folder.
    """
    if not IS_WINDOWS:
        return False
    try:
        command = f'explorer /select,"{os.path.normpath(path)}"'
        subprocess.Popen(command, creationflags=_CREATE_NO_WINDOW)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------------- #
# tasklist (one process at a time, on demand)
# --------------------------------------------------------------------------- #
def _run_tasklist(args: list[str]) -> list[list[str]]:
    """Run ``tasklist`` and return its CSV rows. Empty list on any problem."""
    if not IS_WINDOWS:
        return []
    try:
        completed = subprocess.run(
            ["tasklist", *args, "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=8,
            creationflags=_CREATE_NO_WINDOW,
        )
        if completed.returncode != 0:
            return []
        return [row for row in csv.reader(io.StringIO(completed.stdout)) if row]
    except (OSError, subprocess.SubprocessError):
        return []


def query_tasklist(pid: int) -> dict[str, str]:
    """Extra facts about one PID from ``tasklist`` (Windows only).

    Columns are read by **position**, not by header text, because the header
    names are translated on non-English Windows.

    ``tasklist /V`` column order: Image, PID, Session Name, Session#, Mem Usage,
    Status, User Name, CPU Time, Window Title.
    ``tasklist /SVC`` column order: Image, PID, Services.
    """
    result: dict[str, str] = {}

    verbose = _run_tasklist(["/V", "/FI", f"PID eq {pid}"])
    if verbose and len(verbose[0]) >= 9:
        row = verbose[0]
        if row[2].strip():
            result["Session"] = row[2].strip()
        title = row[8].strip()
        if title and title.upper() != "N/A":
            result["Window title"] = title

    services = _run_tasklist(["/SVC", "/FI", f"PID eq {pid}"])
    if services and len(services[0]) >= 3:
        value = services[0][2].strip()
        if value and value.upper() != "N/A":
            result["Hosted services"] = value

    return result
