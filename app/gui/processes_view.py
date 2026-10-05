"""View 2: Process Analyzer."""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from app.analysis.grouping import ProcessGroup, group_processes
from app.core.process_manager import ProcessInfo, ProcessManager
from app.core.sampler import Snapshot
from app.gui.components import Card, FlatButton, font, make_table, section_title
from app.utils.background import BackgroundRunner
from app.utils.constants import COLORS, TOP_PROCESSES_SHOWN, UNAVAILABLE
from app.utils.formatting import format_bytes, format_count, format_percent

_COLUMNS = [
    ("name", "Process", 190, "w"),
    ("pid", "PID", 62, "e"),
    ("cpu", "CPU", 62, "e"),
    ("memory", "Memory", 85, "e"),
    ("status", "Status", 80, "w"),
    ("ppid", "Parent PID", 85, "e"),
]
_HEADINGS = {col[0]: col[1] for col in _COLUMNS}

_SORT_KEYS: dict[str, Callable[[ProcessInfo], object]] = {
    "name": lambda p: p.name.lower(),
    "pid": lambda p: p.pid,
    "cpu": lambda p: p.cpu_percent,
    "memory": lambda p: p.memory_bytes if p.memory_bytes is not None else -1,
    "status": lambda p: p.status,
    "ppid": lambda p: p.ppid if p.ppid is not None else -1,
}

# Sorting of group rows. Columns that only make sense for a single process (PID, status, parent)
# fall back to the memory total, so the header still does something sensible in grouped mode.
_GROUP_SORT_KEYS: dict[str, Callable[[ProcessGroup], object]] = {
    "name": lambda g: g.display.lower(),
    "cpu": lambda g: g.cpu_percent,
    "memory": lambda g: g.memory_bytes,
}
_GROUP_PREFIX = "g:"  # tree row ids of groups; process rows use the plain PID


class ProcessesView(tk.Frame):
    """Process table (grouped by program or one row per process) with details and top-5 panels."""

    def __init__(self, parent: tk.Misc, runner: BackgroundRunner, on_refresh: Callable[[], None]) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self._runner = runner
        self._processes: list[ProcessInfo] = []
        self._by_pid: dict[int, ProcessInfo] = {}
        self._groups_by_key: dict[str, ProcessGroup] = {}  # groups of the rows currently shown
        self._selected_group: Optional[str] = None
        self._rendered_grouped: Optional[bool] = None
        self._restricted = 0
        self._sort_column = "memory"
        self._sort_descending = True
        self._selected_pid: Optional[int] = None
        self._detail_labels: dict[str, tk.Label] = {}
        self._detail_token = 0
        self._detail_row = 0
        self._loaded = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # -- toolbar ---------------------------------------------------------------
        toolbar = tk.Frame(self, bg=COLORS["bg"])
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        tk.Label(toolbar, text="Search", bg=COLORS["bg"], fg=COLORS["text_dim"], font=font(10)).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render())
        search = tk.Entry(
            toolbar,
            textvariable=self.search_var,
            bg=COLORS["bg_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            font=font(11),
            width=28,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        search.pack(side="left", padx=(10, 0), ipady=5)
        self.count_label = tk.Label(toolbar, text="", bg=COLORS["bg"], fg=COLORS["text_dim"], font=font(10))
        self.count_label.pack(side="left", padx=16)
        FlatButton(toolbar, "Refresh", on_refresh).pack(side="right")
        self.group_var = tk.BooleanVar(value=True)
        self.group_var.trace_add("write", lambda *_: self._on_group_toggle())
        tk.Checkbutton(
            toolbar,
            text="Group by program",
            variable=self.group_var,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            selectcolor=COLORS["bg_alt"],
            activebackground=COLORS["bg"],
            activeforeground=COLORS["text"],
            font=font(10),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        ).pack(side="right", padx=(0, 16))

        # -- table -----------------------------------------------------------------
        table_card = Card(self, padding=0)
        table_card.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        frame, self.tree = make_table(table_card.body, _COLUMNS, expandable=True)
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        for column_id in _HEADINGS:
            self.tree.heading(column_id, command=lambda c=column_id: self._sort_by(c))
        self._update_headings()

        self.empty_label = tk.Label(
            table_card.body, text="Loading system information...", bg=COLORS["card"], fg=COLORS["text_dim"], font=font(11)
        )
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

        # -- top-5 cards (under the table) ------------------------------------------
        tops = tk.Frame(self, bg=COLORS["bg"])
        tops.grid(row=2, column=0, sticky="ew", padx=(0, 12), pady=(12, 0))
        tops.columnconfigure(0, weight=1, uniform="top")
        tops.columnconfigure(1, weight=1, uniform="top")

        self.top_cpu_card = Card(tops, padding=14)
        self.top_cpu_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.top_cpu_title = section_title(self.top_cpu_card.body, "Top CPU programs")
        self.top_cpu_title.pack(fill="x")
        self.top_cpu_list = tk.Frame(self.top_cpu_card.body, bg=COLORS["card"])
        self.top_cpu_list.pack(fill="x", pady=(6, 0))

        self.top_mem_card = Card(tops, padding=14)
        self.top_mem_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.top_mem_title = section_title(self.top_mem_card.body, "Top memory programs")
        self.top_mem_title.pack(fill="x")
        self.top_mem_list = tk.Frame(self.top_mem_card.body, bg=COLORS["card"])
        self.top_mem_list.pack(fill="x", pady=(6, 0))

        # -- details panel (full-height right column) --------------------------------
        side = tk.Frame(self, bg=COLORS["bg"], width=310)
        side.grid(row=1, column=1, rowspan=2, sticky="ns")
        side.grid_propagate(False)
        side.rowconfigure(0, weight=1)
        side.columnconfigure(0, weight=1)

        self.details_card = Card(side)
        self.details_card.grid(row=0, column=0, sticky="nsew")
        section_title(self.details_card.body, "Process details").pack(fill="x")
        self.details_holder = tk.Frame(self.details_card.body, bg=COLORS["card"])
        self.details_holder.pack(fill="both", expand=True, pady=(8, 0))
        self._show_details_message("Select a process to see its details.")

    # ------------------------------------------------------------------------ updates
    def update_snapshot(self, snap: Snapshot) -> None:
        self._loaded = True
        self._processes = snap.processes
        self._by_pid = {p.pid: p for p in snap.processes}
        self._restricted = snap.restricted_processes
        self._render()
        self._render_top_lists()
        self._refresh_selected_details()

    def _on_group_toggle(self) -> None:
        """Switch between grouped and one-row-per-process; the old selection no longer applies."""
        self._selected_pid = None
        self._selected_group = None
        self._detail_token += 1
        self._show_details_message("Select a process to see its details.")
        self._render()
        self._render_top_lists()

    # ------------------------------------------------------------------------ table
    def _sort_by(self, column: str) -> None:
        if column == self._sort_column:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_column = column
            self._sort_descending = column in ("cpu", "memory")  # numbers: biggest first
        self._update_headings()
        self._render()

    def _update_headings(self) -> None:
        for column_id, heading in _HEADINGS.items():
            arrow = ""
            if column_id == self._sort_column:
                arrow = "  ▼" if self._sort_descending else "  ▲"
            self.tree.heading(column_id, text=heading + arrow)

    def _visible_rows(self) -> list[ProcessInfo]:
        needle = self.search_var.get().strip().lower()
        rows = self._processes
        if needle:
            rows = [p for p in rows if needle in p.name.lower() or needle in str(p.pid)]
        return sorted(rows, key=_SORT_KEYS[self._sort_column], reverse=self._sort_descending)

    @staticmethod
    def _row_values(proc: ProcessInfo) -> tuple:
        return (
            proc.name,
            proc.pid,
            format_percent(proc.cpu_percent),
            format_bytes(proc.memory_bytes) if proc.memory_bytes is not None else "—",
            proc.status,
            proc.ppid if proc.ppid is not None else "—",
        )

    def _render(self) -> None:
        """Update the table in place (keeps selection, scroll position and expanded groups)."""
        grouped = self.group_var.get()
        if grouped != self._rendered_grouped:
            # the row structure differs between the two modes, so start from an empty table
            children = self.tree.get_children()
            if children:
                self.tree.delete(*children)
            self.tree.configure(show="tree headings" if grouped else "headings")
            self._rendered_grouped = grouped

        rows = self._visible_rows()
        if grouped:
            self._render_grouped(rows)
        else:
            self._groups_by_key = {}
            self._render_flat(rows)

        total = len(self._processes)
        searching = bool(self.search_var.get().strip())
        if grouped:
            text = f"{format_count(len(self._groups_by_key))} programs  ·  {format_count(len(rows))} processes"
            if searching:
                text = f"{text} (of {format_count(total)})"
        else:
            text = f"{format_count(len(rows))} of {format_count(total)} processes" if searching else (
                f"{format_count(total)} processes"
            )
        if self._restricted:
            text += f"  ·  {format_count(self._restricted)} restricted by Windows (memory not readable)"
        self.count_label.configure(text=text if self._loaded else "")

        if not self._loaded:
            self.empty_label.configure(text="Loading system information...")
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
        elif not rows:
            self.empty_label.configure(text="No matching processes found.")
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.empty_label.place_forget()

    def _render_flat(self, rows: list[ProcessInfo]) -> None:
        wanted = {str(p.pid) for p in rows}
        existing = set(self.tree.get_children())

        for iid in existing - wanted:
            self.tree.delete(iid)
        for index, proc in enumerate(rows):
            iid = str(proc.pid)
            if iid in existing:
                self.tree.item(iid, values=self._row_values(proc))
                self.tree.move(iid, "", index)
            else:
                self.tree.insert("", index, iid=iid, values=self._row_values(proc))

    def _render_grouped(self, rows: list[ProcessInfo]) -> None:
        """One expandable row per program; its processes are the children."""
        groups = group_processes(rows)
        sort_key = _GROUP_SORT_KEYS.get(self._sort_column, _GROUP_SORT_KEYS["memory"])
        groups.sort(key=sort_key, reverse=self._sort_descending)
        self._groups_by_key = {g.key: g for g in groups}

        members: dict[str, list[ProcessInfo]] = {}
        for proc in rows:  # rows are already sorted for the current column
            members.setdefault(proc.name.lower(), []).append(proc)

        wanted = {_GROUP_PREFIX + g.key for g in groups}
        for iid in set(self.tree.get_children()) - wanted:
            self.tree.delete(iid)

        for index, group in enumerate(groups):
            gid = _GROUP_PREFIX + group.key
            if self.tree.exists(gid):
                self.tree.item(gid, values=self._group_values(group))
                self.tree.move(gid, "", index)
            else:
                self.tree.insert("", index, iid=gid, values=self._group_values(group), open=False)

            children = members.get(group.key, [])
            wanted_children = {str(p.pid) for p in children}
            for iid in set(self.tree.get_children(gid)) - wanted_children:
                self.tree.delete(iid)
            for position, proc in enumerate(children):
                iid = str(proc.pid)
                values = self._child_values(proc)
                if self.tree.exists(iid):
                    self.tree.item(iid, values=values)
                    self.tree.move(iid, gid, position)
                else:
                    self.tree.insert(gid, position, iid=iid, values=values)

    @staticmethod
    def _group_values(group: ProcessGroup) -> tuple:
        name = group.display if group.count == 1 else f"{group.display}  ({group.count})"
        return (
            name,
            "",
            format_percent(group.cpu_percent),
            format_bytes(group.memory_bytes) if group.memory_bytes or not group.restricted_pids else "—",
            "",
            "",
        )

    @classmethod
    def _child_values(cls, proc: ProcessInfo) -> tuple:
        values = list(cls._row_values(proc))
        values[0] = "↳ " + proc.name  # visibly belongs to the group row above it
        return tuple(values)

    def _render_top_lists(self) -> None:
        if self.group_var.get():
            self.top_cpu_title.configure(text="TOP CPU PROGRAMS")
            self.top_mem_title.configure(text="TOP MEMORY PROGRAMS")
            groups = group_processes(self._processes)
            top_cpu = sorted(groups, key=lambda g: g.cpu_percent, reverse=True)[:TOP_PROCESSES_SHOWN]
            top_mem = sorted(groups, key=lambda g: g.memory_bytes, reverse=True)[:TOP_PROCESSES_SHOWN]
            self._fill_top(self.top_cpu_list, [(self._group_label(g), format_percent(g.cpu_percent)) for g in top_cpu])
            self._fill_top(self.top_mem_list, [(self._group_label(g), format_bytes(g.memory_bytes)) for g in top_mem])
            return
        self.top_cpu_title.configure(text="TOP CPU PROCESSES")
        self.top_mem_title.configure(text="TOP MEMORY PROCESSES")
        procs = self._processes
        top_cpu_p = sorted(procs, key=lambda p: p.cpu_percent, reverse=True)[:TOP_PROCESSES_SHOWN]
        top_mem_p = sorted(
            (p for p in procs if p.memory_bytes is not None), key=lambda p: p.memory_bytes or 0, reverse=True
        )[:TOP_PROCESSES_SHOWN]
        self._fill_top(self.top_cpu_list, [(p.name, format_percent(p.cpu_percent)) for p in top_cpu_p])
        self._fill_top(self.top_mem_list, [(p.name, format_bytes(p.memory_bytes or 0)) for p in top_mem_p])

    @staticmethod
    def _group_label(group: ProcessGroup) -> str:
        return group.display if group.count == 1 else f"{group.display} ×{group.count}"

    @staticmethod
    def _fill_top(holder: tk.Frame, rows: list[tuple[str, str]]) -> None:
        for child in holder.winfo_children():
            child.destroy()
        holder.columnconfigure(0, weight=1)
        for index, (name, value) in enumerate(rows):
            tk.Label(holder, text=name, bg=COLORS["card"], fg=COLORS["text"], font=font(10), anchor="w").grid(
                row=index, column=0, sticky="w", pady=2
            )
            tk.Label(holder, text=value, bg=COLORS["card"], fg=COLORS["text_dim"], font=font(10), anchor="e").grid(
                row=index, column=1, sticky="e", padx=(12, 0)
            )

    # ------------------------------------------------------------------------ details
    def _on_select(self, _event: object) -> None:
        selection = self.tree.selection()
        if not selection:
            return  # a refresh removed the row; handled in _refresh_selected_details
        if selection[0].startswith(_GROUP_PREFIX):
            self._select_group(selection[0][len(_GROUP_PREFIX):])
            return
        pid = int(selection[0])
        if pid == self._selected_pid:
            return
        self._selected_group = None
        self._selected_pid = pid
        proc = self._by_pid.get(pid)
        if proc is None:
            return
        self._detail_token += 1
        token = self._detail_token
        self._show_fields(ProcessManager.basic_fields(proc))
        # user-name lookup and tasklist can be slow, so fetch them off the GUI thread
        self._runner.submit(
            lambda: ProcessManager.get_details(pid),
            on_done=lambda extra: self._add_extra_fields(token, extra),
        )

    def _add_extra_fields(self, token: int, extra: list[tuple[str, str]]) -> None:
        if token != self._detail_token:
            return  # the user already selected something else
        if extra and extra[0][0] == "Status" and "no longer running" in extra[0][1]:
            self._show_details_message("This process is no longer running.")
            return
        self._append_fields(extra)

    def _refresh_selected_details(self) -> None:
        """Keep CPU / Memory / Status of the selected process (or the selected group's totals) live."""
        if self._selected_group is not None:
            group = self._groups_by_key.get(self._selected_group)
            if group is None:
                self._selected_group = None
                self._show_details_message("This program is no longer running.")
            else:
                self._append_fields(self._group_fields(group))  # updates the existing labels in place
            return
        if self._selected_pid is None:
            return
        proc = self._by_pid.get(self._selected_pid)
        if proc is None:
            self._selected_pid = None
            self._detail_token += 1
            self._show_details_message("This process is no longer running.")
            return
        for label, value in ProcessManager.basic_fields(proc):
            widget = self._detail_labels.get(label)
            if widget is not None and label in ("Memory", "CPU", "Status"):
                widget.configure(text=value)

    def _select_group(self, key: str) -> None:
        group = self._groups_by_key.get(key)
        if group is None:
            return
        self._selected_pid = None
        self._selected_group = key
        self._detail_token += 1  # drop any process-details lookup still running
        self._show_fields(self._group_fields(group))

    @staticmethod
    def _group_fields(group: ProcessGroup) -> list[tuple[str, str]]:
        fields = [
            ("Program", group.display),
            ("File name", group.key),
            ("Processes", format_count(group.count)),
            ("CPU total", format_percent(group.cpu_percent)),
            ("Memory sum", format_bytes(group.memory_bytes)),
        ]
        if group.restricted_pids:
            fields.append(("Unreadable", f"{group.restricted_pids} process(es): Windows denied access to their memory"))
        fields.append(
            (
                "Note",
                "Memory sum adds each process's working set; shared memory may be counted twice.",
            )
        )
        return fields

    def _show_details_message(self, message: str) -> None:
        self._clear_details()
        tk.Label(
            self.details_holder,
            text=message,
            bg=COLORS["card"],
            fg=COLORS["text_dim"],
            font=font(10),
            anchor="w",
            justify="left",
            wraplength=250,
        ).grid(row=0, column=0, columnspan=2, sticky="w")

    def _clear_details(self) -> None:
        self._detail_labels = {}
        self._detail_row = 0
        for child in self.details_holder.winfo_children():
            child.destroy()
        self.details_holder.columnconfigure(1, weight=1)

    def _show_fields(self, fields: list[tuple[str, str]]) -> None:
        self._clear_details()
        self._append_fields(fields)

    @staticmethod
    def _shorten(value: str, limit: int = 96) -> str:
        """Keep the panel compact: long paths lose their middle, other text loses its end."""
        if len(value) <= limit:
            return value
        if "\\" in value or "/" in value:
            head, tail = value[: limit // 3], value[-(limit * 2 // 3):]
            return f"{head}…{tail}"
        return value[: limit - 1] + "…"

    def _append_fields(self, fields: list[tuple[str, str]]) -> None:
        for label, value in fields:
            text = self._shorten(value or UNAVAILABLE)
            if label in self._detail_labels:
                self._detail_labels[label].configure(text=text)
                continue
            row = self._detail_row
            self._detail_row += 1
            tk.Label(
                self.details_holder, text=label, bg=COLORS["card"], fg=COLORS["text_dim"], font=font(9), anchor="nw",
                width=11,
            ).grid(row=row, column=0, sticky="nw", pady=3)
            value_label = tk.Label(
                self.details_holder,
                text=text,
                bg=COLORS["card"],
                fg=COLORS["text"],
                font=font(10),
                anchor="nw",
                justify="left",
                wraplength=165,
            )
            value_label.grid(row=row, column=1, sticky="nw", pady=3)
            self._detail_labels[label] = value_label
