"""View 0: "Ask OScope" - the question-first front door.

The user picks a question; OScope answers from evidence in four layers (what is happening, what is
contributing, what you may not have noticed, what you could consider) and always lists what it could
not check. Nothing here changes the computer.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Sequence

from app.analysis import explain, workloads
from app.analysis.models import DiagnosticResult, Evidence, EvidenceStrength, Finding, Relation, Section
from app.analysis.orchestrator import run_question
from app.analysis.questions import QUESTION_ORDER, QUESTIONS
from app.collectors import labels
from app.core.sampler import SampleRecord, Snapshot
from app.gui.components import Card, FlatButton, ScrollableFrame, font, section_title
from app.utils.background import BackgroundRunner
from app.utils.constants import COLORS, LEVEL_COLORS

ContextProvider = Callable[[], tuple[Optional[Snapshot], Sequence[SampleRecord]]]

_PLACEHOLDER = "Choose a question above. OScope answers only from what it can measure on this PC."
_WAITING = "OScope is still taking its first measurements. Try again in a moment."
_TAG_COLORS = {
    EvidenceStrength.OBSERVED: COLORS["success"],
    EvidenceStrength.STRONG: COLORS["success"],
    EvidenceStrength.CORRELATION: COLORS["accent"],
    EvidenceStrength.INTERPRETATION: COLORS["accent"],
    EvidenceStrength.UNVERIFIED: COLORS["warning"],
}


class AskView(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        runner: BackgroundRunner,
        get_context: ContextProvider,
        navigate: Callable[[str], None],
        workload: str = workloads.DEFAULT_WORKLOAD,
        on_workload_change: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self._runner = runner
        self._get_context = get_context
        self._navigate = navigate
        self._on_workload_change = on_workload_change
        self._token = 0
        self._wrap_labels: list[tk.Label] = []
        self._last_width = 0
        self.last_result: Optional[DiagnosticResult] = None
        self.current_question: Optional[str] = None
        self.question_buttons: dict[str, FlatButton] = {}
        self.workload_id = workload if workload in workloads.WORKLOADS else workloads.DEFAULT_WORKLOAD

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # -- header: title on the left, workload picker on the right ---------------------------
        header = tk.Frame(self, bg=COLORS["bg"])
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        titles = tk.Frame(header, bg=COLORS["bg"])
        titles.grid(row=0, column=0, sticky="ew")
        tk.Label(titles, text="What would you like to know?", bg=COLORS["bg"], fg=COLORS["text"], font=font(16, "bold"), anchor="w").pack(fill="x")
        subtitle = tk.Label(
            titles,
            text="OScope answers from evidence, says plainly when it cannot measure something, and never changes your computer.",
            bg=COLORS["bg"], fg=COLORS["text_dim"], font=font(10), anchor="w", justify="left", wraplength=500,
        )
        subtitle.pack(fill="x", pady=(2, 0))
        titles.bind("<Configure>", lambda e: subtitle.configure(wraplength=max(e.width - 8, 200)))

        picker = tk.Frame(header, bg=COLORS["bg"])
        picker.grid(row=0, column=1, sticky="ne", padx=(16, 0))
        tk.Label(picker, text="I'm mostly doing", bg=COLORS["bg"], fg=COLORS["text_dim"], font=font(10)).pack(anchor="w")
        self.workload_var = tk.StringVar(value=workloads.label(self.workload_id))
        self.workload_box = ttk.Combobox(
            picker, textvariable=self.workload_var, values=list(workloads.WORKLOADS.values()),
            state="readonly", width=20, style="Oscope.TCombobox",
        )
        self.workload_box.pack(anchor="w", pady=(2, 0))
        self.workload_box.bind("<<ComboboxSelected>>", lambda _e: self._on_workload_selected())

        # -- questions: a uniform grid, the long last one spans two columns ---------------------
        buttons = tk.Frame(self, bg=COLORS["bg"])
        buttons.grid(row=1, column=0, sticky="ew", pady=(14, 6))
        for column in range(3):
            buttons.columnconfigure(column, weight=1, uniform="question")
        for index, question_id in enumerate(QUESTION_ORDER):
            button = FlatButton(
                buttons, QUESTIONS[question_id].text, lambda q=question_id: self.ask(q), primary=question_id == "slow"
            )
            last = index == len(QUESTION_ORDER) - 1
            button.grid(
                row=index // 3, column=index % 3, columnspan=2 if last else 1, sticky="ew",
                padx=(0, 8), pady=(0, 8),
            )
            self.question_buttons[question_id] = button

        # -- results -----------------------------------------------------------------------
        self.scroll = ScrollableFrame(self)
        self.scroll.grid(row=2, column=0, sticky="nsew")
        self.scroll.canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        self._show_message(_PLACEHOLDER)

    # ------------------------------------------------------------------------ asking
    def ask(self, question_id: str) -> None:
        """Answer ``question_id`` from the latest data (computed off the GUI thread)."""
        snapshot, window = self._get_context()
        self.current_question = question_id
        if snapshot is None:
            self._show_message(_WAITING)
            return
        self._token += 1
        token = self._token
        self._show_message("Looking at the evidence...")
        window_copy, workload = list(window), self.workload_id
        self._runner.submit(
            lambda: run_question(question_id, snapshot, window_copy, workload),
            on_done=lambda result: self._on_result(token, result),
            on_error=lambda exc: self._on_error(token),
        )

    def _on_result(self, token: int, result: DiagnosticResult) -> None:
        if token != self._token:
            return  # the user already asked something else
        self.last_result = result
        self._render(result)

    def _on_error(self, token: int) -> None:
        if token == self._token:
            self._show_message("OScope could not put this answer together. Try asking again.")

    def _on_workload_selected(self) -> None:
        chosen = self.workload_var.get()
        self.workload_id = next((key for key, text in workloads.WORKLOADS.items() if text == chosen), workloads.DEFAULT_WORKLOAD)
        if self._on_workload_change is not None:
            self._on_workload_change(self.workload_id)
        if self.current_question is not None and self.last_result is not None:
            self.ask(self.current_question)  # the same question, re-ordered for the new workload

    # ------------------------------------------------------------------------ rendering
    def _clear(self) -> None:
        for child in self.scroll.body.winfo_children():
            child.destroy()
        self._wrap_labels = []
        self.scroll.scroll_to_top()

    def _show_message(self, text: str) -> None:
        self._clear()
        self._label(self.scroll.body, text, dim=True, size=11).pack(fill="x", pady=8)

    def _render(self, result: DiagnosticResult) -> None:
        self._clear()
        body = self.scroll.body

        head = Card(body)
        head.pack(fill="x", pady=(0, 12))
        self._label(head.body, result.question_text, size=14, bold=True).pack(fill="x")
        self._label(
            head.body,
            f"Based on {result.sample_count} samples over {result.window_seconds:.0f} seconds  ·  Workload: {result.workload}",
            dim=True, size=9,
        ).pack(fill="x", pady=(2, 8))
        section_title(head.body, "What we found").pack(fill="x")
        self._label(head.body, result.summary, size=11).pack(fill="x", pady=(4, 0))
        for note in result.notes:
            self._label(head.body, note, dim=True, size=9).pack(fill="x", pady=(6, 0))

        for section in result.sections:
            self._render_section(section)
        for finding in result.findings:
            self._render_finding(finding)
        self._render_relations(result.relations)
        if result.unchecked:
            self._render_unchecked(result)
        self._render_footer(result)
        self._apply_wrap(force=True)

    def _render_section(self, section: Section) -> None:
        card = Card(self.scroll.body)
        card.pack(fill="x", pady=(0, 12))
        section_title(card.body, section.title).pack(fill="x")
        self._evidence_rows(card.body, section.rows)

    def _render_finding(self, finding: Finding) -> None:
        card = Card(self.scroll.body)
        card.pack(fill="x", pady=(0, 12))
        top = tk.Frame(card.body, bg=COLORS["card"])
        top.pack(fill="x")
        tk.Label(top, text="●", bg=COLORS["card"], fg=LEVEL_COLORS[finding.level], font=font(12)).pack(side="left", padx=(0, 8))
        self._label(top, finding.title, size=12, bold=True).pack(side="left", fill="x", expand=True)

        section_title(card.body, "What is happening").pack(fill="x", pady=(10, 0))
        self._label(card.body, finding.happening, size=10).pack(fill="x", pady=(2, 0))
        if finding.contributing:
            section_title(card.body, "What is contributing").pack(fill="x", pady=(10, 0))
            self._evidence_rows(card.body, finding.contributing)
        if finding.consider:
            section_title(card.body, "What you could consider").pack(fill="x", pady=(10, 0))
            for item in finding.consider:
                self._label(card.body, "•  " + item, size=10).pack(fill="x", pady=(2, 0))
            if finding.goto:
                FlatButton(card.body, "Open the Storage view", lambda view=finding.goto: self._navigate(view)).pack(
                    anchor="w", pady=(8, 0)
                )

    def _render_relations(self, relations: list[Relation]) -> None:
        card = Card(self.scroll.body)
        card.pack(fill="x", pady=(0, 12))
        section_title(card.body, "Things you may not have noticed").pack(fill="x")
        if not relations:
            self._label(
                card.body, "No relationships between different measurements were found.", dim=True, size=10
            ).pack(fill="x", pady=(4, 0))
            return
        for relation in relations:
            self._label(card.body, "•  " + relation.text, size=10).pack(fill="x", pady=(8, 0))
            self._evidence_rows(card.body, relation.evidence, indent=18)

    def _render_unchecked(self, result: DiagnosticResult) -> None:
        card = Card(self.scroll.body)
        card.pack(fill="x", pady=(0, 12))
        section_title(card.body, "Could not check on this PC").pack(fill="x")
        for reading in result.unchecked:
            detail = f": {reading.detail}" if reading.detail else ""
            self._label(
                card.body, f"•  {labels.title_for(reading.name)} ({reading.status.value}){detail}", dim=True, size=10
            ).pack(fill="x", pady=(3, 0))

    def _render_footer(self, result: DiagnosticResult) -> None:
        footer = tk.Frame(self.scroll.body, bg=COLORS["bg"])
        footer.pack(fill="x", pady=(0, 16))
        FlatButton(footer, "Copy as text", lambda: self.copy_result_text(result)).pack(side="left")
        FlatButton(footer, "Ask again", lambda: self.ask(result.question_id)).pack(side="left", padx=(8, 0))

    def copy_result_text(self, result: Optional[DiagnosticResult] = None) -> bool:
        result = result or self.last_result
        if result is None:
            return False
        self.clipboard_clear()
        self.clipboard_append(explain.result_to_text(result))
        return True

    # -- small builders ------------------------------------------------------------------
    def _label(self, parent: tk.Misc, text: str, dim: bool = False, size: int = 10, bold: bool = False) -> tk.Label:
        bg = parent.cget("bg")
        label = tk.Label(
            parent, text=text, bg=bg, fg=COLORS["text_dim"] if dim else COLORS["text"],
            font=font(size, "bold" if bold else "normal"), anchor="w", justify="left", wraplength=600,
        )
        self._wrap_labels.append(label)
        return label

    def _evidence_rows(self, parent: tk.Misc, rows: Sequence[Evidence], indent: int = 0) -> None:
        grid = tk.Frame(parent, bg=parent.cget("bg"))
        grid.pack(fill="x", pady=(4, 0), padx=(indent, 0))
        grid.columnconfigure(1, weight=1)
        for index, row in enumerate(rows):
            label = self._label(grid, row.label, dim=True, size=9)
            label.configure(wraplength=220)
            label.grid(row=index, column=0, sticky="nw", pady=2, padx=(0, 14))
            self._fixed_wrap(label, 220)
            value = self._label(grid, row.value_text, size=10)
            value.grid(row=index, column=1, sticky="nw", pady=2)
            tag = tk.Label(
                grid, text=explain.strength_label(row.strength), bg=parent.cget("bg"), fg=_TAG_COLORS[row.strength],
                font=font(8), anchor="e",
            )
            tag.grid(row=index, column=2, sticky="ne", pady=2, padx=(12, 0))

    def _fixed_wrap(self, label: tk.Label, width: int) -> None:
        """Keep this label at a fixed wrap width (it is not resized with the window)."""
        if label in self._wrap_labels:
            self._wrap_labels.remove(label)

    # -- keeping text wrapped to the window --------------------------------------------------
    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._apply_wrap(width=event.width)

    def _apply_wrap(self, width: Optional[int] = None, force: bool = False) -> None:
        width = width or self.scroll.width
        if width <= 1 or (width == self._last_width and not force):
            return
        self._last_width = width
        for label in self._wrap_labels:
            try:
                # leave room for the card padding, the evidence label column and the strength tag
                inside_grid = isinstance(label.master, tk.Frame) and label.grid_info().get("column") == 1
                label.configure(wraplength=max(width - (420 if inside_grid else 110), 180))
            except tk.TclError:
                continue  # destroyed by a newer render
