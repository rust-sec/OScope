"""Small pure helpers over a scanned TreeNode tree: find a node's ancestors, search, list big folders."""

from __future__ import annotations

import os
from typing import Iterator, Optional

from app.core.tree_scanner import TreeNode


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def find_chain(root: TreeNode, path: str) -> list[TreeNode]:
    """``[root, ..., node]`` for the item at ``path``, or ``[]`` if it is not in the tree.

    Synthetic "(N smaller files)" items have no path and are never found this way.
    """
    if not path:
        return []
    target = _norm(path)
    chain = [root]
    node = root
    while True:
        if _norm(node.path) == target:
            return chain
        prefix = _norm(node.path).rstrip(os.sep) + os.sep
        if not target.startswith(prefix):
            return []
        for child in node.children:
            if not child.path:
                continue
            child_path = _norm(child.path)
            if target == child_path or target.startswith(child_path.rstrip(os.sep) + os.sep):
                node = child
                chain.append(child)
                break
        else:
            return []


def iter_nodes(root: TreeNode) -> Iterator[TreeNode]:
    """Every node under (and including) ``root``, depth-first, without recursion."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def search(
    root: TreeNode, needle: str, limit: int = 500, category: Optional[str] = None
) -> tuple[list[TreeNode], bool]:
    """Items whose name contains ``needle`` (case-insensitive), largest first.

    ``category`` keeps only files of that category. Returns ``(matches, more_exist)``: the list is cut
    at ``limit`` after sorting, and ``more_exist`` says whether anything was left out.
    """
    needle = needle.strip().lower()
    if not needle:
        return [], False
    matches = [
        node for node in iter_nodes(root)
        if node is not root and not node.synthetic and needle in node.name.lower()
        and (category is None or (not node.is_dir and node.category == category))
    ]
    matches.sort(key=lambda node: node.size, reverse=True)
    return matches[:limit], len(matches) > limit


def top_dirs(root: TreeNode, max_depth: int = 3, limit: int = 200) -> list[tuple[str, int, int]]:
    """The biggest folders down to ``max_depth`` levels below ``root``: ``(path, size, depth)``, largest first."""
    found: list[tuple[str, int, int]] = []
    stack = [(child, 1) for child in root.children if child.is_dir]
    while stack:
        node, depth = stack.pop()
        found.append((node.path, node.size, depth))
        if depth < max_depth:
            stack.extend((child, depth + 1) for child in node.children if child.is_dir)
    found.sort(key=lambda item: item[1], reverse=True)
    return found[:limit]
