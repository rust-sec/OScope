"""The one place that knows how this operating system differs from the next.

Everything outside this module and ``windows_backend`` asks questions like
"is this folder a junction?" or "where may I keep my data?" through here, so
OS-specific checks (``os.name``, Windows file attributes, ``startfile``, the
``SystemDrive`` variable) never leak into scanners, views or collectors.

Dev mode (non-Windows with ``OSCOPE_DEV=1``) gets harmless fallbacks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from app.core import windows_backend

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400  # Windows junctions / symlinks / cloud placeholders carry this flag
DATA_DIR_ENV_VAR = "OSCOPE_DATA_DIR"   # overrides where OScope keeps settings, logs, reports and history


def system_drive_path() -> str:
    """``C:\\`` on Windows (from the SystemDrive variable), ``/`` elsewhere (dev mode)."""
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:").rstrip("\\/") + "\\"
    return os.path.abspath(os.sep)


def is_reparse_point(entry: "os.DirEntry[str]") -> bool:
    """True if ``entry`` is a Windows reparse point (junction, symlink, OneDrive placeholder...).

    Always False where the OS has no such attribute.
    """
    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def open_path(path: str) -> bool:
    """Open a folder/file with its default handler (Explorer for a folder). Windows only."""
    opener = getattr(os, "startfile", None)
    if opener is None:
        return False
    try:
        opener(path)
        return True
    except OSError:
        return False


def is_elevated() -> Optional[bool]:
    """True/False for 'running as administrator'; None when unknown or not applicable."""
    return windows_backend.is_user_admin()


# --------------------------------------------------------------------------- #
# Application data directory
# --------------------------------------------------------------------------- #
def data_dir_candidates(is_nt: bool, env: Mapping[str, str], home: Path) -> list[Path]:
    """Preferred-first list of folders for OScope's own data. Pure, so it is testable anywhere."""
    candidates: list[Path] = []
    override = env.get(DATA_DIR_ENV_VAR)
    if override:
        candidates.append(Path(override))
    elif is_nt and env.get("LOCALAPPDATA"):
        candidates.append(Path(env["LOCALAPPDATA"]) / "OScope")
    candidates.append(home / ".oscope")
    return candidates


def app_data_dir() -> Optional[Path]:
    """A writable per-user folder for settings, logs, reports and history; None if none works.

    ``%LOCALAPPDATA%\\OScope`` on Windows, ``~/.oscope`` as the fallback. Local only.
    """
    for candidate in data_dir_candidates(os.name == "nt", os.environ, Path.home()):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if os.access(candidate, os.W_OK):
                return candidate
        except OSError:
            continue
    return None
