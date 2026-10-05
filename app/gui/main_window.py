"""Main window: header, sidebar navigation, the three views, refresh and report."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Optional

from app.core import platform as oscope_platform
from app.core import platform_ops, report, windows_backend
from app.core.sampler import Sampler, Snapshot
from app.core.system_info import get_static_info
from app.gui.components import FlatButton, apply_theme, font
from app.gui.dialogs import AppSettings, show_about, show_settings
from app.history.settings_store import SettingsStore
from app.gui.overview_view import OverviewView
from app.gui.processes_view import ProcessesView
from app.gui.storage_view import StorageView
from app.utils.background import BackgroundRunner
from app.utils.constants import APP_NAME, APP_SUBTITLE, COLORS
from app.utils.formatting import format_clock

_NAV_ITEMS = [
    ("overview", "▣   Overview"),
    ("processes", "▤   Processes"),
    ("storage", "▰   Storage"),
]


class MainWindow:
    """Owns the window, the background sampler and every view."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings_store = SettingsStore.default()
        self.settings = self.settings_store.load()
        self.runner = BackgroundRunner(root)
        self.info = get_static_info()
        self.latest: Optional[Snapshot] = None
        self._nav_buttons: dict[str, tk.Label] = {}
        self._views: dict[str, tk.Frame] = {}
        self._current = ""

        self._configure_window()
        apply_theme(root)
        self._build_header()
        self._build_sidebar()
        self._build_content()
        self.storage.set_threshold(self.settings.large_file_mb)  # restore the saved threshold
        self.show_view("overview")  # the app always opens on System Health

        root.bind("<F5>", lambda _e: self.refresh_now())
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.sampler = Sampler(
            deliver=lambda snap: self.runner.post(self._on_snapshot, snap),
            interval_seconds=self.settings.refresh_interval,
        )
        self.sampler.start()

    # ------------------------------------------------------------------------ setup
    def _configure_window(self) -> None:
        root = self.root
        root.title(f"{APP_NAME} - {APP_SUBTITLE}")
        scale = root.winfo_fpixels("1i") / 96.0
        # never open larger than the screen (1366x768 laptops are common)
        width = min(int(1200 * scale), root.winfo_screenwidth() - 40)
        height = min(int(740 * scale), root.winfo_screenheight() - 90)
        x = max((root.winfo_screenwidth() - width) // 2, 0)
        y = max((root.winfo_screenheight() - height) // 3, 0)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.minsize(min(int(1000 * scale), width), min(int(620 * scale), height))
        root.configure(bg=COLORS["bg"])
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)
        root.update_idletasks()
        windows_backend.apply_dark_title_bar(root.winfo_id())

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=COLORS["bg_alt"])
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        inner = tk.Frame(header, bg=COLORS["bg_alt"])
        inner.pack(fill="x", padx=24, pady=14)

        titles = tk.Frame(inner, bg=COLORS["bg_alt"])
        titles.pack(side="left")
        tk.Label(titles, text=APP_NAME, bg=COLORS["bg_alt"], fg=COLORS["text"], font=font(18, "bold"), anchor="w").pack(
            fill="x"
        )
        tk.Label(
            titles, text=APP_SUBTITLE, bg=COLORS["bg_alt"], fg=COLORS["text_dim"], font=font(10), anchor="w"
        ).pack(fill="x")

        right = tk.Frame(inner, bg=COLORS["bg_alt"])
        right.pack(side="right")
        FlatButton(right, "Generate Report", self.generate_report, primary=True).pack(side="right", padx=(12, 0))
        FlatButton(right, "Refresh", self.refresh_now).pack(side="right", padx=(12, 0))

        stamp = tk.Frame(right, bg=COLORS["bg_alt"])
        stamp.pack(side="right", padx=(0, 6))
        tk.Label(
            stamp,
            text=oscope_platform.platform_label(),
            bg=COLORS["bg_alt"],
            fg=COLORS["text"],
            font=font(11, "bold"),
            anchor="e",
        ).pack(fill="x")
        self.updated_label = tk.Label(
            stamp, text="Last updated: --:--:--", bg=COLORS["bg_alt"], fg=COLORS["text_dim"], font=font(9), anchor="e"
        )
        self.updated_label.pack(fill="x")

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self.root, bg=COLORS["bg_alt"], width=200)
        sidebar.grid(row=1, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.pack_propagate(False)

        tk.Frame(sidebar, bg=COLORS["border"], height=1).pack(fill="x")
        for key, text in _NAV_ITEMS:
            self._nav_buttons[key] = self._nav_item(sidebar, text, lambda k=key: self.show_view(k))

        tk.Frame(sidebar, bg=COLORS["border"], height=1).pack(fill="x", padx=16, pady=14)
        self._nav_item(sidebar, "⚙   Settings", self.open_settings)
        self._nav_item(sidebar, "ⓘ   About", lambda: show_about(self.root))

    @staticmethod
    def _nav_item(parent: tk.Misc, text: str, command) -> tk.Label:
        label = tk.Label(
            parent,
            text=text,
            bg=COLORS["bg_alt"],
            fg=COLORS["text_dim"],
            font=font(11),
            anchor="w",
            padx=22,
            pady=11,
            cursor="hand2",
        )
        label.pack(fill="x")
        label.bind("<Button-1>", lambda _e: command())
        return label

    def _build_content(self) -> None:
        self.content = tk.Frame(self.root, bg=COLORS["bg"])
        self.content.grid(row=1, column=1, sticky="nsew", padx=24, pady=20)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        self.overview = OverviewView(self.content, self.info)
        self.processes = ProcessesView(self.content, self.runner, self.refresh_now)
        self.storage = StorageView(self.content, self.runner)
        self._views = {"overview": self.overview, "processes": self.processes, "storage": self.storage}
        for view in self._views.values():
            view.grid(row=0, column=0, sticky="nsew")

    # ------------------------------------------------------------------------ navigation
    def show_view(self, name: str) -> None:
        self._current = name
        self._views[name].tkraise()
        for key, button in self._nav_buttons.items():
            active = key == name
            button.configure(
                bg=COLORS["card"] if active else COLORS["bg_alt"],
                fg=COLORS["text"] if active else COLORS["text_dim"],
            )

    # ------------------------------------------------------------------------ data flow
    def refresh_now(self) -> None:
        """Ask the sampler for a new snapshot immediately."""
        self.sampler.request_refresh()

    def _on_snapshot(self, snap: Snapshot) -> None:
        self.latest = snap
        self.updated_label.configure(text=f"Last updated: {format_clock(snap.taken_at)}")
        self.overview.update_snapshot(snap)
        self.processes.update_snapshot(snap)

    # ------------------------------------------------------------------------ dialogs
    def open_settings(self) -> None:
        show_settings(self.root, self.settings, self._apply_settings)

    def _apply_settings(self, settings: AppSettings) -> None:
        self.sampler.set_interval(settings.refresh_interval)
        self.storage.set_threshold(settings.large_file_mb)
        self.settings_store.save(settings)

    # ------------------------------------------------------------------------ report
    def generate_report(self) -> None:
        if self.latest is None:
            messagebox.showinfo(APP_NAME, "Loading system information...\nTry again in a moment.", parent=self.root)
            return
        try:
            text = report.build_report(
                self.info, self.latest, self.storage.last_result, self.storage.threshold_mb
            )
            path = report.save_report(text)
        except Exception:  # noqa: BLE001 - never show a traceback to the user
            messagebox.showerror(
                APP_NAME, "The report could not be saved. Check that the reports folder is writable.", parent=self.root
            )
            return
        if messagebox.askyesno(
            APP_NAME, f"Report saved to:\n{path}\n\nOpen the reports folder?", parent=self.root
        ):
            platform_ops.open_path(str(path.parent))  # Windows only; no-op elsewhere

    # ------------------------------------------------------------------------ shutdown
    def _on_close(self) -> None:
        self.sampler.stop()
        self.storage.cancel_running_scan()
        self.runner.close()
        self.root.destroy()


def run() -> None:
    """Create the window and enter the Tk event loop."""
    windows_backend.enable_dpi_awareness()
    root = tk.Tk()
    MainWindow(root)
    root.mainloop()
