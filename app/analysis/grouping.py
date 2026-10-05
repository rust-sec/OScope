"""Group the process list by program, so "Chrome" is one line instead of thirty.

Honest limits (also shown in the UI):
* Grouping is by program file name (e.g. ``chrome.exe``). Two unrelated programs that share a
  file name would be merged; programs are not told apart by folder.
* A group's memory is the SUM of its processes' working sets. Processes share some pages
  (libraries, browser code), so the sum can exceed what the group truly occupies. It is a
  "working set total", not "RAM used".
* Processes whose memory Windows would not let us read are counted in ``restricted_pids``
  and are not part of the memory total.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.core.process_manager import ProcessInfo

# Friendly names for common programs. Anything not listed keeps its file name; nothing is guessed.
DISPLAY_ALIASES = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Mozilla Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "code.exe": "Visual Studio Code",
    "teams.exe": "Microsoft Teams",
    "ms-teams.exe": "Microsoft Teams",
    "discord.exe": "Discord",
    "spotify.exe": "Spotify",
    "slack.exe": "Slack",
    "onedrive.exe": "OneDrive",
    "dropbox.exe": "Dropbox",
    "photoshop.exe": "Adobe Photoshop",
    "illustrator.exe": "Adobe Illustrator",
    "premiere pro.exe": "Adobe Premiere Pro",
    "adobe premiere pro.exe": "Adobe Premiere Pro",
    "googledrivefs.exe": "Google Drive",
    "afterfx.exe": "Adobe After Effects",
    "resolve.exe": "DaVinci Resolve",
    "obs64.exe": "OBS Studio",
    "explorer.exe": "Windows Explorer",
}

BROWSER_KEYS = frozenset({"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"})

# Windows plumbing, not "programs the user opened". Kept in the table but excluded from app narratives.
SYSTEM_KEYS = frozenset(
    {
        "system",
        "registry",
        "memory compression",
        "secure system",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "services.exe",
        "lsass.exe",
        "svchost.exe",
        "fontdrvhost.exe",
        "winlogon.exe",
        "dwm.exe",
        "sihost.exe",
        "ctfmon.exe",
        "runtimebroker.exe",
        "conhost.exe",
        "dllhost.exe",
        "taskhostw.exe",
        "searchindexer.exe",
        "wmiprvse.exe",
        "audiodg.exe",
        "spoolsv.exe",
    }
)


@dataclass
class ProcessGroup:
    key: str                  # lower-cased program file name, e.g. "chrome.exe"
    display: str              # friendly name if known, else the file name as Windows reports it
    pids: list[int] = field(default_factory=list)
    cpu_percent: float = 0.0
    memory_bytes: int = 0     # working-set total of the processes we could read
    restricted_pids: int = 0  # members whose memory could not be read
    is_system: bool = False

    @property
    def count(self) -> int:
        return len(self.pids)


def group_processes(processes: Iterable[ProcessInfo]) -> list[ProcessGroup]:
    """One group per program file name, biggest working-set total first."""
    groups: dict[str, ProcessGroup] = {}
    for proc in processes:
        key = proc.name.lower()
        group = groups.get(key)
        if group is None:
            group = groups[key] = ProcessGroup(
                key=key,
                display=DISPLAY_ALIASES.get(key, proc.name),
                is_system=key in SYSTEM_KEYS,
            )
        group.pids.append(proc.pid)
        group.cpu_percent += proc.cpu_percent
        if proc.memory_bytes is None:
            group.restricted_pids += 1
        else:
            group.memory_bytes += proc.memory_bytes
    return sorted(groups.values(), key=lambda g: (g.memory_bytes, g.cpu_percent), reverse=True)
