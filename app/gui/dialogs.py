"""Settings and About dialogs."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from app.core import windows_backend
from app.gui.components import FlatButton, font
from app.history.settings_store import AppSettings  # noqa: F401 - re-exported for the main window
from app.utils.constants import APP_NAME, COLORS, REFRESH_INTERVAL_CHOICES


def _make_dialog(root: tk.Misc, title: str, width: int, height: int) -> tk.Toplevel:
    dialog = tk.Toplevel(root)
    dialog.title(title)
    dialog.configure(bg=COLORS["card"])
    dialog.resizable(False, False)
    dialog.transient(root)
    scale = root.winfo_fpixels("1i") / 96.0
    width, height = int(width * scale), int(height * scale)
    x = root.winfo_rootx() + (root.winfo_width() - width) // 2
    y = root.winfo_rooty() + (root.winfo_height() - height) // 3
    dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
    dialog.update_idletasks()
    windows_backend.apply_dark_title_bar(dialog.winfo_id())
    dialog.grab_set()
    return dialog


def show_about(root: tk.Misc) -> None:
    """The About OScope dialog."""
    dialog = _make_dialog(root, f"About {APP_NAME}", 460, 330)
    box = tk.Frame(dialog, bg=COLORS["card"])
    box.pack(fill="both", expand=True, padx=28, pady=24)
    tk.Label(box, text=APP_NAME, bg=COLORS["card"], fg=COLORS["text"], font=font(20, "bold"), anchor="w").pack(fill="x")
    tk.Label(
        box,
        text=(
            "A lightweight system health and resource\n"
            "analyzer for Windows.\n\n"
            "Built as an academic project for\n"
            "Windows & Linux Internals and Commands.\n\n"
            "OScope demonstrates how applications can\n"
            "retrieve operating-system information and\n"
            "present it in a simplified interface."
        ),
        bg=COLORS["card"],
        fg=COLORS["text_dim"],
        font=font(10),
        justify="left",
        anchor="w",
    ).pack(fill="x", pady=(10, 0))
    FlatButton(box, "Close", dialog.destroy, primary=True).pack(side="bottom", anchor="e")


def show_settings(root: tk.Misc, settings: AppSettings, on_apply: Callable[[AppSettings], None]) -> None:
    """The Settings dialog: refresh interval, large-file threshold, theme info."""
    dialog = _make_dialog(root, "Settings", 440, 330)
    box = tk.Frame(dialog, bg=COLORS["card"])
    box.pack(fill="both", expand=True, padx=28, pady=24)
    box.columnconfigure(1, weight=1)

    tk.Label(box, text="Settings", bg=COLORS["card"], fg=COLORS["text"], font=font(16, "bold"), anchor="w").grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 16)
    )

    tk.Label(box, text="Refresh interval (seconds)", bg=COLORS["card"], fg=COLORS["text"], font=font(10)).grid(
        row=1, column=0, sticky="w", pady=8
    )
    interval_var = tk.StringVar(value=str(settings.refresh_interval))
    ttk.Combobox(
        box,
        textvariable=interval_var,
        values=[str(v) for v in REFRESH_INTERVAL_CHOICES],
        state="readonly",
        width=6,
        style="Oscope.TCombobox",
    ).grid(row=1, column=1, sticky="e")

    tk.Label(box, text="Large-file threshold (MB)", bg=COLORS["card"], fg=COLORS["text"], font=font(10)).grid(
        row=2, column=0, sticky="w", pady=8
    )
    threshold_var = tk.StringVar(value=str(settings.large_file_mb))
    tk.Entry(
        box,
        textvariable=threshold_var,
        width=8,
        justify="center",
        bg=COLORS["bg_alt"],
        fg=COLORS["text"],
        insertbackground=COLORS["text"],
        relief="flat",
        font=font(11),
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["accent"],
    ).grid(row=2, column=1, sticky="e", ipady=3)

    tk.Label(box, text="Theme", bg=COLORS["card"], fg=COLORS["text"], font=font(10)).grid(
        row=3, column=0, sticky="w", pady=8
    )
    tk.Label(box, text="Dark (fixed in this version)", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10)).grid(
        row=3, column=1, sticky="e"
    )

    error = tk.Label(box, text="", bg=COLORS["card"], fg=COLORS["danger"], font=font(9), anchor="w")
    error.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def apply() -> None:
        try:
            threshold = int(threshold_var.get().strip())
            if threshold < 1:
                raise ValueError
        except ValueError:
            error.configure(text="Large-file threshold must be a whole number of MB (1 or more).")
            return
        settings.refresh_interval = int(interval_var.get())
        settings.large_file_mb = threshold
        on_apply(settings)
        dialog.destroy()

    buttons = tk.Frame(box, bg=COLORS["card"])
    buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(24, 0))
    FlatButton(buttons, "Cancel", dialog.destroy).pack(side="left")
    FlatButton(buttons, "Save", apply, primary=True).pack(side="left", padx=(8, 0))
