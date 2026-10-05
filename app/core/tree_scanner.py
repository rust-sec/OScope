"""Full-tree variant of the folder scanner, for the Storage view's treemap and folder tree.

Extends DirectoryScanner (never modifies it) so cancellation, permission handling, progress
throttling and the large-file heap behave identically. It additionally retains a nested tree of
every folder and (most) files, which a treemap needs for exact sizes at every zoom level.

What this scanner promises, and what it reports instead of hiding:

* A folder's size is the exact sum of everything in it, and **a folder's children always add up
  to its size.** To keep memory bounded, the smallest files of a very large folder are folded into
  one synthetic "(N smaller files)" item instead of being dropped.
* Items the OS would not let us read are recorded by path (``denied_paths``); an unreadable
  folder is marked ``denied`` (its size is unknown, not zero).
* Links (symlinks, junctions) are never followed, so nothing is counted twice or looped on, but
  they are counted and listed (``linked_skipped``) rather than silently skipped.
* Cloud-only placeholder files (e.g. OneDrive Files On-Demand) occupy no space on this disk, so
  they are counted separately (``cloud_files`` / ``cloud_bytes``) and not added to sizes.
* Sizes are logical file sizes (``st_size``). Hard links are not de-duplicated, and disk usage can
  differ because of cluster rounding, compression or sparse files.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from app.core.platform_ops import FileFlags, file_flags, is_reparse_point
from app.core.storage_manager import DirectoryScanner, ProgressCallback, ScanResult
from app.utils.constants import MAX_FILES_PER_FOLDER, MAX_LARGE_FILES_KEPT, MAX_TREE_NODES
from app.utils.file_categories import FOLDER_CATEGORY, category_for

_SENTINEL = object()
MAX_DENIED_PATHS = 500
MAX_LINK_PATHS = 200


@dataclass(slots=True)
class TreeNode:
    """One file or folder in the retained tree. Folders carry FOLDER_CATEGORY."""

    name: str
    path: str
    size: int
    is_dir: bool
    category: str
    children: list["TreeNode"] = field(default_factory=list)
    mtime: Optional[float] = None     # last modified (seconds since the epoch), if known
    hidden: bool = False
    system: bool = False
    file_count: int = 0               # folders: files inside, all levels
    dir_count: int = 0                # folders: folders inside, all levels
    denied: bool = False              # folder the OS would not let us open: size unknown, not zero
    synthetic: bool = False           # stands for several small files that were folded together


@dataclass(frozen=True)
class ScanStatus:
    """Live progress, throttled: what has been seen so far and where the scan is now."""

    files: int
    dirs: int
    bytes: int
    path: str


StatusCallback = Callable[[ScanStatus], None]


@dataclass
class TreeScanResult(ScanResult):
    """Everything ScanResult has, plus the full tree and what the scan could not see."""

    tree: Optional[TreeNode] = None
    tree_truncated: bool = False                      # global node cap reached (some folders folded more files)
    folded_files: int = 0                             # files represented by "(N smaller files)" items
    denied_paths: list[str] = field(default_factory=list)   # capped at MAX_DENIED_PATHS
    denied_overflow: int = 0                          # denied items beyond that cap
    linked_skipped: int = 0                           # links not followed
    linked_paths: list[tuple[str, str]] = field(default_factory=list)  # (path, "symlink" | "junction or link")
    cloud_files: int = 0                              # cloud-only placeholders (not on this disk)
    cloud_bytes: int = 0
    hidden_files: int = 0
    hidden_bytes: int = 0
    category_totals: dict[str, tuple[int, int]] = field(default_factory=dict)  # category -> (bytes, files)
    elevated: Optional[bool] = None                   # filled in by the caller: was the scan run as administrator?


@dataclass
class _Frame:
    node: TreeNode
    iterator: Optional[Iterator["os.DirEntry[str]"]]
    files: list[TreeNode] = field(default_factory=list)   # retained file leaves, folded when too many
    folded_size: int = 0
    folded_count: int = 0
    hidden_context: bool = False                            # this folder is hidden, or inside a hidden folder


class TreeScanner(DirectoryScanner):
    """Like DirectoryScanner, but also builds a full nested TreeNode tree.

    Inherits ``_record_file`` and every counter from DirectoryScanner unchanged. Platform questions
    (is this a link? is it hidden?) go through injectable functions so the logic is testable anywhere.
    """

    def __init__(
        self,
        root: str,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
        max_large_files: int = MAX_LARGE_FILES_KEPT,
        max_tree_nodes: int = MAX_TREE_NODES,
        status: Optional[StatusCallback] = None,
        max_files_per_folder: int = MAX_FILES_PER_FOLDER,
        scandir_fn: Optional[Callable[[str], Iterator["os.DirEntry[str]"]]] = None,
        flags_fn: Callable[["os.DirEntry[str]"], FileFlags] = file_flags,
        is_reparse_fn: Callable[["os.DirEntry[str]"], bool] = is_reparse_point,
    ) -> None:
        super().__init__(root, progress, cancel_event, max_large_files)
        self._status = status
        self._max_tree_nodes = max_tree_nodes
        self._max_files_per_folder = max(int(max_files_per_folder), 1)
        self._scandir_fn = scandir_fn
        self._flags = flags_fn
        self._is_reparse = is_reparse_fn
        self._tree_nodes_built = 0
        self._truncated = False
        self._folded_files = 0
        self._bytes = 0
        self._current_path = root
        self._denied_paths: list[str] = []
        self._denied_overflow = 0
        self._linked: list[tuple[str, str]] = []
        self._linked_count = 0
        self._cloud_files = 0
        self._cloud_bytes = 0
        self._hidden_files = 0
        self._hidden_bytes = 0
        self._category_bytes: dict[str, int] = {}
        self._category_files: dict[str, int] = {}

    # -- public ---------------------------------------------------------------
    def scan(self) -> TreeScanResult:
        """Same aggregates as DirectoryScanner.scan(), plus a full tree and the details of what was not seen."""
        started = time.monotonic()
        result = TreeScanResult(root=self.root)

        if not self.root or not os.path.isdir(self.root):
            result.error = "The selected folder does not exist or is not a directory."
            return result

        root_name = os.path.basename(os.path.normpath(self.root)) or self.root
        root_node = TreeNode(name=root_name, path=self.root, size=0, is_dir=True, category=FOLDER_CATEGORY)
        try:
            iterator: Optional[Iterator["os.DirEntry[str]"]] = self._scandir(self.root)
        except PermissionError:
            self._deny(self.root)
            root_node.denied = True
            iterator = None
        except OSError:
            result.error = "The selected folder could not be opened."
            return result

        self._walk(root_node, iterator)

        dir_sizes = [(child.name, child.path, child.size) for child in root_node.children if child.is_dir]
        root_files_size = root_node.size - sum(size for _, _, size in dir_sizes)
        if root_files_size > 0:
            dir_sizes.append(("(files in this folder)", self.root, root_files_size))
        dir_sizes.sort(key=lambda item: item[2], reverse=True)

        result.largest_dirs = dir_sizes
        result.total_size = root_node.size
        result.file_count = self._files
        result.dir_count = self._dirs
        result.denied_count = self._denied
        result.skipped_count = self._skipped
        result.large_files = [
            (os.path.basename(path), path, size) for size, path in sorted(self._heap, reverse=True)
        ]
        result.cancelled = self._cancel.is_set()
        result.elapsed_seconds = time.monotonic() - started
        result.tree = root_node
        result.tree_truncated = self._truncated
        result.folded_files = self._folded_files
        result.denied_paths = list(self._denied_paths)
        result.denied_overflow = self._denied_overflow
        result.linked_skipped = self._linked_count
        result.linked_paths = list(self._linked)
        result.cloud_files = self._cloud_files
        result.cloud_bytes = self._cloud_bytes
        result.hidden_files = self._hidden_files
        result.hidden_bytes = self._hidden_bytes
        result.category_totals = {
            category: (size, self._category_files[category]) for category, size in self._category_bytes.items()
        }
        self._emit(force=True)
        return result

    # -- platform / bookkeeping helpers ------------------------------------------
    def _scandir(self, path: str) -> Iterator["os.DirEntry[str]"]:
        return (self._scandir_fn or os.scandir)(path)

    def _deny(self, path: str) -> None:
        self._denied += 1
        if len(self._denied_paths) < MAX_DENIED_PATHS:
            self._denied_paths.append(path)
        else:
            self._denied_overflow += 1

    def _open_dir(self, path: str) -> tuple[Optional[Iterator["os.DirEntry[str]"]], bool]:
        """``(iterator, denied)``: iterator is None if the folder could not be opened."""
        try:
            return self._scandir(path), False
        except PermissionError:
            self._deny(path)
            return None, True
        except OSError:
            self._skipped += 1
            return None, False

    def _is_real_dir(self, entry: "os.DirEntry[str]") -> bool:  # type: ignore[override]
        """A normal folder. Links (symlinks, junctions) are not followed, but they are counted and remembered."""
        if entry.is_symlink():
            self._note_link(entry, "symlink")
            return False
        if not entry.is_dir(follow_symlinks=False):
            return False
        if self._is_reparse(entry):
            self._note_link(entry, "junction or link")
            return False
        return True

    def _note_link(self, entry: "os.DirEntry[str]", kind: str) -> None:
        self._linked_count += 1
        if len(self._linked) < MAX_LINK_PATHS:
            self._linked.append((entry.path, kind))

    def _tick(self) -> None:
        if self._progress is None and self._status is None:
            return
        now = time.monotonic()
        if now - self._last_tick >= 0.15:
            self._last_tick = now
            self._emit()

    def _emit(self, force: bool = False) -> None:
        if self._progress is not None:
            self._progress(self._files, self._dirs)
        if self._status is not None:
            self._status(ScanStatus(self._files, self._dirs, self._bytes, self._current_path))

    # -- the walk -------------------------------------------------------------------
    def _walk(self, root_node: TreeNode, root_iterator: Optional[Iterator["os.DirEntry[str]"]]) -> None:
        """Iterative (never recursive) full-tree build of everything under ``root_node``."""
        frames: list[_Frame] = [_Frame(node=root_node, iterator=root_iterator)]
        self._current_path = root_node.path

        while frames:
            if self._cancel.is_set():
                break
            frame = frames[-1]
            if frame.iterator is None:
                self._finish_frame(frames)
                continue
            try:
                entry = next(frame.iterator, _SENTINEL)
            except OSError:  # the folder became unreadable while we were listing it
                self._skipped += 1
                entry = _SENTINEL
            if entry is _SENTINEL:
                frame.iterator.close()  # type: ignore[union-attr]
                self._finish_frame(frames)
                continue
            try:
                if self._is_real_dir(entry):
                    self._dirs += 1
                    flags = self._flags(entry)
                    child = TreeNode(
                        name=entry.name, path=entry.path, size=0, is_dir=True, category=FOLDER_CATEGORY,
                        mtime=entry.stat(follow_symlinks=False).st_mtime, hidden=flags.hidden, system=flags.system,
                    )
                    iterator, denied = self._open_dir(entry.path)
                    child.denied = denied
                    self._current_path = entry.path
                    frames.append(
                        _Frame(node=child, iterator=iterator, hidden_context=frame.hidden_context or child.hidden)
                    )
                elif entry.is_file(follow_symlinks=False):
                    self._handle_file(frame, entry)
            except PermissionError:
                self._deny(entry.path)
            except OSError:
                self._skipped += 1

        # Cancelled mid-scan: close any still-open iterators and fold every open frame into its parent
        # so the returned tree stays structurally consistent even when partial. A no-op otherwise.
        while len(frames) > 1:
            top_frame = frames[-1]
            if top_frame.iterator is not None:
                top_frame.iterator.close()  # type: ignore[union-attr]
            self._finish_frame(frames)
        if frames:
            if frames[0].iterator is not None:
                frames[0].iterator.close()  # type: ignore[union-attr]
            self._finish_frame(frames)

    def _handle_file(self, frame: _Frame, entry: "os.DirEntry[str]") -> None:
        flags = self._flags(entry)
        stat = entry.stat(follow_symlinks=False)
        if flags.cloud_placeholder:
            # Present as a name only: the content is in the cloud, so it takes no space on this disk.
            self._cloud_files += 1
            self._cloud_bytes += stat.st_size
            self._tick()
            return
        size = self._record_file(entry)
        self._bytes += size
        category = category_for(entry.path, False)
        self._category_bytes[category] = self._category_bytes.get(category, 0) + size
        self._category_files[category] = self._category_files.get(category, 0) + 1
        if flags.hidden or frame.hidden_context:  # files inside a hidden folder are out of sight too
            self._hidden_files += 1
            self._hidden_bytes += size
        frame.node.size += size
        frame.node.file_count += 1
        self._add_leaf(frame, entry, size, stat.st_mtime, flags, category)

    def _add_leaf(
        self, frame: _Frame, entry: "os.DirEntry[str]", size: int, mtime: float, flags: FileFlags, category: str
    ) -> None:
        """Keep a file as a node, or fold it into the folder's "smaller files" item when over the limits."""
        if self._tree_nodes_built >= self._max_tree_nodes:
            self._truncated = True
            frame.folded_size += size
            frame.folded_count += 1
            return
        self._tree_nodes_built += 1
        frame.files.append(
            TreeNode(
                name=entry.name, path=entry.path, size=size, is_dir=False, category=category,
                mtime=mtime, hidden=flags.hidden, system=flags.system,
            )
        )
        if len(frame.files) >= 2 * self._max_files_per_folder:
            self._compact(frame)

    def _compact(self, frame: _Frame) -> None:
        """Keep only the largest files of this folder; fold the rest into one item (amortised, bounded memory)."""
        frame.files.sort(key=lambda node: node.size, reverse=True)
        removed = frame.files[self._max_files_per_folder:]
        del frame.files[self._max_files_per_folder:]
        frame.folded_size += sum(node.size for node in removed)
        frame.folded_count += len(removed)
        self._tree_nodes_built -= len(removed)  # the cap counts nodes that still exist

    def _finish_frame(self, frames: list[_Frame]) -> None:
        frame = frames.pop()
        node = frame.node
        if len(frame.files) > self._max_files_per_folder:
            self._compact(frame)  # the last partial batch is trimmed too, so no folder keeps more than the limit
        node.children.extend(frame.files)
        if frame.folded_count:
            self._folded_files += frame.folded_count
            node.children.append(
                TreeNode(
                    name=f"({frame.folded_count:,} smaller files)", path="", size=frame.folded_size, is_dir=False,
                    category="Other", file_count=frame.folded_count, synthetic=True,
                )
            )
        node.children.sort(key=lambda child: child.size, reverse=True)
        if frames:
            parent = frames[-1].node
            parent.children.append(node)
            parent.size += node.size
            parent.file_count += node.file_count
            parent.dir_count += node.dir_count + 1
