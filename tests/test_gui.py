"""GUI tests: they open the real OScope window for a few seconds.

Skipped automatically when Tkinter or a display is not available.
Run from the project folder:   python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OSCOPE_DEV", "1")  # lets the GUI start on a non-Windows dev machine

try:
    import tkinter as tk
    from tkinter import messagebox

    from app.collectors import labels
    from app.core import report
    from app.core.process_manager import ProcessInfo
    from app.core.sampler import Snapshot
    from app.core.storage_manager import DirectoryScanner
    from app.gui.main_window import MainWindow
    from app.history.settings_store import AppSettings, SettingsStore
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"GUI test dependencies unavailable: {exc}")

MB = 1024 * 1024


def pump(root: "tk.Tk", seconds: float) -> None:
    """Run the Tk event loop for ``seconds`` (what mainloop() does, but bounded)."""
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"No display available: {exc}")
        # keep settings/logs/reports out of the real per-user data folder
        cls._data_dir = tempfile.TemporaryDirectory()
        cls._env = mock.patch.dict(os.environ, {"OSCOPE_DATA_DIR": cls._data_dir.name})
        cls._env.start()
        cls.window = MainWindow(cls.root)
        deadline = time.time() + 15
        while cls.window.latest is None and time.time() < deadline:
            pump(cls.root, 0.2)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.window._on_close()
        except tk.TclError:
            pass
        cls._env.stop()
        cls._data_dir.cleanup()

    # -- startup / overview -----------------------------------------------------
    def test_01_opens_on_overview_and_shows_data(self):
        self.assertIsNotNone(self.window.latest, "no snapshot arrived within 15 s")
        self.assertIn("%", self.window.overview.cpu_card.value_label.cget("text"))
        self.assertIn("/", self.window.overview.memory_card.value_label.cget("text"))
        self.assertIn("/", self.window.overview.storage_card.value_label.cget("text"))
        self.assertTrue(self.window.updated_label.cget("text").startswith("Last updated: "))
        self.assertNotIn("--:--", self.window.updated_label.cget("text"))

    def test_02_manual_refresh_updates_timestamp(self):
        before = self.window.latest.taken_at
        self.window.refresh_now()
        deadline = time.time() + 6
        while self.window.latest.taken_at == before and time.time() < deadline:
            pump(self.root, 0.1)
        self.assertGreater(self.window.latest.taken_at, before)

    # -- processes --------------------------------------------------------------
    def test_03_process_table_search_and_sort(self):
        self.window.show_view("processes")
        pump(self.root, 0.5)
        view = self.window.processes
        view.group_var.set(False)  # this test is about the flat, one-row-per-process table
        pump(self.root, 0.1)
        self.assertGreater(len(view.tree.get_children()), 3)

        view.search_var.set("zzzz_no_such_process_zzzz")
        pump(self.root, 0.1)
        self.assertEqual(len(view.tree.get_children()), 0)
        self.assertEqual(view.empty_label.cget("text"), "No matching processes found.")

        name = self.window.latest.processes[0].name
        view.search_var.set(name.lower())
        pump(self.root, 0.1)
        self.assertGreaterEqual(len(view.tree.get_children()), 1)
        view.search_var.set("")
        pump(self.root, 0.1)

        view._sort_by("pid")
        pids = [int(view.tree.item(i, "values")[1]) for i in view.tree.get_children()]
        self.assertEqual(pids, sorted(pids, reverse=pids[0] > pids[-1]))
        view._sort_by("memory")
        self.assertTrue(view._sort_descending)

    def test_04_process_details_panel(self):
        view = self.window.processes
        view.group_var.set(False)
        pump(self.root, 0.1)
        view.tree.selection_set(str(os.getpid()))
        pump(self.root, 1.5)
        for label in ("Name", "PID", "Parent PID", "Memory", "CPU", "Status", "Executable"):
            self.assertIn(label, view._detail_labels)
        self.assertEqual(view._detail_labels["PID"].cget("text"), str(os.getpid()))

    # -- storage ----------------------------------------------------------------
    def test_05_storage_scan_and_large_file_threshold(self):
        self.window.show_view("storage")
        view = self.window.storage
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "sub").mkdir()
            (Path(tmp) / "sub" / "big.bin").write_bytes(b"x" * (3 * MB))
            (Path(tmp) / "small.txt").write_bytes(b"hi")
            view.set_threshold(1)
            view.scan_folder(tmp)
            deadline = time.time() + 10
            while view.last_result is None or view.last_result.root != str(Path(tmp)):
                pump(self.root, 0.1)
                self.assertLess(time.time(), deadline, "scan did not finish")
            pump(self.root, 0.2)
            self.assertEqual(view.last_result.file_count, 2)
            self.assertEqual([view.files_tree.item(i, "values")[0] for i in view.files_tree.get_children()], ["big.bin"])

            view.set_threshold(10)
            self.assertEqual(len(view.files_tree.get_children()), 0)
            self.assertEqual(view.files_message.cget("text"), "No files above the selected size threshold were found.")
            view.set_threshold(500)

    def test_06_invalid_folder_shows_message_not_traceback(self):
        view = self.window.storage
        view.scan_folder(os.path.join(tempfile.gettempdir(), "oscope_no_such_folder_xyz"))
        deadline = time.time() + 5
        while "does not exist" not in view.progress_label.cget("text") and time.time() < deadline:
            pump(self.root, 0.1)
        self.assertIn("does not exist", view.progress_label.cget("text"))

    def test_07_gui_stays_responsive_during_scan(self):
        """While a (deliberately slowed) scan runs, the Tk loop must keep ticking."""
        view = self.window.storage
        ticks: list[float] = []

        def tick() -> None:
            ticks.append(time.monotonic())
            self.root.after(20, tick)

        with tempfile.TemporaryDirectory() as tmp:
            for i in range(250):
                (Path(tmp) / f"f{i}.txt").write_bytes(b"x")
            original = DirectoryScanner._record_file

            def slow_record(self_, entry):
                time.sleep(0.004)  # 250 files -> at least ~1 second of scanning
                return original(self_, entry)

            with mock.patch.object(DirectoryScanner, "_record_file", slow_record):
                self.root.after(20, tick)
                view.scan_folder(tmp)
                started = time.monotonic()
                while view._scanning and time.monotonic() - started < 15:
                    self.root.update()
                    time.sleep(0.005)
                pump(self.root, 0.2)

        self.assertGreater(len(ticks), 20, "the GUI thread was blocked while scanning")
        longest_gap = max(b - a for a, b in zip(ticks, ticks[1:]))
        self.assertLess(longest_gap, 0.5, f"GUI froze for {longest_gap:.2f}s during the scan")

    # -- report -----------------------------------------------------------------
    def test_08_generate_report_saves_timestamped_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(report, "REPORTS_DIR", Path(tmp)), mock.patch.object(
            messagebox, "askyesno", return_value=False
        ) as asked:
            self.window.generate_report()
            files = list(Path(tmp).glob("oscope_report_*.txt"))
            self.assertEqual(len(files), 1)
            self.assertRegex(files[0].name, r"^oscope_report_\d{4}-\d{2}-\d{2}_\d{6}\.txt$")
            self.assertIn("OSCOPE SYSTEM REPORT", files[0].read_text(encoding="utf-8"))
            asked.assert_called_once()

    # -- storage treemap ----------------------------------------------------------
    def test_09_storage_defaults_to_treemap_and_toggle_switches_modes(self):
        view = self.window.storage
        self.assertEqual(view.view_mode, "treemap")
        pump(self.root, 0.1)
        self.assertTrue(view.treemap_frame.winfo_ismapped())

        view.set_view_mode("list")
        pump(self.root, 0.1)
        self.assertEqual(view.view_mode, "list")
        self.assertFalse(view.treemap_frame.winfo_ismapped())

        view.set_view_mode("treemap")
        pump(self.root, 0.1)
        self.assertEqual(view.view_mode, "treemap")
        self.assertTrue(view.treemap_frame.winfo_ismapped())

    def test_10_treemap_click_selects_and_shows_detail(self):
        view = self.window.storage
        view.set_view_mode("treemap")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "sub").mkdir()
            (Path(tmp) / "sub" / "big.bin").write_bytes(b"x" * (3 * MB))
            (Path(tmp) / "small.txt").write_bytes(b"hi")
            view.scan_folder(tmp)
            deadline = time.time() + 10
            while view.last_result is None or view.last_result.root != str(Path(tmp)):
                pump(self.root, 0.1)
                self.assertLess(time.time(), deadline, "scan did not finish")
            pump(self.root, 0.3)

            self.assertTrue(view.treemap_canvas._rects, "treemap produced no rectangles")
            rect, node = view.treemap_canvas._rects[0]
            cx, cy = int(rect.x + rect.w / 2), int(rect.y + rect.h / 2)
            view.treemap_canvas.event_generate("<Button-1>", x=cx, y=cy)
            pump(self.root, 0.1)

            self.assertEqual(view._selected_node.name, node.name)
            self.assertEqual(view.detail_panel.name_label.cget("text"), node.name)
            self.assertTrue(view.detail_panel.reveal_button._enabled)

    def test_11_treemap_double_click_zooms_and_breadcrumb_navigates_back(self):
        view = self.window.storage
        view.set_view_mode("treemap")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "sub").mkdir()
            (Path(tmp) / "sub" / "big.bin").write_bytes(b"x" * (3 * MB))
            (Path(tmp) / "small.txt").write_bytes(b"hi")
            view.scan_folder(tmp)
            deadline = time.time() + 10
            while view.last_result is None or view.last_result.root != str(Path(tmp)):
                pump(self.root, 0.1)
                self.assertLess(time.time(), deadline, "scan did not finish")
            pump(self.root, 0.3)

            rect, node = next((r, n) for r, n in view.treemap_canvas._rects if n.is_dir)
            cx, cy = int(rect.x + rect.w / 2), int(rect.y + rect.h / 2)
            # A double-click must be synthesized as two real clicks; Tk refuses
            # to fire a synthetic <Double-Button-1> directly.
            for _ in range(2):
                view.treemap_canvas.event_generate("<Button-1>", x=cx, y=cy)
                view.treemap_canvas.event_generate("<ButtonRelease-1>", x=cx, y=cy)
            pump(self.root, 0.1)

            self.assertEqual(len(view._zoom_stack), 2)
            self.assertEqual(view._zoom_stack[-1].name, node.name)
            self.assertTrue(view.treemap_canvas._rects)
            self.assertTrue(all(child.name == "big.bin" for _, child in view.treemap_canvas._rects))

            view._on_breadcrumb_navigate(0)
            pump(self.root, 0.1)
            self.assertEqual(len(view._zoom_stack), 1)

    def test_12_show_in_explorer_calls_windows_backend(self):
        view = self.window.storage
        view.set_view_mode("treemap")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "file.bin").write_bytes(b"x" * MB)
            view.scan_folder(tmp)
            deadline = time.time() + 10
            while view.last_result is None or view.last_result.root != str(Path(tmp)):
                pump(self.root, 0.1)
                self.assertLess(time.time(), deadline, "scan did not finish")
            pump(self.root, 0.3)

            _rect, node = view.treemap_canvas._rects[0]
            view._on_treemap_select(node)

            with mock.patch("app.gui.storage_view.windows_backend.reveal_in_explorer", return_value=True) as reveal:
                result = view.reveal_selected_in_explorer()
            self.assertTrue(result)
            reveal.assert_called_once_with(node.path)

    def test_13_applied_settings_are_saved_and_restored_on_next_launch(self):
        window = self.window
        try:
            window._apply_settings(AppSettings(refresh_interval=5, large_file_mb=123))
            saved = SettingsStore.default().load()  # what the next launch would read
            self.assertEqual((saved.refresh_interval, saved.large_file_mb), (5, 123))
            self.assertEqual(window.storage.threshold_mb, 123)
        finally:
            window._apply_settings(AppSettings())  # restore defaults for any later test
        self.assertEqual(SettingsStore.default().load(), AppSettings())

    # -- Phase 2: grouping by program ----------------------------------------------
    @staticmethod
    def _snapshot_with(processes):
        return Snapshot(
            taken_at=datetime.now(), cpu_percent=1.0, memory=None, storage=None, uptime_seconds=1.0,
            processes=processes,
        )

    @staticmethod
    def _proc(pid, name, mb, cpu=1.0):
        return ProcessInfo(pid=pid, name=name, cpu_percent=cpu, memory_bytes=mb * MB, status="Running", ppid=1)

    def test_14_processes_are_grouped_by_program(self):
        view = self.window.processes
        view.search_var.set("")
        view.group_var.set(True)
        procs = [
            self._proc(9001, "chrome.exe", 100), self._proc(9002, "chrome.exe", 300),
            self._proc(9003, "Chrome.exe", 50), self._proc(9004, "notepad.exe", 10),
        ]
        # fed directly and checked without pumping the event loop, so a real snapshot cannot interleave
        view.update_snapshot(self._snapshot_with(procs))

        top = view.tree.get_children()
        self.assertEqual(top, ("g:chrome.exe", "g:notepad.exe"))          # biggest memory total first
        self.assertEqual(len(view.tree.get_children("g:chrome.exe")), 3)
        values = view.tree.item("g:chrome.exe", "values")
        self.assertEqual((values[0], values[3]), ("Google Chrome  (3)", "450 MB"))
        self.assertTrue(view.tree.item("9001", "values")[0].startswith("↳ chrome.exe"))
        self.assertIn("2 programs", view.count_label.cget("text"))
        self.assertIn("4 processes", view.count_label.cget("text"))
        self.assertEqual(view.top_mem_list.winfo_children()[0].cget("text"), "Google Chrome ×3")
        self.assertIn("tree", str(view.tree.cget("show")))

    def test_15_selecting_a_group_shows_its_totals_and_keeps_them_live(self):
        view = self.window.processes
        view.search_var.set("")
        view.group_var.set(True)
        procs = [self._proc(9001, "chrome.exe", 100), self._proc(9002, "chrome.exe", 300)]
        view.update_snapshot(self._snapshot_with(procs))

        view._select_group("chrome.exe")
        self.assertEqual(view._detail_labels["Program"].cget("text"), "Google Chrome")
        self.assertEqual(view._detail_labels["Processes"].cget("text"), "2")
        self.assertEqual(view._detail_labels["Memory sum"].cget("text"), "400 MB")
        self.assertIn("shared", view._detail_labels["Note"].cget("text"))

        procs[1] = self._proc(9002, "chrome.exe", 500)
        view.update_snapshot(self._snapshot_with(procs))
        self.assertEqual(view._detail_labels["Memory sum"].cget("text"), "600 MB")

        view.update_snapshot(self._snapshot_with([self._proc(9004, "notepad.exe", 10)]))
        self.assertIsNone(view._selected_group)  # the program went away

    def test_16_search_by_pid_in_grouped_mode_and_toggle_back_to_flat(self):
        view = self.window.processes
        view.group_var.set(True)
        procs = [self._proc(9001, "chrome.exe", 100), self._proc(9002, "chrome.exe", 300), self._proc(9004, "a.exe", 1)]
        view.update_snapshot(self._snapshot_with(procs))

        view.search_var.set("9002")
        self.assertEqual(view.tree.get_children(), ("g:chrome.exe",))
        self.assertEqual(view.tree.get_children("g:chrome.exe"), ("9002",))
        view.search_var.set("")

        view.group_var.set(False)
        self.assertEqual(set(view.tree.get_children()), {"9001", "9002", "9004"})
        self.assertNotIn("tree", str(view.tree.cget("show")))
        self.assertIn("3 processes", view.count_label.cget("text"))

        view.group_var.set(True)
        view.update_snapshot(self.window.latest)  # back to real data for any later test

    def test_17_overview_shows_every_evidence_source_with_an_honest_status(self):
        overview = self.window.overview
        snapshot = self.window.latest
        overview.update_snapshot(snapshot)
        for name, _label in labels.EVIDENCE_ROWS:
            self.assertIn(name, overview.evidence_values)
        # off Windows no collector exists for these, and the UI must say so instead of showing a number
        self.assertIn("Not supported", overview.evidence_values["temp.acpi_max"].cget("text"))
        self.assertIn("Not supported", overview.evidence_values["gpu.utilization"].cget("text"))
        # disk activity is cross-platform: once warmed up it shows a rate or an honest reason
        disk_text = overview.evidence_values["disk.read_bps"].cget("text")
        self.assertTrue(disk_text.endswith("/s") or "Unavailable" in disk_text, disk_text)

    def test_18_evidence_details_dialog_lists_reasons_and_closes(self):
        self.window.show_view("overview")
        dialog = self.window.overview.show_evidence_details()
        try:
            text_widgets = [w for w in dialog.winfo_children()[0].winfo_children()[1].winfo_children()
                            if w.winfo_class() == "Text"]
            content = text_widgets[0].get("1.0", "end")
            self.assertIn("GPU load", content)
            self.assertIn("never as a guessed number", content)
            self.assertEqual(str(text_widgets[0].cget("state")), "disabled")
        finally:
            dialog.destroy()  # it holds a modal grab; release it for the other tests
        self.root.update()

    def test_20_privilege_state_is_shown_next_to_the_evidence(self):
        from dataclasses import replace

        overview = self.window.overview
        for elevated, expected in ((True, "administrator"), (False, "Standard user"), (None, "")):
            overview.update_snapshot(replace(self.window.latest, elevated=elevated))
            self.assertIn(expected, overview.privilege_label.cget("text"))
        overview.update_snapshot(self.window.latest)

    def test_19_overview_status_cards_keep_their_height(self):
        self.window.show_view("overview")
        pump(self.root, 0.3)
        overview = self.window.overview
        self.assertGreaterEqual(overview.status_card.winfo_height(), 120)
        self.assertGreaterEqual(overview.details_card.winfo_height(), 120)

    # -- Phase 3: Ask OScope ---------------------------------------------------------------
    def _busy_context(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_analysis as ta

        readings = dict(ta.busy_readings(**dict([ta.avail("power.on_battery", True), ta.avail("power.mode", "Balanced"),
                                                 ta.missing("temp.acpi_max"), ta.missing("fan.rpm")])))
        procs = [ta.proc(i, "chrome.exe", 200) for i in range(1, 20)] + [ta.proc(90, "Photoshop.exe", 3000, cpu=30.0)]
        snapshot = ta.make_snapshot(cpu=92, mem_pct=91, storage_pct=93, processes=procs, readings=readings, uptime=300)
        return snapshot, ta.make_window(cpu=92, mem=91, storage=93, readings=readings)

    def _ask(self, question_id, context=None):
        view = self.window.ask
        view.last_result = None
        real = view._get_context
        if context is not None:
            view._get_context = lambda: context
        try:
            view.ask(question_id)
            deadline = time.time() + 10
            while view.last_result is None and time.time() < deadline:
                pump(self.root, 0.05)
        finally:
            view._get_context = real
        self.assertIsNotNone(view.last_result, "no answer arrived")
        return view.last_result

    @staticmethod
    def _texts(widget):
        found = []
        for child in widget.winfo_children():
            if child.winfo_class() == "Label":
                found.append(str(child.cget("text")))
            found += GuiTests._texts(child)
        return found

    def test_21_app_opens_on_the_question_screen(self):
        self.assertIn("ask", self.window._views)
        self.assertEqual(list(self.window.ask.question_buttons), ["slow", "ram", "heat", "storage", "changed", "everything"])
        window = self.window
        window.show_view("ask")
        self.assertEqual(window._current, "ask")

    def test_22_asking_a_question_renders_the_four_layers_and_what_could_not_be_checked(self):
        self.window.show_view("ask")
        result = self._ask("slow", self._busy_context())
        texts = "\n".join(self._texts(self.window.ask.scroll.body))
        for expected in ("WHAT WE FOUND", "WHAT IS HAPPENING", "WHAT IS CONTRIBUTING", "WHAT YOU COULD CONSIDER",
                         "THINGS YOU MAY NOT HAVE NOTICED", "COULD NOT CHECK ON THIS PC", "Measured"):
            self.assertIn(expected, texts)
        self.assertIn("High memory use", texts)
        self.assertEqual(result.question_id, "slow")
        self.assertTrue(result.relations)

    def test_23_without_data_the_view_waits_instead_of_guessing(self):
        view = self.window.ask
        real = view._get_context
        view._get_context = lambda: (None, [])
        try:
            view.ask("slow")
        finally:
            view._get_context = real
        self.assertTrue(any("first measurements" in t for t in self._texts(view.scroll.body)))

    def test_24_only_the_latest_answer_is_shown(self):
        view = self.window.ask
        context = self._busy_context()
        view._get_context = lambda: context
        try:
            view.last_result = None
            view.ask("ram")
            view.ask("heat")  # asked before the first answer arrived
            deadline = time.time() + 10
            while (view.last_result is None or view.last_result.question_id != "heat") and time.time() < deadline:
                pump(self.root, 0.05)
        finally:
            view._get_context = lambda: (self.window.latest, self.window.sampler.recent_samples())
        self.assertEqual(view.last_result.question_id, "heat")
        self.assertIn("Why is my laptop hot or loud?", self._texts(view.scroll.body))

    def test_25_copy_as_text_puts_the_answer_on_the_clipboard(self):
        self._ask("ram", self._busy_context())
        self.assertTrue(self.window.ask.copy_result_text())
        copied = self.root.clipboard_get()
        self.assertIn("Why is my RAM full?", copied)
        self.assertIn("WHAT WE FOUND", copied)

    def test_26_workload_choice_is_saved_and_reorders_without_hiding(self):
        view = self.window.ask
        try:
            self._ask("slow", self._busy_context())
            before = sorted(f.id for f in view.last_result.findings)
            view.workload_var.set("Gaming")
            context = self._busy_context()
            view._get_context = lambda: context
            view._on_workload_selected()  # an answer is on screen, so it is re-asked for the new workload
            deadline = time.time() + 10
            while view.last_result.workload != "Gaming" and time.time() < deadline:
                pump(self.root, 0.05)
            self.assertEqual(SettingsStore.default().load().workload, "gaming")
            self.assertEqual(view.last_result.workload, "Gaming")
            self.assertEqual(sorted(f.id for f in view.last_result.findings), before)
        finally:
            view._get_context = lambda: (self.window.latest, self.window.sampler.recent_samples())
            view.workload_var.set("General heavy use")
            view._on_workload_selected()
            pump(self.root, 0.3)

    def test_27_storage_answer_offers_to_open_the_storage_view(self):
        self.window.show_view("ask")
        self._ask("storage", self._busy_context())
        buttons = [w for w in self._all_widgets(self.window.ask.scroll.body)
                   if w.winfo_class() == "Label" and str(w.cget("text")) == "Open the Storage view"]
        self.assertTrue(buttons)
        buttons[0]._command()
        self.assertEqual(self.window._current, "storage")
        self.window.show_view("ask")

    @staticmethod
    def _all_widgets(widget):
        found = []
        for child in widget.winfo_children():
            found.append(child)
            found += GuiTests._all_widgets(child)
        return found

    def test_28_show_me_everything_works_on_this_machines_real_data(self):
        pump(self.root, 1.0)  # let a few real samples accumulate
        result = self._ask("everything")
        self.assertEqual({f.id for f in result.findings}, {"cpu", "memory", "disk", "gpu", "storage", "power", "thermal", "startup"})
        self.assertTrue(result.sections)
        self.assertTrue(result.summary)

    # -- Phase 4: history ----------------------------------------------------------------------
    def test_29_what_changed_uses_the_recorded_history(self):
        from app.history.queries import HistoryContext
        from app.history.records import MetricRow

        now = 1_800_000_000
        gb = 2 ** 30
        rows = [MetricRow(now - m * 60, 20.0, 45.0 if m > 15 else 80.0, None, None, None, None, None, None,
                          (200 - (120 - m) * 0.1) * gb, 500 * gb, 15) for m in range(120, 0, -1)]
        context = HistoryContext(now, tuple(rows), (), (), 0)
        view = self.window.ask
        view._get_history = lambda: context
        try:
            result = self._ask("changed", self._busy_context())
        finally:
            view._get_history = self.window.history.context
        self.assertEqual([f.id for f in result.findings], ["changes"])
        self.assertIn("memory use", result.findings[0].happening)
        self.assertIn("What changed recently", "\n".join(self._texts(view.scroll.body)))

    def test_30_notable_answers_are_remembered_as_events(self):
        history = self.window.history
        self.assertTrue(history.enabled)
        history._last_event.clear()
        self._ask("slow", self._busy_context())
        context = history.context()
        self.assertIsNotNone(context)
        self.assertIn("memory", {e.finding_id for e in context.events})
        self.assertTrue(str(history.path).startswith(self._data_dir.name))  # test data folder, not the real one

    def test_31_settings_dialog_toggles_history_and_clears_it(self):
        from app.gui.dialogs import show_settings

        applied = []
        local = AppSettings()
        dialog = show_settings(self.root, local, lambda s: applied.append((s.history_enabled, s.refresh_interval)), self.window.history)
        try:
            widgets = self._all_widgets(dialog)
            checkbox = next(w for w in widgets if w.winfo_class() == "Checkbutton")
            self.assertIn("remember measurements", str(checkbox.cget("text")))
            texts = [str(w.cget("text")) for w in widgets if w.winfo_class() == "Label"]
            self.assertTrue(any("nothing is sent anywhere" in t for t in texts))
            self.assertTrue(any(t.startswith("History is on") for t in texts))

            from unittest import mock
            clear = next(w for w in widgets if w.winfo_class() == "Label" and str(w.cget("text")) == "Clear history")
            with mock.patch("app.gui.dialogs.messagebox.askyesno", return_value=True) as ask:
                clear._command()
            ask.assert_called_once()
            status = next(w for w in widgets if w.winfo_class() == "Label" and str(w.cget("text")) == "History cleared.")
            self.assertTrue(status.winfo_exists())
            context = self.window.history.context()
            self.assertEqual((len(context.metrics), len(context.events)), (0, 0))

            checkbox.invoke()   # untick
            save = next(w for w in widgets if w.winfo_class() == "Label" and str(w.cget("text")) == "Save")
            save._command()
        finally:
            if dialog.winfo_exists():
                dialog.destroy()
        self.assertEqual(applied, [(False, local.refresh_interval)])

    def test_32_main_window_applies_the_history_switch_and_saves_it(self):
        window = self.window
        try:
            window._apply_settings(AppSettings(history_enabled=False))
            self.assertFalse(window.history.enabled)
            self.assertFalse(SettingsStore.default().load().history_enabled)
            self.assertIn("off", window.history.status_text())
        finally:
            window._apply_settings(AppSettings())
        self.assertTrue(window.history.enabled)
        self.assertTrue(SettingsStore.default().load().history_enabled)


if __name__ == "__main__":
    unittest.main()
