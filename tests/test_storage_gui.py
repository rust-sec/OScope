"""Phase 5 GUI tests: the folder tree, treemap and details staying in sync, search, filters, navigation,
progress, scan notes and snapshot comparison.

Skipped automatically when Tkinter or a display is not available (run under xvfb-run on Linux).
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OSCOPE_DEV", "1")

try:
    import tkinter as tk

    from app.core.tree_scanner import ScanStatus, TreeNode, TreeScanner
    from app.core.tree_utils import find_chain
    from app.gui import storage_view as storage_view_module
    from app.gui.components import apply_theme
    from app.gui.storage_view import StorageView
    from app.gui.tree_panel import MAX_CHILDREN_SHOWN, TreePanel
    from app.history.service import HistoryService
    from app.utils.background import BackgroundRunner
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"GUI test dependencies unavailable: {exc}")

MB = 1024 * 1024


def pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


class StorageGuiCase(unittest.TestCase):
    """A real StorageView in a bare window (with real history in a temp folder)."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"No display available: {exc}")
        cls.root.geometry("1200x720")
        apply_theme(cls.root)
        cls.runner = BackgroundRunner(cls.root)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.runner.close()
            cls.root.destroy()
        except tk.TclError:
            pass

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name) / "data"
        self.data.mkdir()
        self.target = Path(self._tmp.name) / "target"
        self.target.mkdir()
        self.history = HistoryService(self.data, True)
        self.addCleanup(self.history.close)
        self.elevated = False
        self.view = StorageView(self.root, self.runner, self.history, is_elevated=lambda: self.elevated)
        self.view.pack(fill="both", expand=True)
        self.addCleanup(self.view.destroy)
        self.addCleanup(self._drain)  # runs first: let pending background callbacks finish before the widgets go
        pump(self.root, 0.1)

    def _drain(self):
        self.view.cancel_running_scan()
        for scanner in getattr(ControlledScanner, "instances", []):
            scanner.gate.set()
        deadline = time.time() + 3
        while self.view._scanning and time.time() < deadline:
            pump(self.root, 0.05)
        pump(self.root, 0.3)

    # -- helpers -------------------------------------------------------------------------
    def build_sample(self):
        write(self.target / "Videos" / "Raw" / "clip.mp4", 3 * MB)
        write(self.target / "Videos" / "notes.txt", 2000)
        write(self.target / "Docs" / "report.pdf", 1 * MB)
        write(self.target / "needle_file.bin", 500)
        write(self.target / "Docs" / "deep" / "needle_in_deep.txt", 100)

    def scan(self, folder=None, wait=15):
        folder = str(folder or self.target)
        self.view.last_result = None
        self.view.scan_folder(folder)
        deadline = time.time() + wait
        while (self.view.last_result is None or self.view._scanning) and time.time() < deadline:
            pump(self.root, 0.05)
        self.assertIsNotNone(self.view.last_result, "scan did not finish")
        pump(self.root, 0.2)
        return self.view.last_result

    def node(self, name):
        tree = self.view.last_result.tree
        return next(n for n in _walk(tree) if n.name == name)


def _walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


# --------------------------------------------------------------------------- #
class SyncTests(StorageGuiCase):
    def setUp(self):
        super().setUp()
        self.build_sample()
        self.result = self.scan()

    def test_the_finished_scan_fills_the_tree_and_shows_the_root_in_the_details(self):
        view = self.view
        self.assertIs(view.tree_panel.root_node, self.result.tree)
        self.assertEqual(set(view.tree_panel.visible_names()), {"Videos", "Docs", "needle_file.bin"})
        self.assertEqual(view._selected_node, self.result.tree)
        self.assertEqual(view.detail_panel.name_label.cget("text"), self.result.tree.name)
        self.assertIn("files", view.detail_panel._rows["Contents"].cget("text"))

    def test_selecting_in_the_tree_moves_the_treemap_and_the_details(self):
        view = self.view
        clip = self.node("clip.mp4")
        view._on_tree_select(clip)
        self.assertEqual([n.name for n in view._zoom_stack][-1], "Raw")           # the treemap shows the clip's folder
        self.assertIs(view.treemap_canvas.selected, clip)                          # ...with the clip highlighted
        self.assertEqual(view.detail_panel.name_label.cget("text"), "clip.mp4")
        self.assertTrue(view.back_button._enabled)

    def test_selecting_in_the_treemap_selects_the_same_item_in_the_tree(self):
        view = self.view
        doc = self.node("report.pdf")
        view._on_tree_select(doc)                                                  # get to the folder first
        view._on_treemap_select(doc)
        pump(self.root, 0.2)
        self.assertIs(view.tree_panel.selected_node(), doc)
        self.assertEqual(view.detail_panel.name_label.cget("text"), "report.pdf")

    def test_the_tree_echo_never_undoes_a_zoom(self):
        view = self.view
        videos = self.node("Videos")
        view._on_treemap_select(videos)                                            # selects it in the tree (async echo to follow)
        view._on_treemap_zoom(videos)                                              # then zoom straight in
        pump(self.root, 0.4)                                                       # let every queued event arrive
        self.assertEqual([n.name for n in view._zoom_stack][-1], "Videos")

    def test_zooming_never_leaves_a_stale_selection(self):
        view = self.view
        view._on_treemap_select(self.node("needle_file.bin"))
        view._on_treemap_zoom(self.node("Videos"))
        self.assertEqual(view._selected_node.name, "Videos")
        self.assertEqual(view.detail_panel.name_label.cget("text"), "Videos")
        self.assertEqual(view.tree_panel.selected_node().name, "Videos")
        self.assertEqual(view.detail_panel.path_var.get(), str(self.target / "Videos"))

    def test_double_clicking_a_folder_in_the_tree_opens_it(self):
        view = self.view
        view._on_tree_activate(self.node("Docs"))
        self.assertEqual([n.name for n in view._zoom_stack][-1], "Docs")
        self.assertEqual(view._selected_node.name, "Docs")

    def test_breadcrumb_navigation_selects_the_folder_shown(self):
        view = self.view
        view._on_treemap_zoom(self.node("Videos"))
        view._on_treemap_zoom(self.node("Raw"))
        view._on_breadcrumb_navigate(1)
        self.assertEqual([n.name for n in view._zoom_stack][-1], "Videos")
        self.assertEqual(view._selected_node.name, "Videos")

    def test_synthetic_group_items_can_be_selected_but_not_revealed(self):
        write(self.target / "Many" / "a.bin", 10)
        write(self.target / "Many" / "b.bin", 20)
        with mock.patch.object(storage_view_module, "TreeScanner", lambda *a, **k: TreeScanner(*a, max_files_per_folder=1, **{kk: v for kk, v in k.items()})):
            self.scan()
        group = next(n for n in _walk(self.view.last_result.tree) if n.synthetic)
        self.view._on_tree_select(group)
        self.assertEqual(self.view.detail_panel.name_label.cget("text"), group.name)
        self.assertFalse(self.view.detail_panel.reveal_button._enabled)
        self.assertEqual(self.view.detail_panel._rows["Type"].cget("text"), "Grouped small files")

    def test_details_show_flags_and_counts(self):
        write(self.target / ".hidden_cache" / "blob.bin", 4000)
        self.scan()
        view = self.view
        view._on_tree_select(self.node(".hidden_cache"))
        panel = view.detail_panel
        self.assertEqual(panel._rows["Notes"].cget("text"), "Hidden")
        self.assertEqual(panel._rows["Contents"].cget("text"), "1 files, 0 folders")
        view._on_tree_select(self.node("blob.bin"))
        self.assertIn("File", panel._rows["Type"].cget("text"))
        self.assertTrue(panel._rows["Modified"].cget("text"))
        self.assertFalse(panel._row_frames["Contents"].winfo_ismapped())            # empty rows are hidden


class NavigationTests(StorageGuiCase):
    def setUp(self):
        super().setUp()
        self.build_sample()
        self.scan()

    def test_back_goes_up_one_level_and_the_button_follows(self):
        view = self.view
        self.assertFalse(view.back_button._enabled)
        view._on_treemap_zoom(self.node("Videos"))
        view._on_treemap_zoom(self.node("Raw"))
        self.assertTrue(view.back_button._enabled)
        self.assertTrue(view.go_back())
        self.assertEqual(view._zoom_stack[-1].name, "Videos")
        self.assertTrue(view.go_back())
        self.assertFalse(view.go_back())                                           # already at the top
        self.assertFalse(view.back_button._enabled)

    def test_escape_goes_back_clears_search_and_filter_in_that_order(self):
        view = self.view
        view._on_treemap_zoom(self.node("Videos"))
        view.legend.set_active("Videos")
        view.search_var.set("zzz")
        view._on_escape()
        self.assertEqual(view.search_var.get(), "")                                # search first
        self.assertEqual(len(view._zoom_stack), 2)
        view._on_escape()
        self.assertIsNone(view.legend.active)                                      # then the filter
        self.assertEqual(len(view._zoom_stack), 2)
        view._on_escape()
        self.assertEqual(len(view._zoom_stack), 1)                                 # then up a level

    def test_escape_stops_a_running_scan(self):
        view = self.view
        view._scanning = True
        view._cancel = threading.Event()
        view._on_escape()
        self.assertTrue(view._cancel.is_set())
        view._scanning = False

    def test_right_click_on_the_treemap_goes_up(self):
        view = self.view
        view._on_treemap_zoom(self.node("Videos"))
        view.treemap_canvas.event_generate("<Button-3>", x=10, y=10)
        pump(self.root, 0.1)
        self.assertEqual(len(view._zoom_stack), 1)

    def test_the_folder_tree_can_be_hidden(self):
        view = self.view
        view.tree_visible.set(False)
        pump(self.root, 0.1)
        self.assertFalse(view._left.winfo_ismapped())
        view.tree_visible.set(True)
        pump(self.root, 0.1)
        self.assertTrue(view._left.winfo_ismapped())


class SearchAndFilterTests(StorageGuiCase):
    def setUp(self):
        super().setUp()
        self.build_sample()
        self.scan()

    def wait_for_results(self, count=None):
        deadline = time.time() + 5
        while time.time() < deadline:
            pump(self.root, 0.05)
            if self.view._search_results and (count is None or len(self.view._search_results) == count):
                return
        self.fail("no search results arrived")

    def test_search_lists_matches_largest_first_and_reveals_the_selected_one(self):
        view = self.view
        view.search_var.set("needle")
        self.wait_for_results(2)
        self.assertEqual([n.name for n in view._search_results], ["needle_file.bin", "needle_in_deep.txt"])
        self.assertIn("2 matches", view.search_header.cget("text"))
        view.search_tree.selection_set("1")
        pump(self.root, 0.3)
        deep = self.node("needle_in_deep.txt")
        self.assertIs(view._selected_node, deep)
        self.assertEqual(view._zoom_stack[-1].name, "deep")                        # the treemap moved to its folder
        self.assertIs(view.tree_panel.selected_node(), deep)                       # and the tree opened up to it
        self.assertEqual(view.detail_panel.name_label.cget("text"), "needle_in_deep.txt")

    def test_search_is_debounced_and_clearing_it_restores_the_tree(self):
        view = self.view
        view.search_var.set("n")
        view.search_var.set("ne")
        view.search_var.set("needle")
        self.assertIsNotNone(view._search_after)                                   # nothing has run yet
        self.wait_for_results()
        view.search_var.set("")
        pump(self.root, 0.2)
        self.assertEqual(view._search_results, [])
        self.assertEqual(view.search_tree.get_children(), ())

    def test_a_search_with_no_matches_says_so(self):
        self.view.search_var.set("zzz_nothing")
        pump(self.root, 0.9)
        self.assertIn("0 matches", self.view.search_header.cget("text"))

    def test_the_category_filter_dims_the_treemap_and_narrows_the_large_files(self):
        view = self.view
        view.set_threshold(1)
        videos_and_docs = {r["file"] for r in view._large_rows}
        self.assertEqual(len(view.files_tree.get_children()), 2)                   # clip.mp4 and report.pdf
        view.legend.set_active("Videos")
        self.assertEqual(view.treemap_canvas._filter, "Videos")
        self.assertEqual([view.files_tree.item(i, "values")[0] for i in view.files_tree.get_children()], ["clip.mp4"])
        self.assertIn("Videos", view.files_note.cget("text"))
        view.legend.toggle("Videos")                                               # click again clears it
        self.assertIsNone(view.treemap_canvas._filter)
        self.assertEqual(len(view.files_tree.get_children()), 2)
        self.assertTrue({"clip.mp4", "report.pdf"} <= videos_and_docs)

    def test_the_filter_also_narrows_search_results(self):
        view = self.view
        write(self.target / "needle_movie.mp4", 5000)
        self.scan()
        view.legend.set_active("Videos")
        view.search_var.set("needle")
        self.wait_for_results(1)
        self.assertEqual([n.name for n in view._search_results], ["needle_movie.mp4"])
        self.assertIn("Videos files only", view.search_header.cget("text"))

    def test_large_file_headers_sort_and_toggle(self):
        view = self.view
        view.set_threshold(1)
        names = lambda: [view.files_tree.item(i, "values")[0] for i in view.files_tree.get_children()]  # noqa: E731
        self.assertEqual(names(), ["clip.mp4", "report.pdf"])                      # size, biggest first
        view._sort_files_by("size")
        self.assertEqual(names(), ["report.pdf", "clip.mp4"])                      # same column again: reversed
        view._sort_files_by("file")
        self.assertEqual(names(), ["clip.mp4", "report.pdf"])                      # name: A to Z
        view._sort_files_by("file")
        self.assertEqual(names(), ["report.pdf", "clip.mp4"])
        self.assertIn("▼", str(view.files_tree.heading("file", "text")))                # descending now


# --------------------------------------------------------------------------- #
class ControlledScanner:
    """Stands in for TreeScanner: blocks until released, can report status, records its cancel event."""

    instances: list = []

    def __init__(self, root, cancel_event=None, status=None, **_ignored):
        self.root, self.cancel_event, self.status = root, cancel_event, status
        self.gate = threading.Event()
        self.started = threading.Event()
        ControlledScanner.instances.append(self)

    def scan(self):
        self.started.set()
        if self.status:
            self.status(ScanStatus(120, 7, 4 * MB, str(Path(self.root) / "some" / "deep" / "folder")))
        self.gate.wait(10)
        return TreeScanner(self.root).scan()


class ProgressAndRescanTests(StorageGuiCase):
    def setUp(self):
        super().setUp()
        write(self.target / "a.bin", 1000)
        ControlledScanner.instances = []
        patcher = mock.patch.object(storage_view_module, "TreeScanner", ControlledScanner)
        patcher.start()
        self.addCleanup(patcher.stop)

    def wait_started(self, scanner):
        self.assertTrue(scanner.started.wait(5))
        pump(self.root, 0.3)

    def test_progress_bar_and_current_folder_are_shown_only_while_scanning(self):
        view = self.view
        view.scan_folder(str(self.target))
        scanner = ControlledScanner.instances[-1]
        self.wait_started(scanner)
        self.assertTrue(view._scanning)
        self.assertTrue(view.progressbar.winfo_ismapped())
        self.assertEqual(str(view.progressbar.cget("mode")), "indeterminate")
        self.assertIn("Scanning", view.progress_label.cget("text"))
        self.assertIn("folder", view.progress_label.cget("text"))                  # the folder being read right now
        self.assertEqual(view._stat_labels["Files"].cget("text"), "120")
        self.assertEqual(view._stat_labels["Total size"].cget("text"), "4.0 MB")
        self.assertFalse(view.select_button._enabled)
        scanner.gate.set()
        deadline = time.time() + 5
        while view._scanning and time.time() < deadline:
            pump(self.root, 0.05)
        pump(self.root, 0.2)
        self.assertFalse(view.progressbar.winfo_ismapped())
        self.assertTrue(view.select_button._enabled)

    def test_starting_a_new_scan_cancels_the_old_one_and_ignores_its_result(self):
        view = self.view
        other = self.target.parent / "other"
        write(other / "b.bin", 5)
        view.scan_folder(str(self.target))
        first = ControlledScanner.instances[-1]
        self.wait_started(first)
        view.scan_folder(str(other))                                               # without waiting for the first
        second = ControlledScanner.instances[-1]
        self.assertTrue(first.cancel_event.is_set())                               # no scan keeps running behind the new one
        self.assertFalse(second.cancel_event.is_set())
        self.wait_started(second)
        first.gate.set()                                                           # the old result arrives late...
        pump(self.root, 0.5)
        self.assertTrue(view._scanning)                                            # ...and is ignored
        self.assertNotEqual(getattr(view.last_result, "root", None), str(self.target))
        second.gate.set()
        deadline = time.time() + 5
        while view._scanning and time.time() < deadline:
            pump(self.root, 0.05)
        self.assertEqual(view.last_result.root, str(other))

    def test_stop_cancels_the_running_scan(self):
        view = self.view
        view.scan_folder(str(self.target))
        scanner = ControlledScanner.instances[-1]
        self.wait_started(scanner)
        view._stop_scan()
        self.assertTrue(scanner.cancel_event.is_set())
        self.assertIn("Stopping", view.progress_label.cget("text"))
        scanner.gate.set()

    def test_the_elevation_state_is_shown(self):
        view = self.view
        for elevated, expected in ((True, "administrator"), (False, "Standard user"), (None, "")):
            self.elevated = elevated
            view.scan_folder(str(self.target))
            self.assertIn(expected, view.elevation_label.cget("text"))
            ControlledScanner.instances[-1].gate.set()
            deadline = time.time() + 5
            while view._scanning and time.time() < deadline:
                pump(self.root, 0.05)
        self.assertTrue(view.last_result.elevated is None)


class NotesAndCompareTests(StorageGuiCase):
    def test_unreadable_folders_are_listed_in_a_banner_and_a_details_window(self):
        write(self.target / "ok" / "a.bin", 1000)
        write(self.target / "locked" / "secret.bin", 5000)
        real = os.scandir
        blocked = str(self.target / "locked")

        def scandir(path):
            if str(path) == blocked:
                raise PermissionError("denied")
            return real(path)

        with mock.patch("app.core.tree_scanner.os.scandir", side_effect=scandir):
            result = self.scan()
        view = self.view
        self.assertEqual(result.denied_paths, [blocked])
        self.assertTrue(view.banner.winfo_ismapped())
        self.assertIn("could not be read", view.banner_text.cget("text"))
        locked = self.node("locked")
        view._on_tree_select(locked)
        self.assertEqual(view.detail_panel._rows["Size"].cget("text"), "Unknown (access denied)")
        self.assertIn("Access denied", view.detail_panel._rows["Notes"].cget("text"))
        self.assertIn("⚠", view.tree_panel.tree.item(view.tree_panel._iids[id(locked)], "text"))
        dialog = view.show_scan_details()
        try:
            texts = _texts(dialog)
            self.assertIn(blocked, texts)
            self.assertIn("COULD NOT BE READ", texts)
            self.assertIn("does not bypass Windows security", texts)
        finally:
            dialog.destroy()

    def test_a_clean_scan_shows_no_warning_banner(self):
        write(self.target / "a.bin", 100)
        self.scan()
        self.assertFalse(self.view.banner.winfo_ismapped())

    def test_two_scans_enable_compare_and_the_dialog_reports_what_changed(self):
        write(self.target / "Videos" / "a.mp4", 3 * MB)
        write(self.target / "Docs" / "r.pdf", 1000)
        self.scan()
        self.assertIn("Saved for later comparison", self.view.progress_label.cget("text"))
        self.assertFalse(self.view.compare_button._enabled)                        # only one scan so far
        write(self.target / "Videos" / "b.mp4", 2 * MB)
        self.scan()
        deadline = time.time() + 5
        while not self.view.compare_button._enabled and time.time() < deadline:
            pump(self.root, 0.05)
        self.assertTrue(self.view.compare_button._enabled)
        self.view.last_dialog = None
        self.view._compare_with_previous()
        deadline = time.time() + 5
        while self.view.last_dialog is None and time.time() < deadline:
            pump(self.root, 0.05)
        dialog = self.view.last_dialog
        self.assertIsNotNone(dialog)
        try:
            texts = _texts(dialog)
            self.assertIn("Changes in", texts)
            self.assertIn("+2.0 MB", texts)
            self.assertIn("Videos", texts)
            self.assertIn("shows where space changed, not why", texts)
        finally:
            dialog.destroy()

    def test_with_history_off_nothing_is_saved_and_compare_stays_off(self):
        self.history.set_enabled(False)
        write(self.target / "a.bin", 100)
        self.scan()
        self.scan()
        self.assertNotIn("Saved", self.view.progress_label.cget("text"))
        self.assertFalse(self.view.compare_button._enabled)

    def test_compare_without_an_earlier_scan_says_so(self):
        write(self.target / "a.bin", 100)
        self.scan()
        self.view.compare_button.set_enabled(True)
        self.view._show_comparison(None)
        self.assertIn("no earlier scan", self.view.progress_label.cget("text"))


class TooltipTests(StorageGuiCase):
    def test_hovering_reuses_a_single_tooltip_window(self):
        for i in range(8):
            write(self.target / f"f{i}.bin", 1000 * (i + 1))
        self.scan()
        canvas = self.view.treemap_canvas
        pump(self.root, 0.2)
        rects = list(canvas._rects)
        self.assertGreaterEqual(len(rects), 6)
        for _ in range(8):
            for rect, _node in rects:
                canvas.event_generate("<Motion>", x=int(rect.x + rect.w / 2), y=int(rect.y + rect.h / 2))
                canvas.update()
        tooltips = [w for w in canvas.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(tooltips), 1)                                          # not one window per hover
        canvas.event_generate("<Leave>")
        canvas.update()
        self.assertEqual(tooltips[0].state(), "withdrawn")

    def test_tooltip_text_mentions_hidden_and_denied_items(self):
        node = TreeNode("secret", "/x/secret", 0, True, "Folder", hidden=True, denied=True)
        lines = type(self.view.treemap_canvas)._tip_lines(node)
        self.assertIn("Access denied: size unknown, not zero", lines)
        self.assertIn("Hidden", lines)


def _texts(dialog):
    found = []

    def visit(widget):
        for child in widget.winfo_children():
            if child.winfo_class() == "Text":
                found.append(child.get("1.0", "end"))
            visit(child)

    visit(dialog)
    return "\n".join(found)


# --------------------------------------------------------------------------- #
class TreePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"No display available: {exc}")
        apply_theme(cls.root)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except tk.TclError:
            pass

    def make_tree(self, children=1200):
        files = [TreeNode(f"f{i:04}.bin", f"/r/big/f{i:04}.bin", 10 + i, False, "Other") for i in range(children)]
        files.sort(key=lambda n: n.size, reverse=True)
        big = TreeNode("big", "/r/big", sum(f.size for f in files), True, "Folder", files, dir_count=0, file_count=len(files))
        small = TreeNode("small", "/r/small", 5, True, "Folder", [TreeNode("s.txt", "/r/small/s.txt", 5, False, "Documents")])
        return TreeNode("r", "/r", big.size + small.size, True, "Folder", [big, small])

    def panel(self, tree):
        selected, activated = [], []
        panel = TreePanel(self.root, selected.append, activated.append)
        panel.pack(fill="both", expand=True)
        panel.set_root(tree)
        self.root.update()
        return panel, selected, activated

    def test_rows_are_created_lazily_and_capped(self):
        tree = self.make_tree()
        panel, _s, _a = self.panel(tree)
        self.assertEqual(panel.visible_names(), ["big", "small"])
        big = tree.children[0]
        self.assertEqual(panel.visible_names(big), [])                              # not opened yet: no rows built
        panel.tree.item(panel._iids[id(big)], open=True)
        panel._populate(panel._iids[id(big)])
        self.assertEqual(len(panel.visible_names(big)), MAX_CHILDREN_SHOWN)
        more = [c for c in panel.tree.get_children(panel._iids[id(big)]) if c.startswith("more:")]
        self.assertEqual(len(more), 1)
        self.assertIn("700 more items", panel.tree.item(more[0], "text"))

    def test_percent_of_folder_is_shown(self):
        tree = self.make_tree(children=2)
        panel, _s, _a = self.panel(tree)
        values = panel.tree.item(panel._iids[id(tree.children[0])], "values")
        self.assertEqual(values[0], "21 B")
        self.assertEqual(values[1], "81")                                           # 21 of 26 bytes

    def test_sorting_by_name_and_back_keeps_open_folders_open(self):
        tree = self.make_tree(children=5)
        panel, _s, _a = self.panel(tree)
        big = tree.children[0]
        panel.select_node(big.children[0])
        self.root.update()
        before = panel.visible_names(big)
        panel.sort_by("name")
        self.assertEqual(panel.visible_names(big), sorted(before))                  # A to Z, and still open
        self.assertIs(panel.selected_node(), big.children[0])                       # selection survived the rebuild
        panel.sort_by("name")
        self.assertEqual(panel.visible_names(big), sorted(before, reverse=True))
        panel.sort_by("size")
        self.assertEqual(panel.visible_names(big), before)

    def test_programmatic_selection_is_not_reported_but_a_user_click_is(self):
        tree = self.make_tree(children=5)
        panel, selected, activated = self.panel(tree)
        target = tree.children[0].children[2]
        self.assertTrue(panel.select_node(target))
        self.root.update()
        pump(self.root, 0.1)
        self.assertEqual(selected, [])                                              # our own selection: no echo
        other = tree.children[1]
        panel.tree.selection_set(panel._iids[id(other)])                            # as if the user clicked
        pump(self.root, 0.1)
        self.assertEqual(selected, [other])

    def test_unknown_or_hidden_nodes_cannot_be_selected(self):
        tree = self.make_tree()
        panel, _s, _a = self.panel(tree)
        self.assertFalse(panel.select_node(TreeNode("x", "/elsewhere/x", 1, False, "Other")))
        self.assertFalse(panel.select_node(None))
        beyond_cap = tree.children[0].children[-1]                                  # smallest of 1200: past the 500-row limit
        self.assertFalse(panel.select_node(beyond_cap))

    def test_double_click_reports_folders_with_contents_only(self):
        tree = self.make_tree(children=3)
        panel, _s, activated = self.panel(tree)
        iid = panel._iids[id(tree.children[0])]
        panel.tree.update_idletasks()
        bbox = panel.tree.bbox(iid)
        self.assertTrue(bbox)
        for _ in range(2):  # Tk cannot be sent a synthetic Double-1: two quick real clicks make one
            panel.tree.event_generate("<Button-1>", x=bbox[0] + 20, y=bbox[1] + 5)
            panel.tree.event_generate("<ButtonRelease-1>", x=bbox[0] + 20, y=bbox[1] + 5)
        self.root.update()
        self.assertEqual(activated, [tree.children[0]])

    def test_denied_hidden_and_grouped_rows_are_marked(self):
        locked = TreeNode("locked", "/r/locked", 0, True, "Folder", denied=True)
        hidden = TreeNode(".cache", "/r/.cache", 9, True, "Folder", hidden=True)
        grouped = TreeNode("(5 smaller files)", "", 5, False, "Other", synthetic=True, file_count=5)
        tree = TreeNode("r", "/r", 14, True, "Folder", [hidden, grouped, locked])
        panel, _s, _a = self.panel(tree)
        text = lambda node: panel.tree.item(panel._iids[id(node)], "text")  # noqa: E731
        tags = lambda node: panel.tree.item(panel._iids[id(node)], "tags")  # noqa: E731
        self.assertIn("⚠", text(locked))
        self.assertEqual(panel.tree.item(panel._iids[id(locked)], "values")[0], "?")
        self.assertIn("denied", tags(locked))
        self.assertIn("hidden", tags(hidden))
        self.assertIn("grouped", tags(grouped))

    def test_an_empty_panel_after_clearing(self):
        panel, _s, _a = self.panel(self.make_tree(children=2))
        panel.set_root(None)
        self.assertEqual(panel.visible_names(), [])
        self.assertIsNone(panel.root_node)

    def test_find_chain_agrees_with_what_the_panel_selects(self):
        tree = self.make_tree(children=4)
        node = tree.children[0].children[1]
        self.assertEqual([n.name for n in find_chain(tree, node.path)], ["r", "big", node.name])


if __name__ == "__main__":
    unittest.main()
