"""Settings and About dialogs."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from typing import TYPE_CHECKING, Callable, Mapping, Optional

from app.core import windows_backend
from app.collectors import labels
from app.collectors.base import Reading
from app.gui.components import FlatButton, font
from app.history.settings_store import AppSettings  # noqa: F401 - re-exported for the main window
from app.utils.constants import APP_NAME, COLORS, REFRESH_INTERVAL_CHOICES

if TYPE_CHECKING:  # only for type hints; the dialog works without a history service
    from app.history.service import HistoryService


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


def show_settings(
    root: tk.Misc,
    settings: AppSettings,
    on_apply: Callable[[AppSettings], None],
    history: Optional["HistoryService"] = None,
) -> tk.Toplevel:
    """The Settings dialog: refresh interval, large-file threshold, history, theme info."""
    dialog = _make_dialog(root, "Settings", 480, 470)
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

    history_var = tk.BooleanVar(value=settings.history_enabled)
    tk.Checkbutton(
        box, text="History: remember measurements on this PC", variable=history_var, bg=COLORS["card"],
        fg=COLORS["text"], selectcolor=COLORS["bg_alt"], activebackground=COLORS["card"],
        activeforeground=COLORS["text"], font=font(10), highlightthickness=0, bd=0, cursor="hand2", anchor="w",
    ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 4))
    history_note = tk.Label(
        box,
        text=("Used to answer \"What changed recently?\". Only numbers and program names are kept, "
              "in a file on this PC; nothing is sent anywhere."),
        bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9), anchor="w", justify="left", wraplength=400,
    )
    history_note.grid(row=5, column=0, columnspan=2, sticky="w")
    history_status = tk.Label(
        box, text=history.status_text() if history is not None else "", bg=COLORS["card"], fg=COLORS["text_dim"],
        font=font(9), anchor="w", justify="left", wraplength=400,
    )
    history_status.grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def clear_history() -> None:
        if history is None:
            return
        if not messagebox.askyesno(
            APP_NAME, "Delete everything OScope has remembered on this PC?\nThis cannot be undone.", parent=dialog
        ):
            return
        done = history.clear()
        history_status.configure(text="History cleared." if done else "History could not be cleared.")

    if history is not None:
        FlatButton(box, "Clear history", clear_history).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

    error = tk.Label(box, text="", bg=COLORS["card"], fg=COLORS["danger"], font=font(9), anchor="w")
    error.grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 0))

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
        settings.history_enabled = history_var.get()
        on_apply(settings)
        dialog.destroy()

    buttons = tk.Frame(box, bg=COLORS["card"])
    buttons.grid(row=9, column=0, columnspan=2, sticky="e", pady=(16, 0))
    FlatButton(buttons, "Cancel", dialog.destroy).pack(side="left")
    FlatButton(buttons, "Save", apply, primary=True).pack(side="left", padx=(8, 0))
    return dialog


def show_evidence_details(root: tk.Misc, readings: Mapping[str, Reading]) -> tk.Toplevel:
    """Every evidence source with its state, the reason for any gap, and where the number came from."""
    dialog = _make_dialog(root, "What OScope can measure on this PC", 680, 520)
    box = tk.Frame(dialog, bg=COLORS["card"])
    box.pack(fill="both", expand=True, padx=24, pady=20)
    FlatButton(box, "Close", dialog.destroy, primary=True).pack(side="bottom", anchor="e", pady=(12, 0))
    holder = tk.Frame(box, bg=COLORS["card"])
    holder.pack(side="top", fill="both", expand=True)
    scrollbar = ttk.Scrollbar(holder, orient="vertical", style="Oscope.Vertical.TScrollbar")
    text = tk.Text(
        holder, wrap="word", bg=COLORS["bg_alt"], fg=COLORS["text"], relief="flat", font=font(10), padx=12, pady=10,
        highlightthickness=0, yscrollcommand=scrollbar.set, cursor="arrow",
    )
    scrollbar.configure(command=text.yview)
    scrollbar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    text.insert("1.0", labels.evidence_report(readings))
    text.configure(state="disabled")  # read-only, but still selectable for copying
    return dialog
