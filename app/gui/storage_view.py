"""View 3: Storage Analyzer (read-only folder analysis)."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Optional

from app.core import windows_backend
from app.core.storage_manager import ScanResult
from app.core.tree_scanner import TreeNode, TreeScanner
from app.gui.components import BarList, Card, FlatButton, font, make_table, section_title
from app.gui.treemap_view import Breadcrumb, DetailPanel, TreemapCanvas
from app.utils.background import BackgroundRunner
from app.utils.constants import (
    ACCESS_DENIED_MESSAGE,
    ACCESS_DENIED_TITLE,
    COLORS,
    LARGE_FILE_THRESHOLD_MB,
    LARGEST_DIRS_SHOWN,
    MAX_LARGE_FILES_KEPT,
)
from app.utils.formatting import format_bytes, format_count

_FILE_COLUMNS = [
    ("file", "File", 170, "w"),
    ("path", "Path", 300, "w"),
    ("size", "Size", 80, "e"),
]


class StorageView(tk.Frame):
    """Choose a folder, scan it on a background thread, show sizes."""

    def __init__(self, parent: tk.Misc, runner: BackgroundRunner) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self._runner = runner
        self._threshold_mb = LARGE_FILE_THRESHOLD_MB
        self._cancel: Optional[threading.Event] = None
        self._scan_token = 0
        self._scanning = False
        self.last_result: Optional[ScanResult] = None
        self._view_mode = "treemap"
        self._zoom_stack: list[TreeNode] = []
        self._selected_node: Optional[TreeNode] = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        # -- toolbar ---------------------------------------------------------------
        toolbar = tk.Frame(self, bg=COLORS["bg"])
        toolbar.grid(row=0, column=0, sticky="ew")
        self.select_button = FlatButton(toolbar, "Select Folder", self._choose_folder, primary=True)
        self.select_button.pack(side="left")
        self.downloads_button = FlatButton(toolbar, "Downloads", self._scan_downloads)
        self.downloads_button.pack(side="left", padx=(8, 0))
        self.stop_button = FlatButton(toolbar, "Stop", self._stop_scan)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.stop_button.set_enabled(False)

        tk.Frame(toolbar, bg=COLORS["border"], width=1).pack(side="left", fill="y", padx=12, pady=4)
        self.treemap_toggle = FlatButton(toolbar, "Treemap", lambda: self.set_view_mode("treemap"))
        self.treemap_toggle.pack(side="left")
        self.list_toggle = FlatButton(toolbar, "List", lambda: self.set_view_mode("list"))
        self.list_toggle.pack(side="left", padx=(8, 0))

        self.threshold_var = tk.StringVar(value=str(self._threshold_mb))
        self.threshold_entry = tk.Entry(
            toolbar,
            textvariable=self.threshold_var,
            width=7,
            justify="center",
            bg=COLORS["bg_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            font=font(11),
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        self.threshold_entry.pack(side="right", ipady=4)
        tk.Label(toolbar, text="Large file threshold (MB)", bg=COLORS["bg"], fg=COLORS["text_dim"], font=font(10)).pack(
            side="right", padx=(0, 10)
        )
        self.threshold_entry.bind("<Return>", lambda _e: self._apply_threshold_entry())
        self.threshold_entry.bind("<FocusOut>", lambda _e: self._apply_threshold_entry())

        # -- summary card ----------------------------------------------------------
        self.summary_card = Card(self)
        self.summary_card.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        section_title(self.summary_card.body, "Storage analysis").pack(fill="x")
        self.folder_label = tk.Label(
            self.summary_card.body,
            text="Select a folder to analyze its size.",
            bg=COLORS["card"],
            fg=COLORS["text_dim"],
            font=font(11),
            anchor="w",
            justify="left",
        )
        self.folder_label.pack(fill="x", pady=(8, 0))
        stats = tk.Frame(self.summary_card.body, bg=COLORS["card"])
        stats.pack(fill="x", pady=(10, 0))
        self._stat_labels: dict[str, tk.Label] = {}
        for column, name in enumerate(("Total size", "Files", "Directories")):
            stats.columnconfigure(column, weight=1, uniform="stat")
            box = tk.Frame(stats, bg=COLORS["card"])
            box.grid(row=0, column=column, sticky="w")
            tk.Label(box, text=name, bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9), anchor="w").pack(fill="x")
            value = tk.Label(box, text="—", bg=COLORS["card"], fg=COLORS["text"], font=font(18, "bold"), anchor="w")
            value.pack(fill="x")
            self._stat_labels[name] = value
        self.progress_label = tk.Label(
            self.summary_card.body, text="", bg=COLORS["card"], fg=COLORS["accent"], font=font(10), anchor="w"
        )
        self.progress_label.pack(fill="x", pady=(8, 0))

        # -- access-denied banner (hidden until needed) -----------------------------
        self.banner = tk.Frame(self, bg=COLORS["card"], highlightthickness=1, highlightbackground=COLORS["warning"])
        tk.Label(
            self.banner, text=ACCESS_DENIED_TITLE, bg=COLORS["card"], fg=COLORS["warning"], font=font(10, "bold"), anchor="w"
        ).pack(fill="x", padx=14, pady=(10, 0))
        self.banner_text = tk.Label(
            self.banner, text=ACCESS_DENIED_MESSAGE, bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10),
            anchor="w", justify="left", wraplength=900,
        )
        self.banner_text.pack(fill="x", padx=14, pady=(2, 10))

        # -- results ---------------------------------------------------------------
        results = tk.Frame(self, bg=COLORS["bg"])
        results.grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        results.columnconfigure(0, weight=2, uniform="res")
        results.columnconfigure(1, weight=3, uniform="res")
        results.rowconfigure(0, weight=1)

        self.dirs_card = Card(results)
        self.dirs_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        section_title(self.dirs_card.body, "Largest directories").pack(fill="x")
        self.dirs_list = BarList(self.dirs_card.body)
        self.dirs_list.pack(fill="x", pady=(8, 0))
        self.dirs_message = tk.Label(
            self.dirs_card.body, text="Nothing scanned yet.", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10),
            anchor="w",
        )
        self.dirs_message.pack(fill="x", pady=(8, 0))

        self.files_card = Card(results, padding=0)
        self.files_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        head = tk.Frame(self.files_card.body, bg=COLORS["card"])
        head.pack(fill="x", padx=16, pady=(16, 8))
        section_title(head, "Large files").pack(side="left")
        self.files_note = tk.Label(head, text="", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9))
        self.files_note.pack(side="right")
        frame, self.files_tree = make_table(self.files_card.body, _FILE_COLUMNS, height=8)
        frame.pack(fill="both", expand=True)
        self.files_message = tk.Label(
            self.files_card.body, text="", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10)
        )

        # -- treemap (default view) -------------------------------------------------
        self.treemap_frame = tk.Frame(self, bg=COLORS["bg"])
        self.treemap_frame.grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        self.treemap_frame.columnconfigure(0, weight=1)
        self.treemap_frame.rowconfigure(1, weight=1)

        self.breadcrumb = Breadcrumb(self.treemap_frame, self._on_breadcrumb_navigate)
        self.breadcrumb.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        treemap_body = tk.Frame(self.treemap_frame, bg=COLORS["bg"])
        treemap_body.grid(row=1, column=0, columnspan=2, sticky="nsew")
        treemap_body.columnconfigure(0, weight=3, uniform="tm")
        treemap_body.columnconfigure(1, weight=1, uniform="tm")
        treemap_body.rowconfigure(0, weight=1)

        canvas_card = Card(treemap_body, padding=0)
        canvas_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.treemap_canvas = TreemapCanvas(canvas_card.body, self._on_treemap_select, self._on_treemap_zoom)
        self.treemap_canvas.pack(fill="both", expand=True)

        self.detail_panel = DetailPanel(treemap_body, self.reveal_selected_in_explorer)
        self.detail_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        self._results_frame = results
        self._results_frame.grid_remove()
        self._update_view_toggle()

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
        active, inactive = COLORS["card"], COLORS["bg"]
        treemap_active = self._view_mode == "treemap"
        self.treemap_toggle.configure(bg=active if treemap_active else inactive)
        self.treemap_toggle._normal = active if treemap_active else inactive  # keep hover restore correct
        self.list_toggle.configure(bg=active if not treemap_active else inactive)
        self.list_toggle._normal = active if not treemap_active else inactive

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

    def scan_folder(self, folder: str) -> None:
        """Start scanning ``folder`` on a background thread."""
        folder = str(Path(folder))
        self._scan_token += 1
        token = self._scan_token
        self._cancel = threading.Event()
        cancel = self._cancel
        self._set_scanning(True)
        self.banner.grid_forget()
        self.folder_label.configure(text=folder, fg=COLORS["text"])
        for label in self._stat_labels.values():
            label.configure(text="—")
        self.progress_label.configure(text="Scanning...", fg=COLORS["accent"])
        self.dirs_list.set_items([])
        self.dirs_message.configure(text="Scanning...")
        self._clear_files()
        self.files_message.configure(text="Scanning...")
        self.files_message.place(relx=0.5, rely=0.55, anchor="center")
        self._zoom_stack = []
        self._selected_node = None
        self.breadcrumb.set_path([])
        self.treemap_canvas.clear("Scanning...")

        def on_progress(files: int, dirs: int) -> None:
            self._runner.post(self._on_progress, token, files, dirs)

        scanner = TreeScanner(folder, progress=on_progress, cancel_event=cancel)
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
        self.stop_button.set_enabled(scanning)

    def _on_progress(self, token: int, files: int, dirs: int) -> None:
        if token != self._scan_token or not self._scanning:
            return
        self.progress_label.configure(text=f"Scanning...   Analyzed: {format_count(files)} files")
        self._stat_labels["Files"].configure(text=format_count(files))
        self._stat_labels["Directories"].configure(text=format_count(dirs))

    def _on_error(self, token: int) -> None:
        if token != self._scan_token:
            return
        self._set_scanning(False)
        self.progress_label.configure(text="The scan could not be completed.")
        self.dirs_message.configure(text="The scan could not be completed.")
        self.files_message.configure(text="The scan could not be completed.")
        self.treemap_canvas.clear("The scan could not be completed.")

    def _on_done(self, token: int, result: ScanResult) -> None:
        if token != self._scan_token:
            return
        self._set_scanning(False)
        self.last_result = result

        if result.error:
            self.progress_label.configure(text=result.error, fg=COLORS["danger"])
            self.dirs_message.configure(text="Nothing to show.")
            self.files_message.configure(text="Nothing to show.")
            self.treemap_canvas.clear("Nothing to show.")
            return

        self.progress_label.configure(fg=COLORS["accent"])
        self._stat_labels["Total size"].configure(text=format_bytes(result.total_size))
        self._stat_labels["Files"].configure(text=format_count(result.file_count))
        self._stat_labels["Directories"].configure(text=format_count(result.dir_count))
        status = f"Finished in {result.elapsed_seconds:.1f}s"
        if result.cancelled:
            status = "Scan stopped early. Totals are partial."
        elif getattr(result, "tree_truncated", False):
            status += "  ·  Very large folder — treemap shows a partial view; List mode is complete."
        self.progress_label.configure(text=status)

        if result.denied_count:
            self.banner.grid(row=2, column=0, sticky="ew", pady=(14, 0))

        shown = result.largest_dirs[:LARGEST_DIRS_SHOWN]
        if shown:
            self.dirs_message.pack_forget()
            self.dirs_list.set_items([(name, size, format_bytes(size)) for name, _path, size in shown])
        else:
            self.dirs_list.set_items([])
            self.dirs_message.pack(fill="x", pady=(8, 0))
            self.dirs_message.configure(text="This folder has no sub-folders or files that could be read.")
        self._render_large_files()

        tree = getattr(result, "tree", None)
        self._zoom_stack = [tree] if tree is not None else []
        self._render_treemap()

    # ------------------------------------------------------------------------ large files
    def _clear_files(self) -> None:
        for iid in self.files_tree.get_children():
            self.files_tree.delete(iid)
        self.files_message.place_forget()

    def _render_large_files(self) -> None:
        result = self.last_result
        if result is None or result.error or self._scanning:
            return
        self._clear_files()
        limit = self._threshold_mb * 1024 * 1024
        big = [f for f in result.large_files if f[2] >= limit]
        for index, (name, path, size) in enumerate(big):
            self.files_tree.insert("", "end", iid=str(index), values=(name, path, format_bytes(size)))
        if big:
            note = f"{len(big)} file(s) ≥ {self._threshold_mb} MB"
            if len(result.large_files) >= MAX_LARGE_FILES_KEPT and result.large_files[-1][2] >= limit:
                note += f"  ·  showing the {MAX_LARGE_FILES_KEPT} largest"
            self.files_note.configure(text=note)
        else:
            self.files_note.configure(text="")
            self.files_message.configure(text="No files above the selected size threshold were found.")
            self.files_message.place(relx=0.5, rely=0.55, anchor="center")

    # ------------------------------------------------------------------------ treemap
    def _render_treemap(self) -> None:
        if not self._zoom_stack:
            self.breadcrumb.set_path([])
            self.treemap_canvas.clear("Nothing to show.")
            return
        self.breadcrumb.set_path(self._zoom_stack)
        self.treemap_canvas.show(self._zoom_stack[-1])

    def _on_treemap_select(self, node: TreeNode) -> None:
        self._selected_node = node
        self.detail_panel.show_node(node)

    def _on_treemap_zoom(self, node: TreeNode) -> None:
        self._zoom_stack.append(node)
        self._render_treemap()

    def _on_breadcrumb_navigate(self, index: int) -> None:
        del self._zoom_stack[index + 1 :]
        self._render_treemap()

    def reveal_selected_in_explorer(self) -> bool:
        """Open Windows Explorer with the currently selected item highlighted."""
        if self._selected_node is None or not self._selected_node.path:
            return False
        return windows_backend.reveal_in_explorer(self._selected_node.path)
