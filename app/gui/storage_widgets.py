"""Small widgets for the Storage view: the colour legend that doubles as the file-type filter."""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from app.gui.components import font
from app.gui.treemap_view import _label_color
from app.utils.constants import CATEGORY_COLORS, COLORS
from app.utils.file_categories import CATEGORY_NAMES, FOLDER_CATEGORY


class CategoryLegend(tk.Frame):
    """Colour key for the treemap. Click a file type to dim everything else; click it again to clear."""

    def __init__(self, parent: tk.Misc, on_change: Callable[[Optional[str]], None]) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self._on_change = on_change
        self._active: Optional[str] = None
        self.chips: dict[str, tk.Label] = {}
        tk.Label(self, text="File types", bg=COLORS["bg"], fg=COLORS["text_dim"], font=font(9)).pack(side="left", padx=(0, 6))
        for name in (FOLDER_CATEGORY, *CATEGORY_NAMES):
            color = CATEGORY_COLORS[name]
            chip = tk.Label(
                self, text=name, bg=color, fg=_label_color(color), font=font(8), padx=6, pady=1,
                highlightthickness=2, highlightbackground=COLORS["bg"], highlightcolor=COLORS["bg"],
                cursor="arrow" if name == FOLDER_CATEGORY else "hand2",
            )
            chip.pack(side="left", padx=(0, 4))
            if name != FOLDER_CATEGORY:  # folders are always shown, so they are not a filter
                chip.bind("<Button-1>", lambda _e, n=name: self.toggle(n))
            self.chips[name] = chip

    @property
    def active(self) -> Optional[str]:
        return self._active

    def toggle(self, category: str) -> None:
        self.set_active(None if self._active == category else category)

    def set_active(self, category: Optional[str]) -> None:
        self._active = category
        for name, chip in self.chips.items():
            selected = name == category
            chip.configure(highlightbackground="#FFFFFF" if selected else COLORS["bg"])
        self._on_change(category)
