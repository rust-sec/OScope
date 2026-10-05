"""The folder tree next to the treemap: expand folders, sort, and stay in sync with what is selected.

Rows are created lazily (a folder's children appear when it is opened), so even a scan with hundreds
of thousands of items opens instantly. The panel never changes the scan data; it only displays it.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from app.core.tree_scanner import TreeNode
from app.core.tree_utils import find_chain
from app.gui.components import font
from app.utils.constants import COLORS
from app.utils.formatting import format_bytes

MAX_CHILDREN_SHOWN = 500   # per folder; the rest is summarised in one "... N more" row

_DUMMY = ":placeholder"
_MORE = "more:"


class TreePanel(tk.Frame):
    """A ``ttk.Treeview`` of the scanned folders: Name | Size | % of folder."""

    def __init__(
        self,
        parent: tk.Misc,
        on_select: Callable[[TreeNode], None],
        on_activate: Callable[[TreeNode], None],
    ) -> None:
        super().__init__(parent, bg=COLORS["card"])
        self._on_select = on_select
        self._on_activate = on_activate
        self._root: Optional[TreeNode] = None
        self._nodes: dict[str, TreeNode] = {}
        self._iids: dict[int, str] = {}
        self._counter = 0
        self._sort_key = "size"
        self._sort_descending = True
        # Tk delivers <<TreeviewSelect>> later, from its event queue. So instead of a flag held during the call,
        # remember which row WE selected and swallow exactly that one echo; real clicks are always reported.
        self._expected: Optional[str] = None

        self.tree = ttk.Treeview(
            self, columns=("size", "pct"), show="tree headings", selectmode="browse", style="Oscope.Treeview"
        )
        self.tree.heading("#0", text="Name", anchor="w", command=lambda: self.sort_by("name"))
        self.tree.heading("size", text="Size", anchor="e", command=lambda: self.sort_by("size"))
        self.tree.heading("pct", text="%", anchor="e", command=lambda: self.sort_by("size"))
        self.tree.column("#0", width=110, minwidth=90, stretch=True)
        self.tree.column("size", width=66, minwidth=56, anchor="e", stretch=False)
        self.tree.column("pct", width=38, minwidth=34, anchor="e", stretch=False)
        self.tree.tag_configure("hidden", foreground=COLORS["text_dim"])
        self.tree.tag_configure("denied", foreground=COLORS["warning"])
        self.tree.tag_configure("grouped", foreground=COLORS["text_dim"], font=font(10, "italic"))
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview, style="Oscope.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_double_click)
        self._update_headings()

    # -- public ---------------------------------------------------------------------------
    @property
    def root_node(self) -> Optional[TreeNode]:
        return self._root

    def set_root(self, root: Optional[TreeNode]) -> None:
        """Show ``root`` (opened one level), or an empty panel for None."""
        self._root = root
        self._clear()
        if root is None:
            return
        iid = self._insert("", root)
        self._populate(iid)
        self.tree.item(iid, open=True)

    def select_node(self, node: Optional[TreeNode]) -> bool:
        """Highlight ``node`` (opening its ancestors). Does not report the change back. False if it is not shown."""
        if node is None or self._root is None or not node.path:
            self.clear_selection()
            return False
        chain = find_chain(self._root, node.path)
        if not chain:
            self.clear_selection()
            return False
        iid = self._iids.get(id(chain[0]))
        for ancestor in chain[1:]:
            if iid is None:
                return False
            self._populate(iid)
            self.tree.item(iid, open=True)
            iid = self._iids.get(id(ancestor))
        if iid is None:
            return False  # beyond the per-folder row limit
        if self.tree.selection() != (iid,):
            self._expected = iid  # the selection really changes, so exactly one echo event will follow
            self.tree.selection_set(iid)
        self.tree.see(iid)
        return True

    def clear_selection(self) -> None:
        selection = self.tree.selection()
        if selection:
            self.tree.selection_remove(*selection)  # its echo has no selected node, so it is ignored anyway

    def sort_by(self, key: str) -> None:
        if key == self._sort_key:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_key = key
            self._sort_descending = key == "size"  # sizes: biggest first; names: A to Z
        self._update_headings()
        self._rebuild()

    def selected_node(self) -> Optional[TreeNode]:
        selection = self.tree.selection()
        return self._nodes.get(selection[0]) if selection else None

    def visible_names(self, parent: Optional[TreeNode] = None) -> list[str]:
        """Names of the rows currently shown under ``parent`` (the root by default); handy for tests."""
        node = parent or self._root
        iid = self._iids.get(id(node)) if node is not None else None
        if iid is None:
            return []
        return [
            self.tree.item(child, "text") for child in self.tree.get_children(iid)
            if not child.endswith(_DUMMY) and not child.startswith(_MORE)
        ]

    # -- building rows -----------------------------------------------------------------
    def _clear(self) -> None:
        self._nodes.clear()
        self._iids.clear()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)

    def _new_iid(self) -> str:
        self._counter += 1
        return f"n{self._counter}"

    def _insert(self, parent_iid: str, node: TreeNode, parent_size: int = 0) -> str:
        iid = self._new_iid()
        self._nodes[iid] = node
        self._iids[id(node)] = iid
        if node.denied:
            text, tags = f"{node.name}  ⚠", ("denied",)
        elif node.synthetic:
            text, tags = node.name, ("grouped",)
        elif node.hidden:
            text, tags = node.name, ("hidden",)
        else:
            text, tags = node.name, ()
        size_text = "?" if node.denied else format_bytes(node.size)
        self.tree.insert(parent_iid, "end", iid=iid, text=text, values=(size_text, self._percent(node, parent_size)), tags=tags)
        if node.is_dir and node.children:
            self.tree.insert(iid, "end", iid=iid + _DUMMY, text="")  # makes the open arrow appear; replaced on open
        return iid

    @staticmethod
    def _percent(node: TreeNode, parent_size: int) -> str:
        if node.denied or parent_size <= 0:
            return ""
        share = node.size / parent_size * 100
        return "<1" if 0 < share < 1 else f"{share:.0f}"

    def _sorted_children(self, node: TreeNode) -> list[TreeNode]:
        if self._sort_key == "name":
            return sorted(node.children, key=lambda c: (not c.is_dir, c.name.lower()), reverse=self._sort_descending)
        return sorted(node.children, key=lambda c: c.size, reverse=self._sort_descending)

    def _populate(self, iid: str) -> None:
        """Create the rows of a folder's children, once (the placeholder row is the marker)."""
        dummy = iid + _DUMMY
        if not self.tree.exists(dummy):
            return
        self.tree.delete(dummy)
        node = self._nodes[iid]
        children = self._sorted_children(node)
        for child in children[:MAX_CHILDREN_SHOWN]:
            self._insert(iid, child, node.size)
        hidden = len(children) - MAX_CHILDREN_SHOWN
        if hidden > 0:
            self.tree.insert(iid, "end", iid=f"{_MORE}{iid}", text=f"… {hidden:,} more items (use Search or the treemap)", tags=("grouped",))

    def _rebuild(self) -> None:
        """Re-create the rows after a sort change, keeping the opened folders open and the selection."""
        if self._root is None:
            return
        open_paths = [node.path for iid, node in self._nodes.items() if node.is_dir and self.tree.item(iid, "open")]
        selected = self.selected_node()
        self.set_root(self._root)
        for path in sorted(open_paths, key=len):
            chain = find_chain(self._root, path)
            if chain and chain[-1].path:
                self._open_chain(chain)
        if selected is not None:
            self.select_node(selected)

    def _open_chain(self, chain: list[TreeNode]) -> None:
        iid = self._iids.get(id(chain[0]))
        for node in chain[1:] + [None]:  # type: ignore[list-item]
            if iid is None:
                return
            self._populate(iid)
            self.tree.item(iid, open=True)
            iid = self._iids.get(id(node)) if node is not None else None

    def _update_headings(self) -> None:
        arrow = "  ▼" if self._sort_descending else "  ▲"
        self.tree.heading("#0", text="Name" + (arrow if self._sort_key == "name" else ""))
        self.tree.heading("size", text="Size" + (arrow if self._sort_key == "size" else ""))

    # -- events ----------------------------------------------------------------------------
    def _on_open(self, _event: object) -> None:
        iid = self.tree.focus()
        if iid and iid in self._nodes:
            self._populate(iid)

    def _on_tree_select(self, _event: object) -> None:
        selection = self.tree.selection()
        if self._expected is not None:
            expected, self._expected = self._expected, None
            if selection == (expected,):
                return  # the echo of our own select_node(); not something the user did
        node = self.selected_node()
        if node is not None and (node.path or node.synthetic):
            self._on_select(node)

    def _on_double_click(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        node = self._nodes.get(iid) if iid else None
        if node is not None and node.is_dir and node.children:
            self._on_activate(node)
