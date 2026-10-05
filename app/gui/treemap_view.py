"""Interactive treemap for the Storage Analyzer: rectangles sized by file
size, coloured by file-type category, clickable, hoverable, and zoomable.
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from typing import Callable, Optional, Sequence

from app.core.tree_scanner import TreeNode
from app.gui.components import Card, FlatButton, font, section_title
from app.gui.treemap_layout import Rect, layout_children
from app.utils.constants import CATEGORY_COLORS, COLORS, TREEMAP_MAX_RECTS_PER_LEVEL
from app.utils.formatting import format_bytes, format_count

_MUTED = "#323847"  # file rectangles outside the active category filter


def _label_color(fill: str) -> str:
    """Dark text on light fills, white text on dark fills (WCAG-ish relative luminance)."""
    r, g, b = int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#1A1D24" if luminance > 150 else "#FFFFFF"


def _more_items_node(hidden: Sequence[TreeNode]) -> TreeNode:
    """A synthetic, non-navigable node representing the smallest items past the render cap."""
    total = sum(node.size for node in hidden)
    return TreeNode(
        name=f"+{len(hidden)} more items",
        path="",
        size=total,
        is_dir=False,
        category="Other",
    )


class TreemapCanvas(tk.Canvas):
    """Draws one folder's immediate children as a squarified treemap."""

    _MIN_LABEL_W = 40
    _MIN_LABEL_H = 16

    def __init__(
        self,
        parent: tk.Misc,
        on_select: Callable[[TreeNode], None],
        on_zoom: Callable[[TreeNode], None],
        on_back: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent, bg=COLORS["bg"], highlightthickness=0, bd=0)
        self._on_select = on_select
        self._on_zoom = on_zoom
        self._on_back = on_back
        self._filter: Optional[str] = None
        self._tip_labels: list[tk.Label] = []
        self._node: Optional[TreeNode] = None
        self._rects: list[tuple[Rect, TreeNode]] = []
        self._selected: Optional[TreeNode] = None
        self._hovered: Optional[TreeNode] = None
        self._message: str = ""
        self._tooltip: Optional[tk.Toplevel] = None

        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<Double-Button-1>", self._on_double_click)
        self.bind("<Button-3>", lambda _e: self._on_back() if self._on_back else None)  # right-click: up one level
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._hide_tooltip())

    # -- public ---------------------------------------------------------------
    def show(self, node: Optional[TreeNode]) -> None:
        """Render ``node``'s immediate children. Call again after resize is handled internally."""
        self._node = node
        self._selected = None
        self._message = ""
        self._redraw()

    def clear(self, message: str = "") -> None:
        self._node = None
        self._rects = []
        self._selected = None
        self._message = message
        self._redraw()

    def set_selected(self, node: Optional[TreeNode]) -> None:
        self._selected = node
        self._redraw()

    def set_category_filter(self, category: Optional[str]) -> None:
        """Dim every file that is not in ``category`` (folders keep their colour). None shows everything."""
        self._filter = category
        self._redraw()

    @property
    def selected(self) -> Optional[TreeNode]:
        return self._selected

    # -- rendering --------------------------------------------------------------
    def _redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 2 or height <= 2:
            return

        if self._node is None or not self._node.children:
            self._rects = []
            text = self._message or "Nothing to show."
            self.create_text(width / 2, height / 2, text=text, fill=COLORS["text_dim"], font=font(11))
            return

        children = self._node.children
        visible = children[:TREEMAP_MAX_RECTS_PER_LEVEL]
        hidden = children[TREEMAP_MAX_RECTS_PER_LEVEL:]
        render_list = list(visible)
        if hidden:
            render_list.append(_more_items_node(hidden))

        pairs = layout_children(render_list, 0, 0, float(width), float(height))
        self._rects = pairs
        for rect, node in pairs:
            self._draw_rect(rect, node)

    def _draw_rect(self, rect: Rect, node: TreeNode) -> None:
        color = CATEGORY_COLORS.get(node.category, CATEGORY_COLORS["Other"])
        if self._filter is not None and not node.is_dir and node.category != self._filter:
            color = _MUTED
        selected = node is self._selected
        # High-contrast white ring regardless of fill colour (an accent-blue
        # ring would blend into the Images category's own sky-blue fill).
        outline = "#FFFFFF" if selected else COLORS["bg"]
        width = 3 if selected else 1
        self.create_rectangle(
            rect.x, rect.y, rect.x + rect.w, rect.y + rect.h,
            fill=color, outline=outline, width=width,
            dash=(3, 3) if node.hidden and not selected else (),  # a dashed edge marks hidden items
        )
        if rect.w > self._MIN_LABEL_W and rect.h > self._MIN_LABEL_H:
            label = ("⚠ " if node.denied else "") + node.name
            max_chars = max(int(rect.w / 7), 3)
            if len(label) > max_chars:
                label = label[: max_chars - 1] + "…"
            self.create_text(
                rect.x + 6, rect.y + 4, text=label, fill=_label_color(color),
                font=font(9), anchor="nw",
            )

    # -- hit testing --------------------------------------------------------------
    def _hit(self, x: float, y: float) -> Optional[TreeNode]:
        for rect, node in self._rects:
            if rect.x <= x <= rect.x + rect.w and rect.y <= y <= rect.y + rect.h:
                return node
        return None

    def _on_click(self, event: tk.Event) -> None:
        node = self._hit(event.x, event.y)
        if node is not None and node.path:  # the synthetic "+N more" node has no path
            self._selected = node
            self._redraw()
            self._on_select(node)

    def _on_double_click(self, event: tk.Event) -> None:
        node = self._hit(event.x, event.y)
        if node is not None and node.is_dir and node.children:
            self._on_zoom(node)

    # -- tooltip --------------------------------------------------------------
    def _on_motion(self, event: tk.Event) -> None:
        node = self._hit(event.x, event.y)
        if node is self._hovered:
            if node is not None:
                self._move_tooltip(event)
            return
        self._hovered = node
        if node is None:
            self._hide_tooltip()
        else:
            self._show_tooltip(event, node)

    def _ensure_tooltip(self) -> tk.Toplevel:
        """One tooltip window for the whole canvas, created once and re-used (moved, re-texted, hidden)."""
        if self._tooltip is None:
            top = tk.Toplevel(self)
            top.withdraw()
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            frame = tk.Frame(top, bg=COLORS["card"], highlightthickness=1, highlightbackground=COLORS["border"])
            frame.pack()
            for index in range(5):
                self._tip_labels.append(tk.Label(
                    frame, text="", bg=COLORS["card"], fg=COLORS["text"] if index == 0 else COLORS["text_dim"],
                    font=font(9, "bold" if index == 0 else "normal"), anchor="w", justify="left",
                ))
            self._tooltip = top
        return self._tooltip

    @staticmethod
    def _tip_lines(node: TreeNode) -> list[str]:
        kind = "Folder" if node.is_dir else "File"
        lines = [node.name, f"{kind}  ·  {format_bytes(node.size)}"]
        if node.denied:
            lines.append("Access denied: size unknown, not zero")
        elif node.is_dir and (node.file_count or node.dir_count):
            lines.append(f"{format_count(node.file_count)} files, {format_count(node.dir_count)} folders")
        if node.hidden:
            lines.append("Hidden")
        if node.path:
            lines.append(node.path)
        return lines[:5]

    def _show_tooltip(self, event: tk.Event, node: TreeNode) -> None:
        top = self._ensure_tooltip()
        lines = self._tip_lines(node)
        for index, label in enumerate(self._tip_labels):
            if index < len(lines):
                label.configure(text=lines[index])
                label.pack(fill="x", padx=8, pady=(6 if index == 0 else 0, 6 if index == len(lines) - 1 else 2))
            else:
                label.pack_forget()
        top.deiconify()
        self._move_tooltip(event)

    def _move_tooltip(self, event: tk.Event) -> None:
        if self._tooltip is not None:
            self._tooltip.geometry(f"+{event.x_root + 16}+{event.y_root + 16}")

    def _hide_tooltip(self) -> None:
        if self._tooltip is not None:
            self._tooltip.withdraw()
        self._hovered = None


class Breadcrumb(tk.Frame):
    """Clickable "C:\\ > Users > satya > Downloads" path bar."""

    def __init__(self, parent: tk.Misc, on_navigate: Callable[[int], None]) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self._on_navigate = on_navigate

    def set_path(self, nodes: Sequence[TreeNode]) -> None:
        for child in self.winfo_children():
            child.destroy()
        for index, node in enumerate(nodes):
            is_last = index == len(nodes) - 1
            label = tk.Label(
                self, text=node.name, bg=COLORS["bg"],
                fg=COLORS["text"] if is_last else COLORS["accent"],
                font=font(10, "bold" if is_last else "normal"),
                cursor="arrow" if is_last else "hand2",
            )
            label.pack(side="left")
            if not is_last:
                label.bind("<Button-1>", lambda _e, i=index: self._on_navigate(i))
                tk.Label(self, text="  >  ", bg=COLORS["bg"], fg=COLORS["text_dim"], font=font(10)).pack(side="left")


class DetailPanel(Card):
    """Selected file/folder: name, type, exact size, contents, flags, path, Show in Explorer.

    Compact on purpose: rows with nothing to say are hidden so the path and the Explorer button always fit.
    """

    _ROWS = ("Type", "Size", "Modified", "Contents", "Notes")

    def __init__(self, parent: tk.Misc, on_reveal: Callable[[], None]) -> None:
        super().__init__(parent, padding=12)
        self._on_reveal = on_reveal
        section_title(self.body, "Selected item").pack(fill="x")

        self.name_label = tk.Label(
            self.body, text="Click a rectangle to see details.", bg=COLORS["card"], fg=COLORS["text"],
            font=font(11, "bold"), anchor="w", justify="left", wraplength=190,
        )
        self.name_label.pack(fill="x", pady=(6, 4))

        self._rows_holder = tk.Frame(self.body, bg=COLORS["card"])
        self._rows_holder.pack(fill="x")
        self._row_frames: dict[str, tk.Frame] = {}
        self._rows: dict[str, tk.Label] = {}
        for key in self._ROWS:
            row = tk.Frame(self._rows_holder, bg=COLORS["card"])
            tk.Label(row, text=key, bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9), width=8, anchor="nw").pack(side="left", anchor="n")
            value = tk.Label(
                row, text="", bg=COLORS["card"], fg=COLORS["text"], font=font(9), anchor="w", justify="left", wraplength=125
            )
            value.pack(side="left", fill="x", expand=True)
            self._row_frames[key] = row
            self._rows[key] = value

        self.path_var = tk.StringVar(value="")
        self.path_entry = tk.Entry(
            self.body, textvariable=self.path_var, state="readonly", readonlybackground=COLORS["bg_alt"],
            fg=COLORS["text"], relief="flat", font=font(9),
        )
        self.path_entry.pack(fill="x", pady=(8, 6))
        self.reveal_button = FlatButton(self.body, "Show in File Explorer", self._on_reveal_click, primary=True)
        self.reveal_button.pack(fill="x")
        self.reveal_button.set_enabled(False)

        self.largest_label = tk.Label(
            self.body, text="", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9), anchor="w", justify="left",
            wraplength=190,
        )
        self.largest_label.pack(fill="x", pady=(8, 0))

    def _show_rows(self, texts: dict[str, str]) -> None:
        for key in self._ROWS:
            self._row_frames[key].pack_forget()
        for key in self._ROWS:
            if texts.get(key):
                self._rows[key].configure(text=texts[key])
                self._row_frames[key].pack(fill="x", pady=1)

    def clear(self, message: str = "Click a rectangle to see details.") -> None:
        self.name_label.configure(text=message)
        self._show_rows({})
        self.largest_label.configure(text="")
        self.path_var.set("")
        self.reveal_button.set_enabled(False)

    def show_node(self, node: TreeNode) -> None:
        self.name_label.configure(text=node.name)
        if node.is_dir:
            kind = "Folder"
        elif node.synthetic:
            kind = "Grouped small files"
        else:
            kind = f"File  ·  {node.category}"
        if node.denied:
            size = "Unknown (access denied)"
        else:
            size = f"{format_bytes(node.size)}  ({format_count(node.size)} bytes)"
        if node.is_dir:
            contents = f"{format_count(node.file_count)} files, {format_count(node.dir_count)} folders"
        elif node.synthetic:
            contents = f"{format_count(node.file_count)} files"
        else:
            contents = ""
        notes = []
        if node.hidden:
            notes.append("Hidden")
        if node.system:
            notes.append("System")
        if node.denied:
            notes.append("Access denied: size unknown, not zero")
        self._show_rows({
            "Type": kind,
            "Size": size,
            "Modified": datetime.fromtimestamp(node.mtime).strftime("%Y-%m-%d %H:%M") if node.mtime else "",
            "Contents": contents,
            "Notes": ", ".join(notes),
        })
        biggest = [child for child in node.children if child.size > 0][:3] if node.is_dir else []
        self.largest_label.configure(
            text=("Largest items\n" + "\n".join(f"{format_bytes(c.size):>9}  {c.name}" for c in biggest)) if biggest else ""
        )
        self.path_var.set(node.path)
        self.reveal_button.set_enabled(bool(node.path))

    def _on_reveal_click(self) -> None:
        if self.path_var.get():
            self._on_reveal()
