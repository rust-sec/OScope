"""Drive usage and read-only folder analysis.

Nothing in this module writes, moves, renames, deletes or changes permissions.
It only calls ``os.scandir`` / ``stat`` and reads the results.
"""

from __future__ import annotations

import heapq
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.utils.constants import MAX_LARGE_FILES_KEPT

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400  # Windows junctions / symlinks carry this flag


# --------------------------------------------------------------------------- #
# Drive usage
# --------------------------------------------------------------------------- #
@dataclass
class StorageInfo:
    path: str
    total: int
    used: int
    free: int
    percent: float


def system_drive_path() -> str:
    """``C:\\`` on Windows (from the SystemDrive variable), ``/`` elsewhere (dev mode)."""
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:").rstrip("\\/") + "\\"
    return os.path.abspath(os.sep)


def get_drive_usage(path: Optional[str] = None) -> Optional[StorageInfo]:
    """Total / used / free space of the drive that holds ``path`` (default: system drive)."""
    target = path or system_drive_path()
    try:
        usage = shutil.disk_usage(target)  # Windows: GetDiskFreeSpaceExW
    except OSError:
        return None
    if usage.total <= 0:
        return None
    used = usage.total - usage.free
    return StorageInfo(
        path=target,
        total=usage.total,
        used=used,
        free=usage.free,
        percent=used / usage.total * 100.0,
    )


# --------------------------------------------------------------------------- #
# Folder analysis
# --------------------------------------------------------------------------- #
@dataclass
class ScanResult:
    root: str
    total_size: int = 0
    file_count: int = 0
    dir_count: int = 0
    denied_count: int = 0        # items Windows refused to let us read
    skipped_count: int = 0       # items that vanished / failed for another reason
    largest_dirs: list[tuple[str, str, int]] = field(default_factory=list)   # (name, path, bytes)
    large_files: list[tuple[str, str, int]] = field(default_factory=list)    # (name, path, bytes)
    cancelled: bool = False
    elapsed_seconds: float = 0.0
    error: Optional[str] = None  # set only when the folder itself is invalid


ProgressCallback = Callable[[int, int], None]  # (files_seen, dirs_seen)


class DirectoryScanner:
    """Walk a folder tree once, collecting sizes.

    * Iterative (explicit stack) so very deep trees cannot hit Python's recursion limit.
    * Streams entries; never builds a list of every file. Only the N biggest files are kept.
    * Does not follow symlinks or junctions, so it cannot loop and never leaves the folder.
    * Permission errors are counted and skipped; the scan continues.
    """

    def __init__(
        self,
        root: str,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
        max_large_files: int = MAX_LARGE_FILES_KEPT,
    ) -> None:
        self.root = root
        self._progress = progress
        self._cancel = cancel_event or threading.Event()
        self._max_large = max_large_files
        self._files = 0
        self._dirs = 0
        self._denied = 0
        self._skipped = 0
        self._heap: list[tuple[int, str]] = []  # min-heap of (size, path): smallest of the big files on top
        self._last_tick = 0.0

    # -- public ---------------------------------------------------------------
    def scan(self) -> ScanResult:
        started = time.monotonic()
        result = ScanResult(root=self.root)

        if not self.root or not os.path.isdir(self.root):
            result.error = "The selected folder does not exist or is not a directory."
            return result

        dir_sizes: list[tuple[str, str, int]] = []
        root_files_size = 0
        subdirs: list[os.DirEntry] = []

        try:
            iterator = os.scandir(self.root)
        except PermissionError:
            self._denied += 1
            iterator = None
        except OSError:
            result.error = "The selected folder could not be opened."
            return result

        if iterator is not None:
            with iterator:
                try:
                    for entry in iterator:
                        if self._cancel.is_set():
                            break
                        try:
                            if self._is_real_dir(entry):
                                subdirs.append(entry)
                                self._dirs += 1
                            elif entry.is_file(follow_symlinks=False):
                                root_files_size += self._record_file(entry)
                        except PermissionError:
                            self._denied += 1
                        except OSError:
                            self._skipped += 1
                except OSError:
                    self._skipped += 1

        for entry in subdirs:
            if self._cancel.is_set():
                break
            dir_sizes.append((entry.name, entry.path, self._scan_tree(entry.path)))

        if root_files_size > 0:
            dir_sizes.append(("(files in this folder)", self.root, root_files_size))

        dir_sizes.sort(key=lambda item: item[2], reverse=True)
        result.largest_dirs = dir_sizes
        # dir_sizes already includes the "(files in this folder)" bucket
        result.total_size = sum(size for _, _, size in dir_sizes)
        result.file_count = self._files
        result.dir_count = self._dirs
        result.denied_count = self._denied
        result.skipped_count = self._skipped
        result.large_files = [
            (os.path.basename(path), path, size) for size, path in sorted(self._heap, reverse=True)
        ]
        result.cancelled = self._cancel.is_set()
        result.elapsed_seconds = time.monotonic() - started
        if self._progress:
            self._progress(self._files, self._dirs)
        return result

    # -- internals ------------------------------------------------------------
    def _scan_tree(self, top: str) -> int:
        """Total size in bytes of every file under ``top``."""
        total = 0
        stack = [top]
        while stack:
            if self._cancel.is_set():
                break
            directory = stack.pop()
            try:
                iterator = os.scandir(directory)
            except PermissionError:
                self._denied += 1
                continue
            except OSError:
                self._skipped += 1
                continue
            with iterator:
                try:
                    for entry in iterator:
                        try:
                            if self._is_real_dir(entry):
                                stack.append(entry.path)
                                self._dirs += 1
                            elif entry.is_file(follow_symlinks=False):
                                total += self._record_file(entry)
                        except PermissionError:
                            self._denied += 1
                        except OSError:
                            self._skipped += 1  # e.g. file deleted while scanning
                except OSError:
                    self._skipped += 1
        return total

    @staticmethod
    def _is_real_dir(entry: "os.DirEntry[str]") -> bool:
        """True for a normal directory; False for files, symlinks and Windows junctions."""
        if not entry.is_dir(follow_symlinks=False):
            return False
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return not (attributes & _FILE_ATTRIBUTE_REPARSE_POINT)

    def _record_file(self, entry: "os.DirEntry[str]") -> int:
        size = entry.stat(follow_symlinks=False).st_size
        self._files += 1
        if len(self._heap) < self._max_large:
            heapq.heappush(self._heap, (size, entry.path))
        elif size > self._heap[0][0]:
            heapq.heapreplace(self._heap, (size, entry.path))  # drop the smallest, add this one
        self._tick()
        return size

    def _tick(self) -> None:
        if self._progress is None:
            return
        now = time.monotonic()
        if now - self._last_tick >= 0.15:
            self._last_tick = now
            self._progress(self._files, self._dirs)
