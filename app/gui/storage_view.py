"""View 4: Storage - choose a folder, scan it on a background thread, then explore it.

Three views of the same scan stay in step: the folder tree (left), the treemap (middle) and the details
of the selected item (right). Selecting in one selects in the others. Nothing here changes any file:
the only action on a file is "Show in File Explorer".
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Optional

from app.core import platform_ops, windows_backend
from app.core.scan_summary import scan_details_text, scan_notes
from app.core.storage_manager import ScanResult
from app.core.tree_scanner import ScanStatus, TreeNode, TreeScanner, TreeScanResult
from app.core.tree_utils import find_chain, search as search_tree
from app.gui.components import BarList, Card, FlatButton, font, make_table, section_title
from app.gui.dialogs import show_text_dialog
from app.gui.storage_widgets import CategoryLegend
from app.gui.tree_panel import TreePanel
from app.gui.treemap_view import Breadcrumb, DetailPanel, TreemapCanvas
from app.history import storage as storage_history
from app.history.service import HistoryService
from app.utils.background import BackgroundRunner
from app.utils.constants import COLORS, LARGE_FILE_THRESHOLD_MB, LARGEST_DIRS_SHOWN, MAX_LARGE_FILES_KEPT
from app.utils.file_categories import category_for
from app.utils.formatting import format_bytes, format_count, shorten_middle

_FILE_COLUMNS = [
    ("file", "File", 150, "w"),
    ("category", "Type", 80, "w"),
    ("size", "Size", 75, "e"),
    ("modified", "Modified", 105, "w"),
    ("path", "Path", 260, "w"),
]
_SEARCH_COLUMNS = [
    ("name", "Name", 130, "w"),
    ("size", "Size", 62, "e"),
    ("folder", "In folder", 90, "w"),
]
SEARCH_DELAY_MS = 300
SEARCH_LIMIT = 500


class StorageView(tk.Frame):
    """Choose a folder, scan it on a background thread, explore sizes."""

    def __init__(
        self,
        parent: tk.Misc,
        runner: BackgroundRunner,
        history: Optional[HistoryService] = None,
        is_elevated: Callable[[], Optional[bool]] = platform_ops.is_elevated,
    ) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self._runner = runner
        self._history = history
        self._is_elevated = is_elevated
        self._threshold_mb = LARGE_FILE_THRESHOLD_MB
        self._cancel: Optional[threading.Event] = None
        self._scan_token = 0
        self._search_token = 0
        self._search_after: Optional[str] = None
        self._scanning = False
        self.last_result: Optional[ScanResult] = None
        self._view_mode = "treemap"
        self._zoom_stack: list[TreeNode] = []
        self._selected_node: Optional[TreeNode] = None
        self._category_filter: Optional[str] = None
        self._search_results: list[TreeNode] = []
        self._large_rows: list[dict] = []
        self._files_sort = ("size", True)
        self.last_dialog: Optional[tk.Toplevel] = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._build_toolbar()
        self._build_summary()
        self._build_banner()
        self._build_list_mode()
        self._build_treemap_mode()
        self._results_frame.grid_remove()
        self._update_view_toggle()
        self._update_back_button()
        self.after_idle(self._bind_keys)

    # ------------------------------------------------------------------------ construction
    def _build_toolbar(self) -> None:
        toolbar = tk.Frame(self, bg=COLORS["bg"])
        toolbar.grid(row=0, column=0, sticky="ew")
        self.select_button = FlatButton(toolbar, "Select Folder", self._choose_folder, primary=True)
        self.select_button.pack(side="left")
        self.downloads_button = FlatButton(toolbar, "Downloads", self._scan_downloads)
        self.downloads_button.pack(side="left", padx=(8, 0))
        self.system_button = FlatButton(toolbar, "System drive", self._scan_system_drive)
        self.system_button.pack(side="left", padx=(8, 0))
        self.stop_button = FlatButton(toolbar, "Stop", self._stop_scan)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.stop_button.set_enabled(False)

        tk.Frame(toolbar, bg=COLORS["border"], width=1).pack(side="left", fill="y", padx=12, pady=4)
        self.treemap_toggle = FlatButton(toolbar, "Treemap", lambda: self.set_view_mode("treemap"))
        self.treemap_toggle.pack(side="left")
        self.list_toggle = FlatButton(toolbar, "List", lambda: self.set_view_mode("list"))
        self.list_toggle.pack(side="left", padx=(8, 0))
        self.back_button = FlatButton(toolbar, "◀ Back", self.go_back)
        self.back_button.pack(side="left", padx=(12, 0))
        self.compare_button = FlatButton(toolbar, "Compare scans", self._compare_with_previous)
        self.compare_button.pack(side="left", padx=(8, 0))
        self.compare_button.set_enabled(False)

    def _build_summary(self) -> None:
        self.summary_card = Card(self, padding=10)
        self.summary_card.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        body = self.summary_card.body
        top = tk.Frame(body, bg=COLORS["card"])
        top.pack(fill="x")
        self.folder_label = tk.Label(
            top, text="Select a folder to analyze its size.", bg=COLORS["card"], fg=COLORS["text_dim"],
            font=font(11), anchor="w", justify="left",
        )
        self.folder_label.pack(side="left", fill="x", expand=True)
        self.elevation_label = tk.Label(top, text="", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9), anchor="e")
        self.elevation_label.pack(side="right")

        stats = tk.Frame(body, bg=COLORS["card"])
        stats.pack(fill="x", pady=(4, 0))
        stats.columnconfigure(3, weight=1)
        self._stat_labels: dict[str, tk.Label] = {}
        for column, name in enumerate(("Total size", "Files", "Directories")):
            box = tk.Frame(stats, bg=COLORS["card"])
            box.grid(row=0, column=column, sticky="w", padx=(0, 22))
            tk.Label(box, text=name, bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9), anchor="w").pack(side="left")
            value = tk.Label(box, text="—", bg=COLORS["card"], fg=COLORS["text"], font=font(12, "bold"), anchor="w")
            value.pack(side="left", padx=(6, 0))
            self._stat_labels[name] = value
        # status ("Scanning... <current folder>", "Finished in 3.2s") shares the row, right-aligned
        self.progress_label = tk.Label(stats, text="", bg=COLORS["card"], fg=COLORS["accent"], font=font(10), anchor="e", justify="right")
        self.progress_label.grid(row=0, column=3, sticky="e")
        self.progressbar = ttk.Progressbar(body, mode="indeterminate", style="Oscope.Horizontal.TProgressbar")

    def _build_banner(self) -> None:
        self.banner = tk.Frame(self, bg=COLORS["card"], highlightthickness=1, highlightbackground=COLORS["warning"])
        self.banner_text = tk.Label(
            self.banner, text="", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10), anchor="w",
            justify="left", wraplength=800,
        )
        self.banner_text.pack(side="left", fill="x", expand=True, padx=14, pady=5)
        self.details_link = tk.Label(
            self.banner, text="Details…", bg=COLORS["card"], fg=COLORS["accent"], font=font(10, "bold"), cursor="hand2"
        )
        self.details_link.pack(side="right", padx=14)
        self.details_link.bind("<Button-1>", lambda _e: self.show_scan_details())
        self.banner.bind("<Configure>", lambda e: self.banner_text.configure(wraplength=max(e.width - 140, 200)))

    def _build_list_mode(self) -> None:
        results = tk.Frame(self, bg=COLORS["bg"])
        results.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        results.columnconfigure(0, weight=2, uniform="res")
        results.columnconfigure(1, weight=3, uniform="res")
        results.rowconfigure(0, weight=1)

        self.dirs_card = Card(results)
        self.dirs_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        section_title(self.dirs_card.body, "Largest directories").pack(fill="x")
        self.dirs_list = BarList(self.dirs_card.body)
        self.dirs_list.pack(fill="x", pady=(8, 0))
        self.dirs_message = tk.Label(
            self.dirs_card.body, text="Nothing scanned yet.", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10), anchor="w"
        )
        self.dirs_message.pack(fill="x", pady=(8, 0))

        self.files_card = Card(results, padding=0)
        self.files_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        head = tk.Frame(self.files_card.body, bg=COLORS["card"])
        head.pack(fill="x", padx=16, pady=(16, 8))
        section_title(head, "Large files").pack(side="left")
        self.threshold_var = tk.StringVar(value=str(self._threshold_mb))
        self.threshold_entry = tk.Entry(
            head, textvariable=self.threshold_var, width=6, justify="center", bg=COLORS["bg_alt"], fg=COLORS["text"],
            insertbackground=COLORS["text"], relief="flat", font=font(10), highlightthickness=1,
            highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"],
        )
        self.threshold_entry.pack(side="right", ipady=2)
        tk.Label(head, text="at least (MB)", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9)).pack(side="right", padx=(0, 6))
        self.threshold_entry.bind("<Return>", lambda _e: self._apply_threshold_entry())
        self.threshold_entry.bind("<FocusOut>", lambda _e: self._apply_threshold_entry())
        self.files_note = tk.Label(head, text="", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9))
        self.files_note.pack(side="right", padx=(0, 12))

        frame, self.files_tree = make_table(self.files_card.body, _FILE_COLUMNS, height=8)
        frame.pack(fill="both", expand=True)
        for column_id, _heading, _width, _anchor in _FILE_COLUMNS:
            self.files_tree.heading(column_id, command=lambda c=column_id: self._sort_files_by(c))
        self._update_file_headings()
        self.files_message = tk.Label(self.files_card.body, text="", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10))
        self._results_frame = results

    def _build_treemap_mode(self) -> None:
        frame = tk.Frame(self, bg=COLORS["bg"])
        frame.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.treemap_frame = frame

        top = tk.Frame(frame, bg=COLORS["bg"])
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.breadcrumb = Breadcrumb(top, self._on_breadcrumb_navigate)
        self.breadcrumb.pack(side="left")
        self.tree_visible = tk.BooleanVar(value=True)
        self.tree_visible.trace_add("write", lambda *_: self._apply_tree_visibility())
        tk.Checkbutton(
            top, text="Folder tree", variable=self.tree_visible, bg=COLORS["bg"], fg=COLORS["text_dim"],
            selectcolor=COLORS["bg_alt"], activebackground=COLORS["bg"], activeforeground=COLORS["text"],
            font=font(9), highlightthickness=0, bd=0, cursor="hand2",
        ).pack(side="right", padx=(14, 0))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._schedule_search())
        self.search_entry = tk.Entry(
            top, textvariable=self.search_var, width=24, bg=COLORS["bg_alt"], fg=COLORS["text"],
            insertbackground=COLORS["text"], relief="flat", font=font(10), highlightthickness=1,
            highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"],
        )
        self.search_entry.pack(side="right", ipady=3)
        tk.Label(top, text="Search names", bg=COLORS["bg"], fg=COLORS["text_dim"], font=font(9)).pack(side="right", padx=(0, 6))

        body = tk.Frame(frame, bg=COLORS["bg"])
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3, minsize=230, uniform="cols")
        body.columnconfigure(1, weight=5, uniform="cols")
        body.columnconfigure(2, weight=3, minsize=230, uniform="cols")
        body.rowconfigure(0, weight=1)
        self._treemap_body = body

        # left: the folder tree, or search results while a search is active (both stacked in one cell)
        self._left = tk.Frame(body, bg=COLORS["bg"])
        self._left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._left.columnconfigure(0, weight=1)
        self._left.rowconfigure(0, weight=1)
        self.tree_card = Card(self._left, padding=0)
        self.tree_card.grid(row=0, column=0, sticky="nsew")
        self.tree_panel = TreePanel(self.tree_card.body, self._on_tree_select, self._on_tree_activate)
        self.tree_panel.pack(fill="both", expand=True)
        self.search_card = Card(self._left, padding=0)
        self.search_card.grid(row=0, column=0, sticky="nsew")
        self.search_header = tk.Label(
            self.search_card.body, text="", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9), anchor="w",
            justify="left", wraplength=260,
        )
        self.search_header.pack(fill="x", padx=12, pady=(10, 6))
        table, self.search_tree = make_table(self.search_card.body, _SEARCH_COLUMNS, height=8)
        table.pack(fill="both", expand=True)
        self.search_tree.bind("<<TreeviewSelect>>", self._on_search_select)
        self.tree_card.tkraise()

        canvas_card = Card(body, padding=0)
        canvas_card.grid(row=0, column=1, sticky="nsew", padx=(0, 7))
        self.treemap_canvas = TreemapCanvas(canvas_card.body, self._on_treemap_select, self._on_treemap_zoom, self.go_back)
        self.treemap_canvas.pack(fill="both", expand=True)
        self.treemap_canvas.bind("<Button-1>", lambda _e: self.treemap_canvas.focus_set(), add="+")

        self.detail_panel = DetailPanel(body, self.reveal_selected_in_explorer)
        self.detail_panel.grid(row=0, column=2, sticky="nsew")

        self.legend = CategoryLegend(frame, self._on_category_filter)
        self.legend.grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _bind_keys(self) -> None:
        """Escape stops a scan / clears a search / goes up a level; BackSpace goes up a level (not while typing)."""
        try:
            self.winfo_toplevel().bind("<Escape>", lambda _e: self._on_escape(), add="+")
        except tk.TclError:
            return
        for widget in (self.treemap_canvas, self.tree_panel.tree):
            widget.bind("<BackSpace>", lambda _e: self.go_back())

    def destroy(self) -> None:
        if self._search_after is not None:
            try:
                self.after_cancel(self._search_after)
            except tk.TclError:
                pass
            self._search_after = None
        try:
            self.progressbar.stop()  # an animated progress bar must not outlive its window
        except tk.TclError:
            pass
        super().destroy()

    def _alive(self) -> bool:
        """False once the view has been destroyed (late results from worker threads must then be dropped)."""
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    # ------------------------------------------------------------------------ settings
    def set_threshold(self, megabytes: int) -> None:
        self._threshold_mb = max(int(megabytes), 1)
        self.threshold_var.set(str(self._threshold_mb))
        self._render_large_files()

    @property
    def threshold_mb(self) -> int:
        return self._threshold_mb

    @property
    def view_mode(self) -> str:
        return self._view_mode

    def set_view_mode(self, mode: str) -> None:
        if mode not in ("treemap", "list") or mode == self._view_mode:
            return
        self._view_mode = mode
        if mode == "treemap":
            self._results_frame.grid_remove()
            self.treemap_frame.grid()
        else:
            self.treemap_frame.grid_remove()
            self._results_frame.grid()
        self._update_view_toggle()

    def _update_view_toggle(self) -> None:
        self.treemap_toggle.set_toggled(self._view_mode == "treemap")
        self.list_toggle.set_toggled(self._view_mode == "list")

    def _apply_tree_visibility(self) -> None:
        if self.tree_visible.get():
            self._left.grid()
        else:
            self._left.grid_remove()

    def _apply_threshold_entry(self) -> None:
        try:
            value = int(self.threshold_var.get().strip())
            if value < 1:
                raise ValueError
        except ValueError:
            self.threshold_var.set(str(self._threshold_mb))  # invalid input: revert quietly
            return
        if value != self._threshold_mb:
            self.set_threshold(value)

    # ------------------------------------------------------------------------ scanning
    def _choose_folder(self) -> None:
        if self._scanning:
            return
        folder = filedialog.askdirectory(title="Select a folder to analyze", mustexist=True)
        if folder:
            self.scan_folder(folder)

    def _scan_downloads(self) -> None:
        if self._scanning:
            return
        downloads = Path.home() / "Downloads"
        if downloads.is_dir():
            self.scan_folder(str(downloads))
        else:
            self.progress_label.configure(text="Downloads folder was not found. Use Select Folder instead.")

    def _scan_system_drive(self) -> None:
        if not self._scanning:
            self.scan_folder(platform_ops.system_drive_path())

    def scan_folder(self, folder: str) -> None:
        """Start scanning ``folder`` on a background thread (a scan already running is cancelled first)."""
        folder = str(Path(folder))
        if self._cancel is not None:
            self._cancel.set()  # never leave an older scan running behind the new one
        self._scan_token += 1
        token = self._scan_token
        self._cancel = threading.Event()
        cancel = self._cancel
        self._elevated = self._is_elevated()
        self._set_scanning(True)
        self.banner.grid_forget()
        self.folder_label.configure(text=folder, fg=COLORS["text"])
        self.elevation_label.configure(
            text={True: "Running as administrator", False: "Standard user: protected folders may be unreadable"}.get(
                self._elevated, ""
            )
        )
        for label in self._stat_labels.values():
            label.configure(text="—")
        self.progress_label.configure(text="Scanning...", fg=COLORS["accent"])
        self.progressbar.pack(fill="x", pady=(6, 0))
        self.progressbar.start(14)
        self.dirs_list.set_items([])
        self.dirs_message.configure(text="Scanning...")
        self._clear_files()
        self._large_rows = []
        self.files_message.configure(text="Scanning...")
        self.files_message.place(relx=0.5, rely=0.55, anchor="center")
        self.compare_button.set_enabled(False)
        self._clear_search(refresh=False)
        self._zoom_stack = []
        self._selected_node = None
        self.tree_panel.set_root(None)
        self.detail_panel.clear()
        self.breadcrumb.set_path([])
        self.treemap_canvas.clear("Scanning...")
        self._update_back_button()

        scanner = TreeScanner(folder, cancel_event=cancel, status=lambda status: self._runner.post(self._on_status, token, status))
        self._runner.submit(
            scanner.scan,
            on_done=lambda result: self._on_done(token, result),
            on_error=lambda exc: self._on_error(token),
        )

    def _stop_scan(self) -> None:
        if self._cancel is not None and self._scanning:
            self._cancel.set()
            self.progress_label.configure(text="Stopping...")

    def cancel_running_scan(self) -> None:
        """Called when the window closes."""
        if self._cancel is not None:
            self._cancel.set()

    def _set_scanning(self, scanning: bool) -> None:
        self._scanning = scanning
        self.select_button.set_enabled(not scanning)
        self.downloads_button.set_enabled(not scanning)
        self.system_button.set_enabled(not scanning)
        self.stop_button.set_enabled(scanning)
        if not scanning:
            self.progressbar.stop()
            self.progressbar.pack_forget()

    def _on_status(self, token: int, status: ScanStatus) -> None:
        if token != self._scan_token or not self._scanning or not self._alive():
            return
        self.progress_label.configure(
            text=f"Scanning  {shorten_middle(status.path, 52)}"
        )
        self._stat_labels["Total size"].configure(text=format_bytes(status.bytes))
        self._stat_labels["Files"].configure(text=format_count(status.files))
        self._stat_labels["Directories"].configure(text=format_count(status.dirs))

    def _on_error(self, token: int) -> None:
        if token != self._scan_token or not self._alive():
            return
        self._set_scanning(False)
        self.progress_label.configure(text="The scan could not be completed.")
        self.dirs_message.configure(text="The scan could not be completed.")
        self.files_message.configure(text="The scan could not be completed.")
        self.treemap_canvas.clear("The scan could not be completed.")

    def _on_done(self, token: int, result: ScanResult) -> None:
        if token != self._scan_token or not self._alive():
            return
        self._set_scanning(False)
        self.last_result = result

        if result.error:
            self.progress_label.configure(text=result.error, fg=COLORS["danger"])
            self.dirs_message.configure(text="Nothing to show.")
            self.files_message.configure(text="Nothing to show.")
            self.treemap_canvas.clear("Nothing to show.")
            return
        if isinstance(result, TreeScanResult):
            result.elevated = self._elevated

        self.progress_label.configure(fg=COLORS["accent"])
        self._stat_labels["Total size"].configure(text=format_bytes(result.total_size))
        self._stat_labels["Files"].configure(text=format_count(result.file_count))
        self._stat_labels["Directories"].configure(text=format_count(result.dir_count))
        status = f"Finished in {result.elapsed_seconds:.1f}s"
        if result.cancelled:
            status = "Scan stopped early. Totals are partial."
        elif isinstance(result, TreeScanResult) and result.tree_truncated:
            status += "  ·  Very large folder: some small files are grouped in the treemap; List mode is complete."
        if isinstance(result, TreeScanResult) and self._history is not None and self._history.save_storage_scan(result, self._elevated):
            status += "  ·  Saved for later comparison"
            self._refresh_compare_button(token, result.root)
        self.progress_label.configure(text=status)
        self._show_notes(result)

        shown = result.largest_dirs[:LARGEST_DIRS_SHOWN]
        if shown:
            self.dirs_message.pack_forget()
            self.dirs_list.set_items([(name, size, format_bytes(size)) for name, _path, size in shown])
        else:
            self.dirs_list.set_items([])
            self.dirs_message.pack(fill="x", pady=(8, 0))
            self.dirs_message.configure(text="This folder has no sub-folders or files that could be read.")
        self._build_large_rows(result)
        self._render_large_files()

        tree = getattr(result, "tree", None)
        self.tree_panel.set_root(tree)
        self._zoom_stack = [tree] if tree is not None else []
        self._render_treemap()
        self._select_viewed_folder()

    def _show_notes(self, result: ScanResult) -> None:
        if not isinstance(result, TreeScanResult):
            return
        warnings = [text for kind, text in scan_notes(result) if kind == "warn"]
        if warnings:
            self.banner_text.configure(text="\n".join(warnings))
            self.banner.grid(row=2, column=0, sticky="ew", pady=(10, 0))

    def show_scan_details(self) -> Optional[tk.Toplevel]:
        """A window listing what could not be read or followed, and how sizes are measured."""
        if not isinstance(self.last_result, TreeScanResult):
            return None
        self.last_dialog = show_text_dialog(self.winfo_toplevel(), "Scan details", scan_details_text(self.last_result), 700, 540)
        return self.last_dialog

    # ------------------------------------------------------------------------ history: compare
    def _refresh_compare_button(self, token: int, root: str) -> None:
        history = self._history
        if history is None:
            return
        self._runner.submit(
            lambda: history.storage_snapshots(root),
            on_done=lambda snapshots: self._set_compare_available(token, len(snapshots) >= 2),
        )

    def _set_compare_available(self, token: int, available: bool) -> None:
        if token == self._scan_token and self._alive():
            self.compare_button.set_enabled(available)

    def _compare_with_previous(self) -> None:
        history, result = self._history, self.last_result
        if history is None or result is None or not self.compare_button._enabled:
            return
        self.progress_label.configure(text="Comparing with the previous scan...")
        self._runner.submit(lambda: history.compare_latest(result.root), on_done=self._show_comparison)

    def _show_comparison(self, comparison: Optional[storage_history.StorageComparison]) -> Optional[tk.Toplevel]:
        if not self._alive():
            return None
        if comparison is None:
            self.progress_label.configure(text="There is no earlier scan of this folder to compare with.")
            return None
        self.progress_label.configure(text="")
        self.last_dialog = show_text_dialog(
            self.winfo_toplevel(), "Storage changes", storage_history.describe(comparison), 760, 560
        )
        return self.last_dialog

    # ------------------------------------------------------------------------ large files
    def _clear_files(self) -> None:
        for iid in self.files_tree.get_children():
            self.files_tree.delete(iid)
        self.files_message.place_forget()

    def _build_large_rows(self, result: ScanResult) -> None:
        rows = []
        for name, path, size in result.large_files:
            try:
                modified = os.stat(path).st_mtime
            except OSError:
                modified = None
            rows.append({"file": name, "category": category_for(path, False), "size": size, "modified": modified, "path": path})
        self._large_rows = rows

    def _sort_files_by(self, column: str) -> None:
        key, descending = self._files_sort
        self._files_sort = (column, not descending if column == key else column in ("size", "modified"))
        self._update_file_headings()
        self._render_large_files()

    def _update_file_headings(self) -> None:
        key, descending = self._files_sort
        for column_id, heading, _width, _anchor in _FILE_COLUMNS:
            arrow = ("  ▼" if descending else "  ▲") if column_id == key else ""
            self.files_tree.heading(column_id, text=heading + arrow)

    def _render_large_files(self) -> None:
        result = self.last_result
        if result is None or result.error or self._scanning:
            return
        self._clear_files()
        limit = self._threshold_mb * 1024 * 1024
        big = [r for r in self._large_rows if r["size"] >= limit and (self._category_filter in (None, r["category"]))]
        key, descending = self._files_sort
        big.sort(key=lambda r: (r[key] is None, r[key] if r[key] is not None else 0) if key == "modified" else r[key], reverse=descending)
        for index, row in enumerate(big):
            stamp = "—" if row["modified"] is None else _stamp(row["modified"])
            self.files_tree.insert(
                "", "end", iid=str(index),
                values=(row["file"], row["category"], format_bytes(row["size"]), stamp, row["path"]),
            )
        if big:
            note = f"{len(big)} file(s) ≥ {self._threshold_mb} MB"
            if self._category_filter:
                note += f" · {self._category_filter}"
            if len(result.large_files) >= MAX_LARGE_FILES_KEPT and result.large_files[-1][2] >= limit:
                note += f"  ·  showing the {MAX_LARGE_FILES_KEPT} largest"
            self.files_note.configure(text=note)
        else:
            self.files_note.configure(text="")
            self.files_message.configure(text="No files above the selected size threshold were found.")
            self.files_message.place(relx=0.5, rely=0.55, anchor="center")

    # ------------------------------------------------------------------------ treemap + selection
    def _root_node(self) -> Optional[TreeNode]:
        return self._zoom_stack[0] if self._zoom_stack else getattr(self.last_result, "tree", None)

    def _render_treemap(self) -> None:
        if not self._zoom_stack:
            self.breadcrumb.set_path([])
            self.treemap_canvas.clear("Nothing to show.")
        else:
            self.breadcrumb.set_path(self._zoom_stack)
            self.treemap_canvas.show(self._zoom_stack[-1])
        self._update_back_button()

    def _update_back_button(self) -> None:
        self.back_button.set_enabled(len(self._zoom_stack) > 1)

    def _select_viewed_folder(self) -> None:
        """After moving up or down, the folder being viewed becomes the selection in all three places."""
        if not self._zoom_stack:
            self._selected_node = None
            self.detail_panel.clear("Nothing selected.")
            self.tree_panel.clear_selection()
            return
        node = self._zoom_stack[-1]
        self._selected_node = node
        self.detail_panel.show_node(node)
        self.tree_panel.select_node(node)

    def go_back(self) -> bool:
        """Up one folder. False if already at the top."""
        if len(self._zoom_stack) <= 1:
            return False
        self._zoom_stack.pop()
        self._render_treemap()
        self._select_viewed_folder()
        return True

    def _on_escape(self) -> None:
        if not self.winfo_ismapped():
            return
        if self._scanning:
            self._stop_scan()
        elif self.search_var.get():
            self.search_var.set("")
        elif self._category_filter is not None:
            self.legend.set_active(None)
        else:
            self.go_back()

    def _on_treemap_select(self, node: TreeNode) -> None:
        self._selected_node = node
        self.detail_panel.show_node(node)
        self.tree_panel.select_node(node)

    def _on_treemap_zoom(self, node: TreeNode) -> None:
        self._zoom_stack.append(node)
        self._render_treemap()
        self._select_viewed_folder()  # no stale selection: the details follow the folder now shown

    def _on_breadcrumb_navigate(self, index: int) -> None:
        del self._zoom_stack[index + 1 :]
        self._render_treemap()
        self._select_viewed_folder()

    def _on_tree_select(self, node: TreeNode) -> None:
        self._selected_node = node
        self.detail_panel.show_node(node)
        self._show_in_treemap(node)

    def _on_tree_activate(self, node: TreeNode) -> None:
        """Double-click a folder in the tree: open it in the treemap."""
        root = self._root_node()
        chain = find_chain(root, node.path) if root is not None else []
        if chain:
            self._zoom_stack = chain
            self._render_treemap()
            self._select_viewed_folder()

    def _show_in_treemap(self, node: TreeNode) -> None:
        """Make the treemap show the level that contains ``node`` and highlight it."""
        root = self._root_node()
        chain = find_chain(root, node.path) if root is not None and node.path else []
        if not chain:
            return
        level = chain[:-1] or chain[:1]
        if self._zoom_stack != level:
            self._zoom_stack = list(level)
            self._render_treemap()
        self.treemap_canvas.set_selected(node)

    def _on_category_filter(self, category: Optional[str]) -> None:
        self._category_filter = category
        self.treemap_canvas.set_category_filter(category)
        self._render_large_files()
        if self.search_var.get().strip():
            self._run_search()

    def reveal_selected_in_explorer(self) -> bool:
        """Open Windows Explorer with the currently selected item highlighted."""
        if self._selected_node is None or not self._selected_node.path:
            return False
        return windows_backend.reveal_in_explorer(self._selected_node.path)

    # ------------------------------------------------------------------------ search
    def _schedule_search(self) -> None:
        if self._search_after is not None:
            self.after_cancel(self._search_after)
            self._search_after = None
        if not self.search_var.get().strip():
            self._clear_search(reset_text=False)  # emptying the box restores the folder tree at once, no delay
            return
        self._search_after = self.after(SEARCH_DELAY_MS, self._run_search)

    def _run_search(self) -> None:
        self._search_after = None
        needle = self.search_var.get().strip()
        root = self._root_node()
        if not needle:
            self._clear_search()
            return
        if root is None:
            self.search_header.configure(text="Scan a folder first, then search it.")
            self.search_card.tkraise()
            return
        self._search_token += 1
        token, category = self._search_token, self._category_filter
        self._runner.submit(
            lambda: search_tree(root, needle, SEARCH_LIMIT, category),
            on_done=lambda found: self._show_search(token, needle, found),
        )

    def _show_search(self, token: int, needle: str, found: tuple[list[TreeNode], bool]) -> None:
        if token != self._search_token or not self._alive():
            return
        nodes, more = found
        self._search_results = nodes
        for iid in self.search_tree.get_children():
            self.search_tree.delete(iid)
        for index, node in enumerate(nodes):
            parent = os.path.basename(os.path.dirname(node.path))
            self.search_tree.insert("", "end", iid=str(index), values=(node.name, format_bytes(node.size), parent))
        header = f"{len(nodes):,} match{'es' if len(nodes) != 1 else ''} for “{needle}”"
        if more:
            header += f" (the largest {SEARCH_LIMIT} are shown)"
        if self._category_filter:
            header += f" · {self._category_filter} files only"
        result = self.last_result
        if isinstance(result, TreeScanResult) and result.folded_files:
            header += "\nVery small files grouped in huge folders are not searchable by name."
        self.search_header.configure(text=header)
        self.search_card.tkraise()

    def _clear_search(self, refresh: bool = True, reset_text: bool = True) -> None:
        if self._search_after is not None:
            self.after_cancel(self._search_after)
            self._search_after = None
        self._search_token += 1  # drop any search still running
        self._search_results = []
        for iid in self.search_tree.get_children():
            self.search_tree.delete(iid)
        if reset_text and self.search_var.get():
            self.search_var.set("")  # its change notification comes back here with nothing left to clear
        if refresh:
            self.tree_card.tkraise()

    def _on_search_select(self, _event: object) -> None:
        selection = self.search_tree.selection()
        if not selection:
            return
        node = self._search_results[int(selection[0])]
        self._selected_node = node
        self.detail_panel.show_node(node)
        self._show_in_treemap(node)
        self.tree_panel.select_node(node)  # "reveal in tree": the folder tree opens up to it


def _stamp(seconds: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M")
