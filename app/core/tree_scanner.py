"""Full-tree variant of the folder scanner, for the Storage Analyzer's treemap.

Extends DirectoryScanner (never modifies it) so cancellation, permission
handling, progress throttling and the large-file heap behave identically.
The one deliberate departure from DirectoryScanner's bounded-memory design:
this scanner also retains a full nested tree of every file/folder, needed
because a treemap needs exact sizes at every zoom level. See
PROJECT_DOCUMENTATION.md "Future Improvements" ("graphical disk map"). A soft
cap (MAX_TREE_NODES) keeps memory bounded on huge trees; aggregate totals
(total_size, file_count, largest_dirs, large_files, ...) stay correct even
when the retained tree itself is truncated.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional

from app.core.storage_manager import DirectoryScanner, ProgressCallback, ScanResult
from app.utils.constants import MAX_LARGE_FILES_KEPT, MAX_TREE_NODES
from app.utils.file_categories import FOLDER_CATEGORY, category_for

_SENTINEL = object()


@dataclass(slots=True)
class TreeNode:
    """One file or folder in the retained tree. Folders carry FOLDER_CATEGORY."""

    name: str
    path: str
    size: int
    is_dir: bool
    category: str
    children: list["TreeNode"] = field(default_factory=list)


@dataclass
class TreeScanResult(ScanResult):
    """Everything ScanResult has, plus the full tree needed for the treemap."""

    tree: Optional[TreeNode] = None
    tree_truncated: bool = False


@dataclass
class _Frame:
    node: TreeNode
    iterator: Optional[Iterator["os.DirEntry[str]"]]


class TreeScanner(DirectoryScanner):
    """Like DirectoryScanner, but also builds a full nested TreeNode tree.

    Inherits _record_file/_is_real_dir/_tick and every counter from
    DirectoryScanner unchanged (tests that patch DirectoryScanner._record_file
    keep working against this subclass too, since it is never overridden here).
    """

    def __init__(
        self,
        root: str,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
        max_large_files: int = MAX_LARGE_FILES_KEPT,
        max_tree_nodes: int = MAX_TREE_NODES,
    ) -> None:
        super().__init__(root, progress, cancel_event, max_large_files)
        self._max_tree_nodes = max_tree_nodes
        self._tree_nodes_built = 0
        self._truncated = False

    # -- public ---------------------------------------------------------------
    def scan(self) -> TreeScanResult:
        """Same aggregate computation as DirectoryScanner.scan(), plus a full tree."""
        started = time.monotonic()
        result = TreeScanResult(root=self.root)

        if not self.root or not os.path.isdir(self.root):
            result.error = "The selected folder does not exist or is not a directory."
            return result

        dir_sizes: list[tuple[str, str, int]] = []
        root_files_size = 0
        root_file_nodes: list[TreeNode] = []
        subdirs: list["os.DirEntry[str]"] = []

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
                                size = self._record_file(entry)
                                root_files_size += size
                                leaf = self._leaf_node(entry.name, entry.path, size)
                                if leaf is not None:
                                    root_file_nodes.append(leaf)
                        except PermissionError:
                            self._denied += 1
                        except OSError:
                            self._skipped += 1
                except OSError:
                    self._skipped += 1

        subdir_nodes: list[TreeNode] = []
        for entry in subdirs:
            if self._cancel.is_set():
                break
            child = self._scan_tree_node(entry.path, entry.name)
            dir_sizes.append((entry.name, entry.path, child.size))
            subdir_nodes.append(child)

        if root_files_size > 0:
            dir_sizes.append(("(files in this folder)", self.root, root_files_size))

        dir_sizes.sort(key=lambda item: item[2], reverse=True)
        result.largest_dirs = dir_sizes
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

        children = root_file_nodes + subdir_nodes
        children.sort(key=lambda n: n.size, reverse=True)
        root_name = os.path.basename(os.path.normpath(self.root)) or self.root
        result.tree = TreeNode(
            name=root_name, path=self.root, size=result.total_size,
            is_dir=True, category=FOLDER_CATEGORY, children=children,
        )
        result.tree_truncated = self._truncated
        return result

    # -- internals ------------------------------------------------------------
    def _leaf_node(self, name: str, path: str, size: int) -> Optional[TreeNode]:
        """A file TreeNode, or None past the node cap (aggregates still count it)."""
        if self._tree_nodes_built >= self._max_tree_nodes:
            self._truncated = True
            return None
        self._tree_nodes_built += 1
        return TreeNode(name=name, path=path, size=size, is_dir=False, category=category_for(path, False))

    def _safe_scandir(self, path: str) -> Optional[Iterator["os.DirEntry[str]"]]:
        try:
            return os.scandir(path)
        except PermissionError:
            self._denied += 1
            return None
        except OSError:
            self._skipped += 1
            return None

    def _finish_frame(self, frames: list[_Frame]) -> None:
        frame = frames.pop()
        frame.node.children.sort(key=lambda n: n.size, reverse=True)
        if not frames:
            return
        parent = frames[-1]
        parent.node.children.append(frame.node)
        parent.node.size += frame.node.size

    def _scan_tree_node(self, top: str, name: str) -> TreeNode:
        """Iterative (never recursive) full-tree build of everything under ``top``."""
        root_node = TreeNode(name=name, path=top, size=0, is_dir=True, category=FOLDER_CATEGORY)
        frames: list[_Frame] = [_Frame(node=root_node, iterator=self._safe_scandir(top))]

        while frames:
            if self._cancel.is_set():
                break
            frame = frames[-1]
            if frame.iterator is None:
                self._finish_frame(frames)
                continue
            entry = next(frame.iterator, _SENTINEL)
            if entry is _SENTINEL:
                frame.iterator.close()  # type: ignore[union-attr]
                self._finish_frame(frames)
                continue
            try:
                if self._is_real_dir(entry):
                    self._dirs += 1
                    child_node = TreeNode(name=entry.name, path=entry.path, size=0, is_dir=True, category=FOLDER_CATEGORY)
                    frames.append(_Frame(node=child_node, iterator=self._safe_scandir(entry.path)))
                elif entry.is_file(follow_symlinks=False):
                    size = self._record_file(entry)
                    frame.node.size += size
                    leaf = self._leaf_node(entry.name, entry.path, size)
                    if leaf is not None:
                        frame.node.children.append(leaf)
            except PermissionError:
                self._denied += 1
            except OSError:
                self._skipped += 1

        # Cancelled mid-scan: close any still-open iterators and fold every
        # open frame into its parent so the returned tree stays structurally
        # consistent even when partial. A no-op on normal completion.
        while len(frames) > 1:
            top_frame = frames[-1]
            if top_frame.iterator is not None:
                top_frame.iterator.close()  # type: ignore[union-attr]
            self._finish_frame(frames)
        if frames and frames[0].iterator is not None:
            frames[0].iterator.close()  # type: ignore[union-attr]

        root_node.children.sort(key=lambda n: n.size, reverse=True)
        return root_node
