"""Reusable dark-theme widgets shared by all views."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Sequence

from app.utils import constants as c
from app.utils.constants import COLORS, FONT_FAMILY


def font(size: int = 10, weight: str = "normal") -> tuple:
    return (FONT_FAMILY, size, weight)


def usage_color(kind: str, percent: Optional[float]) -> str:
    """Bar colour for a usage percentage. Accent normally, amber/red only when it matters."""
    if percent is None:
        return COLORS["accent"]
    warn, crit = {
        "cpu": (c.CPU_HIGH_PERCENT, c.CPU_CRITICAL_PERCENT),
        "memory": (c.MEMORY_HIGH_PERCENT, c.MEMORY_CRITICAL_PERCENT),
        "storage": (c.STORAGE_LOW_PERCENT, c.STORAGE_CRITICAL_PERCENT),
    }.get(kind, (101, 101))
    if percent >= crit:
        return COLORS["danger"]
    if percent >= warn:
        return COLORS["warning"]
    return COLORS["accent"]


# --------------------------------------------------------------------------- #
# Theme
# --------------------------------------------------------------------------- #
def apply_theme(root: tk.Misc) -> None:
    """Configure ttk styles (tables, scrollbars, drop-downs) for the dark theme."""
    root.configure(bg=COLORS["bg"])
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(
        "Oscope.Treeview",
        background=COLORS["card"],
        fieldbackground=COLORS["card"],
        foreground=COLORS["text"],
        borderwidth=0,
        rowheight=28,
        font=font(10),
    )
    style.map(
        "Oscope.Treeview",
        background=[("selected", COLORS["select"])],
        foreground=[("selected", COLORS["text"])],
    )
    style.layout("Oscope.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure(
        "Oscope.Treeview.Heading",
        background=COLORS["bg_alt"],
        foreground=COLORS["text_dim"],
        relief="flat",
        borderwidth=0,
        font=font(9, "bold"),
        padding=(8, 7),
    )
    style.map("Oscope.Treeview.Heading", background=[("active", COLORS["hover"])])

    style.configure(
        "Oscope.Vertical.TScrollbar",
        background=COLORS["border"],
        troughcolor=COLORS["card"],
        bordercolor=COLORS["card"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
        arrowcolor=COLORS["text_dim"],
        relief="flat",
    )
    style.map("Oscope.Vertical.TScrollbar", background=[("active", COLORS["text_dim"])])

    style.configure(
        "Oscope.TCombobox",
        fieldbackground=COLORS["bg_alt"],
        background=COLORS["bg_alt"],
        foreground=COLORS["text"],
        arrowcolor=COLORS["text_dim"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["bg_alt"],
        darkcolor=COLORS["bg_alt"],
        padding=4,
    )
    style.map(
        "Oscope.TCombobox",
        fieldbackground=[("readonly", COLORS["bg_alt"])],
        foreground=[("readonly", COLORS["text"])],
        selectbackground=[("readonly", COLORS["bg_alt"])],
        selectforeground=[("readonly", COLORS["text"])],
    )
    root.option_add("*TCombobox*Listbox.background", COLORS["bg_alt"])
    root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", COLORS["select"])
    root.option_add("*TCombobox*Listbox.selectForeground", COLORS["text"])


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #
class Card(tk.Frame):
    """A padded panel with a thin border. Put child widgets in ``card.body``."""

    def __init__(self, parent: tk.Misc, padding: int = 16, **kwargs) -> None:
        super().__init__(
            parent,
            bg=COLORS["card"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["border"],
            **kwargs,
        )
        self.body = tk.Frame(self, bg=COLORS["card"])
        self.body.pack(fill="both", expand=True, padx=padding, pady=padding)


class ProgressBar(tk.Canvas):
    """A slim pill-shaped progress bar (0-100)."""

    def __init__(self, parent: tk.Misc, height: int = 8, bg: str = COLORS["card"]) -> None:
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, bd=0)
        self._height = height
        self._percent = 0.0
        self._color = COLORS["accent"]
        self.bind("<Configure>", lambda _e: self._redraw())

    def set(self, percent: Optional[float], color: Optional[str] = None) -> None:
        self._percent = min(max(percent or 0.0, 0.0), 100.0)
        if color:
            self._color = color
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        h = self._height
        if width <= h:
            return
        y = h / 2
        left, right = h / 2, width - h / 2
        self.create_line(left, y, right, y, width=h, capstyle="round", fill=COLORS["track"])
        if self._percent > 0:
            fill_right = max(left, left + (right - left) * self._percent / 100.0)
            self.create_line(left, y, fill_right, y, width=h, capstyle="round", fill=self._color)


class FlatButton(tk.Label):
    """A flat button with a hover effect (a Label, so colours are fully controllable)."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Optional[Callable[[], None]] = None,
        primary: bool = False,
    ) -> None:
        self._primary = primary
        self._command = command
        self._enabled = True
        self._normal = COLORS["accent"] if primary else COLORS["card"]
        self._hover = COLORS["accent_hover"] if primary else COLORS["hover"]
        super().__init__(
            parent,
            text=text,
            bg=self._normal,
            fg="#FFFFFF" if primary else COLORS["text"],
            font=font(10, "bold" if primary else "normal"),
            padx=14,
            pady=7,
            cursor="hand2",
            highlightthickness=0 if primary else 1,
            highlightbackground=COLORS["border"],
        )
        self.bind("<Enter>", lambda _e: self._enabled and self.configure(bg=self._hover))
        self.bind("<Leave>", lambda _e: self.configure(bg=self._normal))
        self.bind("<ButtonRelease-1>", self._on_click)

    def _on_click(self, event: tk.Event) -> None:
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if self._enabled and inside and self._command:
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(
            fg=(("#FFFFFF" if self._primary else COLORS["text"]) if enabled else COLORS["text_dim"]),
            cursor="hand2" if enabled else "arrow",
            bg=self._normal,
        )

    def set_text(self, text: str) -> None:
        self.configure(text=text)


class MetricCard(Card):
    """Title, big value, sub-line and optional progress bar (used on the Overview)."""

    def __init__(self, parent: tk.Misc, title: str, with_bar: bool = True) -> None:
        super().__init__(parent)
        tk.Label(
            self.body, text=title, bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10), anchor="w"
        ).pack(fill="x")
        # width=1 stops long text from forcing the card wider; fill="x" then stretches the label
        self.value_label = tk.Label(
            self.body, text="—", bg=COLORS["card"], fg=COLORS["text"], font=font(20, "bold"), anchor="w", width=1
        )
        self.value_label.pack(fill="x", pady=(4, 0))
        self.sub_label = tk.Label(
            self.body, text=" ", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9), anchor="w",
            justify="left", width=1,
        )
        self.sub_label.pack(fill="x", pady=(0, 8))
        self.body.bind("<Configure>", lambda e: self.sub_label.configure(wraplength=max(e.width - 4, 80)))
        self.bar: Optional[ProgressBar] = None
        if with_bar:
            self.bar = ProgressBar(self.body)
            self.bar.pack(fill="x")

    def set(self, value: str, sub: str = " ", percent: Optional[float] = None, color: Optional[str] = None) -> None:
        self.value_label.configure(text=value)
        self.sub_label.configure(text=sub)
        if self.bar is not None:
            self.bar.set(percent, color)


class BarList(tk.Frame):
    """Rows of ``label | horizontal bar | size`` (used for the largest directories)."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=COLORS["card"])
        self.columnconfigure(1, weight=1)

    def set_items(self, items: Sequence[tuple[str, int, str]]) -> None:
        """``items`` = ``(label, value, value_text)``; bars are scaled to the largest value."""
        for child in self.winfo_children():
            child.destroy()
        peak = max((value for _, value, _ in items), default=0) or 1
        for row, (label, value, text) in enumerate(items):
            tk.Label(
                self, text=label, bg=COLORS["card"], fg=COLORS["text"], font=font(10), anchor="w", width=24
            ).grid(row=row, column=0, sticky="w", pady=5)
            bar = ProgressBar(self, height=10)
            bar.grid(row=row, column=1, sticky="ew", padx=12)
            bar.set(value / peak * 100.0, COLORS["accent"])
            tk.Label(
                self, text=text, bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10), anchor="e", width=10
            ).grid(row=row, column=2, sticky="e")


def make_table(parent: tk.Misc, columns: Sequence[tuple[str, str, int, str]], height: int = 12):
    """Create a styled table with a scrollbar.

    ``columns`` = ``(column_id, heading, width_px, anchor)``.
    Returns ``(container_frame, treeview)``.
    """
    container = tk.Frame(parent, bg=COLORS["card"])
    tree = ttk.Treeview(
        container,
        columns=[col[0] for col in columns],
        show="headings",
        selectmode="browse",
        height=height,
        style="Oscope.Treeview",
    )
    for column_id, heading, width, anchor in columns:
        tree.heading(column_id, text=heading, anchor=anchor if anchor != "w" else "w")
        tree.column(column_id, width=width, minwidth=120 if column_id == columns[0][0] else 55, anchor=anchor, stretch=True)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=tree.yview, style="Oscope.Vertical.TScrollbar")
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    return container, tree


def section_title(parent: tk.Misc, text: str, bg: str = COLORS["card"]) -> tk.Label:
    """Small upper-case caption used at the top of cards (e.g. ``SYSTEM STATUS``)."""
    return tk.Label(parent, text=text.upper(), bg=bg, fg=COLORS["text_dim"], font=font(9, "bold"), anchor="w")
