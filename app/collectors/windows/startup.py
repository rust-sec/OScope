"""Programs configured to start with Windows (registry Run keys and Startup folders), read-only.

Honest scope: this is NOT everything that starts at boot. Services, scheduled tasks and
drivers also start things; they are not listed here and the UI says so.

Enabled/disabled state comes from the ``StartupApproved`` keys that Task Manager's
Startup tab writes: the first byte is even (02, 06) when enabled and odd (03, 07) when
disabled. An entry with no record there is enabled. Anything we cannot interpret is
reported as ``enabled=None`` (unknown), never assumed.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from app.collectors.base import Availability, CollectorContext, Reading
from app.collectors.windows.winreg_reader import RegistryReader

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_HKLM = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_RUN_HKLM_32 = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

# (hive, run key, StartupApproved sub-key, label shown to the user)
_REGISTRY_SOURCES = [
    ("HKCU", _RUN, _APPROVED + r"\Run", "Registry (this user)"),
    ("HKLM", _RUN_HKLM, _APPROVED + r"\Run", "Registry (all users)"),
    ("HKLM", _RUN_HKLM_32, _APPROVED + r"\Run32", "Registry (all users, 32-bit)"),
]
_STARTUP_FOLDER_TAIL = r"Microsoft\Windows\Start Menu\Programs\Startup"
CACHE_SECONDS = 60.0
_SOURCE = "winreg:Run + Startup folders"


@dataclass(frozen=True)
class StartupEntry:
    name: str
    command: str
    location: str
    enabled: Optional[bool]  # None = state could not be determined


def approved_state(data: object) -> Optional[bool]:
    """Decode a StartupApproved value: True = enabled, False = disabled, None = unreadable."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return None
    return data[0] % 2 == 0


def _folder_entries(
    folder: Optional[str], approved: Mapping[str, object], location: str, listdir: Callable[[str], list[str]]
) -> list[StartupEntry]:
    if not folder:
        return []
    try:
        names = listdir(folder)
    except OSError:
        return []
    entries = []
    for name in sorted(names, key=str.lower):
        if name.lower() == "desktop.ini":
            continue
        state = approved_state(approved[name]) if name in approved else True
        entries.append(StartupEntry(name, os.path.join(folder, name), location, state))
    return entries


class StartupCollector:
    """``startup.entries`` (list of StartupEntry) and ``startup.enabled_count`` (entries known to be enabled)."""

    key = "startup"

    def __init__(
        self,
        registry: Optional[RegistryReader] = None,
        env: Optional[Mapping[str, str]] = None,
        listdir: Callable[[str], list[str]] = os.listdir,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry or RegistryReader()
        self._env = os.environ if env is None else env
        self._listdir = listdir
        self._clock = clock
        self._cached: Optional[dict[str, Reading]] = None
        self._cached_at = 0.0

    def collect(self, ctx: CollectorContext) -> dict[str, Reading]:
        now = self._clock()
        if self._cached is not None and now - self._cached_at < CACHE_SECONDS:
            return self._cached
        self._cached = self._read()
        self._cached_at = now
        return self._cached

    def _read(self) -> dict[str, Reading]:
        if not self._registry.available:
            return {
                name: Reading.missing(name, Availability.NOT_SUPPORTED, "the Windows registry is not available", _SOURCE)
                for name in ("startup.entries", "startup.enabled_count")
            }
        entries: list[StartupEntry] = []
        readable_sources = 0
        for hive, run_key, approved_key, label in _REGISTRY_SOURCES:
            values = self._registry.values(hive, run_key)
            if values is None:
                continue
            readable_sources += 1
            approved = self._registry.values(hive, approved_key) or {}
            for name, command in values.items():
                state = approved_state(approved[name]) if name in approved else True
                entries.append(StartupEntry(name, str(command), label, state))

        user_folder = self._startup_folder("APPDATA")
        common_folder = self._startup_folder("ProgramData")
        entries += _folder_entries(
            user_folder,
            self._registry.values("HKCU", _APPROVED + r"\StartupFolder") or {},
            "Startup folder (this user)",
            self._listdir,
        )
        entries += _folder_entries(
            common_folder,
            self._registry.values("HKLM", _APPROVED + r"\StartupFolder") or {},
            "Startup folder (all users)",
            self._listdir,
        )

        if readable_sources == 0 and not entries:
            # No Run key could be read at all: do not claim "zero startup apps".
            return {
                name: Reading.missing(name, Availability.UNAVAILABLE, "startup registry keys could not be read", _SOURCE)
                for name in ("startup.entries", "startup.enabled_count")
            }
        enabled = sum(1 for entry in entries if entry.enabled is True)
        unknown = sum(1 for entry in entries if entry.enabled is None)
        detail = "registry Run keys and Startup folders only; services and scheduled tasks are not included"
        if unknown:
            detail += f"; {unknown} entr{'y' if unknown == 1 else 'ies'} with unknown state"
        return {
            "startup.entries": Reading.available("startup.entries", tuple(entries), source=_SOURCE, detail=detail),
            "startup.enabled_count": Reading.available("startup.enabled_count", enabled, source=_SOURCE, detail=detail),
        }

    def _startup_folder(self, variable: str) -> Optional[str]:
        base = self._env.get(variable)
        return os.path.join(base, _STARTUP_FOLDER_TAIL) if base else None
