"""View 1: System Health overview."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta
from typing import Optional

from app.collectors import labels
from app.core.sampler import Snapshot
from app.core.system_info import StaticInfo
from app.gui.dialogs import show_evidence_details
from app.gui.components import Card, MetricCard, font, section_title, usage_color
from app.utils.constants import COLORS, LEVEL_COLORS, UNAVAILABLE
from app.utils.formatting import (
    format_bytes,
    format_duration,
    format_percent,
    format_used_of_total,
)


class OverviewView(tk.Frame):
    """Four metric cards, a system-status card, a machine-details card and the evidence sources."""

    def __init__(self, parent: tk.Misc, info: StaticInfo) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self._info = info
        self._findings_key: Optional[tuple] = None
        self.evidence_values: dict[str, tk.Label] = {}  # reading name -> label showing its value or its status
        self._evidence_text: dict[str, str] = {}
        self.privilege_label: Optional[tk.Label] = None

        for column in range(4):
            self.columnconfigure(column, weight=1, uniform="metric")
        self.rowconfigure(1, weight=1, minsize=232)  # the status cards keep room even when the window is short
        self._readings: dict = {}

        self.cpu_card = MetricCard(self, "CPU")
        self.memory_card = MetricCard(self, "Memory")
        self.storage_card = MetricCard(self, "Storage")
        self.uptime_card = MetricCard(self, "Uptime", with_bar=False)
        for column, card in enumerate((self.cpu_card, self.memory_card, self.storage_card, self.uptime_card)):
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0 if column == 3 else 8))

        # -- System status card --------------------------------------------------
        self.status_card = Card(self)
        self.status_card.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=(0, 8), pady=(16, 0))
        section_title(self.status_card.body, "System status").pack(fill="x")
        self.status_holder = tk.Frame(self.status_card.body, bg=COLORS["card"])
        self.status_holder.pack(fill="both", expand=True, pady=(10, 0))
        self._set_status_loading()

        # -- Machine details card ------------------------------------------------
        self.details_card = Card(self)
        self.details_card.grid(row=1, column=2, columnspan=2, sticky="nsew", padx=(8, 0), pady=(16, 0))
        section_title(self.details_card.body, "This computer").pack(fill="x")
        self._build_details()

        # -- Evidence sources ------------------------------------------------------
        self.evidence_card = Card(self)
        self.evidence_card.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        header = tk.Frame(self.evidence_card.body, bg=COLORS["card"])
        header.pack(fill="x")
        section_title(header, "What OScope can measure on this PC").pack(side="left")
        details_link = tk.Label(
            header, text="Details…", bg=COLORS["card"], fg=COLORS["accent"], font=font(9, "bold"), cursor="hand2"
        )
        details_link.pack(side="right")
        details_link.bind("<Button-1>", lambda _e: self.show_evidence_details())
        self.privilege_label = tk.Label(header, text="", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9))
        self.privilege_label.pack(side="right", padx=(0, 14))
        self._build_evidence()

    # -- construction helpers ---------------------------------------------------
    def _build_details(self) -> None:
        info = self._info
        cpus = (
            f"{info.logical_cpus} logical / {info.physical_cpus} physical"
            if info.logical_cpus is not None and info.physical_cpus is not None
            else (f"{info.logical_cpus} logical" if info.logical_cpus is not None else UNAVAILABLE)
        )
        rows = [
            ("Operating System", info.os_name),
            ("OS details", info.os_detail or UNAVAILABLE),
            ("Hostname", info.hostname),
            ("Processor", info.cpu_name or UNAVAILABLE),
            ("CPU cores", cpus),
        ]
        grid = tk.Frame(self.details_card.body, bg=COLORS["card"])
        grid.pack(fill="both", expand=True, pady=(10, 0))
        grid.columnconfigure(1, weight=1)
        for row, (label, value) in enumerate(rows):
            tk.Label(grid, text=label, bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10), anchor="w").grid(
                row=row, column=0, sticky="nw", pady=4, padx=(0, 16)
            )
            value_label = tk.Label(
                grid, text=value, bg=COLORS["card"], fg=COLORS["text"], font=font(10), anchor="w", justify="left"
            )
            value_label.grid(row=row, column=1, sticky="nw", pady=4)
        grid.bind("<Configure>", lambda e, g=grid: self._wrap_details(g, e.width))

    def _build_evidence(self) -> None:
        """One cell per reading: its name, and below it the value or the honest reason there is none."""
        grid = tk.Frame(self.evidence_card.body, bg=COLORS["card"])
        grid.pack(fill="x", pady=(6, 0))
        columns = 4
        for column in range(columns):
            grid.columnconfigure(column, weight=1, uniform="evidence")
        for index, (name, title) in enumerate(labels.EVIDENCE_ROWS):
            cell = tk.Frame(grid, bg=COLORS["card"])
            cell.grid(row=index // columns, column=index % columns, sticky="nw", padx=(0, 12), pady=(0, 6))
            tk.Label(cell, text=title, bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9), anchor="w").pack(fill="x")
            value = tk.Label(
                cell, text="Checking...", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10),
                anchor="w", justify="left", wraplength=220,
            )
            value.pack(fill="x")
            self.evidence_values[name] = value

    def show_evidence_details(self) -> tk.Toplevel:
        """Open the dialog explaining each reading's state, reason and source."""
        return show_evidence_details(self.winfo_toplevel(), self._readings)

    def _render_evidence(self, snap: Snapshot) -> None:
        self._readings = snap.readings
        for name, widget in self.evidence_values.items():
            value, status = labels.describe(snap.readings, name)
            text = value or status  # a gap shows only its state here; the reason is in "Details"
            if self._evidence_text.get(name) == text:
                continue  # unchanged: skip the redraw
            self._evidence_text[name] = text
            widget.configure(text=text, fg=COLORS["text"] if value else COLORS["text_dim"])

    @staticmethod
    def _wrap_details(grid: tk.Frame, width: int) -> None:
        for child in grid.grid_slaves(column=1):
            child.configure(wraplength=max(width - 170, 120))

    def _set_status_loading(self) -> None:
        self._clear(self.status_holder)
        tk.Label(
            self.status_holder,
            text="Loading system information...",
            bg=COLORS["card"],
            fg=COLORS["text_dim"],
            font=font(11),
            anchor="w",
        ).pack(fill="x")

    @staticmethod
    def _clear(frame: tk.Frame) -> None:
        for child in frame.winfo_children():
            child.destroy()

    # -- updates ----------------------------------------------------------------
    def update_snapshot(self, snap: Snapshot) -> None:
        # CPU
        if snap.cpu_percent is not None:
            self.cpu_card.set(
                format_percent(snap.cpu_percent),
                self._cpu_subtitle(),
                snap.cpu_percent,
                usage_color("cpu", snap.cpu_percent),
            )
        else:
            self.cpu_card.set(UNAVAILABLE, " ", 0)

        # Memory
        memory = snap.memory
        if memory is not None:
            self.memory_card.set(
                format_used_of_total(memory.used, memory.total),
                f"{format_percent(memory.percent)} used  ·  {format_bytes(memory.available)} available",
                memory.percent,
                usage_color("memory", memory.percent),
            )
        else:
            self.memory_card.set(UNAVAILABLE, " ", 0)

        # Storage
        storage = snap.storage
        if storage is not None:
            self.storage_card.set(
                format_used_of_total(storage.used, storage.total),
                f"{storage.path}  ·  {format_percent(storage.percent)} used  ·  {format_bytes(storage.free)} free",
                storage.percent,
                usage_color("storage", storage.percent),
            )
        else:
            self.storage_card.set(UNAVAILABLE, " ", 0)

        # Uptime
        if snap.uptime_seconds is not None:
            booted = datetime.now() - timedelta(seconds=snap.uptime_seconds)
            self.uptime_card.set(format_duration(snap.uptime_seconds), f"Since {booted:%Y-%m-%d %H:%M}")
        else:
            self.uptime_card.set(UNAVAILABLE)

        self._render_findings(snap.findings)
        self._render_evidence(snap)
        if self.privilege_label is not None:
            self.privilege_label.configure(
                text={True: "Running as administrator", False: "Standard user: some readings may be restricted"}.get(
                    snap.elevated, ""
                )
            )

    def _cpu_subtitle(self) -> str:
        info = self._info
        if info.logical_cpus is None:
            return " "
        if info.physical_cpus is not None:
            return f"{info.logical_cpus} logical  ·  {info.physical_cpus} physical cores"
        return f"{info.logical_cpus} logical CPUs"

    def _render_findings(self, findings: list[dict]) -> None:
        key = tuple((f["level"], f["title"], f["message"]) for f in findings)
        if key == self._findings_key:
            return  # nothing changed: avoid rebuilding widgets every refresh
        self._findings_key = key
        self._clear(self.status_holder)
        for finding in findings:
            row = tk.Frame(self.status_holder, bg=COLORS["card"])
            row.pack(fill="x", pady=(0, 12))
            tk.Label(row, text="●", bg=COLORS["card"], fg=LEVEL_COLORS[finding["level"]], font=font(12)).pack(
                side="left", anchor="n", padx=(0, 10)
            )
            text = tk.Frame(row, bg=COLORS["card"])
            text.pack(side="left", fill="x", expand=True)
            tk.Label(
                text, text=finding["title"], bg=COLORS["card"], fg=COLORS["text"], font=font(11, "bold"), anchor="w"
            ).pack(fill="x")
            message = tk.Label(
                text,
                text=finding["message"],
                bg=COLORS["card"],
                fg=COLORS["text_dim"],
                font=font(10),
                anchor="w",
                justify="left",
                wraplength=380,
            )
            message.pack(fill="x", pady=(2, 0))
            text.bind("<Configure>", lambda e, m=message: m.configure(wraplength=max(e.width - 4, 120)))
