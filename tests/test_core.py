"""Automated tests for the OS-independent logic of OScope (no GUI needed).

Run from the project folder:   python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import diagnostics, report, windows_backend  # noqa: E402
from app.core import platform as oscope_platform  # noqa: E402
from app.core.process_manager import ProcessManager  # noqa: E402
from app.core.resource_manager import ResourceMonitor  # noqa: E402
from app.core.sampler import Snapshot  # noqa: E402
from app.core.storage_manager import DirectoryScanner, get_drive_usage, system_drive_path  # noqa: E402
from app.core.system_info import StaticInfo, get_static_info  # noqa: E402
from app.core.tree_scanner import TreeScanner  # noqa: E402
from app.utils import constants as c  # noqa: E402
from app.utils import file_categories  # noqa: E402
from app.utils.formatting import (  # noqa: E402
    format_bytes,
    format_duration,
    format_used_of_total,
)

MB = 1024 * 1024


class FormattingTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(6657193984), "6.2 GB")
        self.assertEqual(format_bytes(820 * MB), "820 MB")

    def test_used_of_total(self):
        gb = 1024 ** 3
        self.assertEqual(format_used_of_total(int(6.1 * gb), 8 * gb), "6.1 / 8.0 GB")
        self.assertEqual(format_used_of_total(391 * gb, 512 * gb), "391 / 512 GB")

    def test_duration(self):
        self.assertEqual(format_duration(4 * 3600 + 32 * 60), "4h 32m")
        self.assertEqual(format_duration(90000), "1d 1h 0m")
        self.assertEqual(format_duration(45), "45s")


class DiagnosticsTests(unittest.TestCase):
    def test_normal(self):
        result = diagnostics.evaluate(10, 40, 50)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["level"], "normal")

    def test_thresholds_come_from_constants(self):
        self.assertEqual(diagnostics.evaluate(c.CPU_HIGH_PERCENT, 0, 0)[0]["title"], "High CPU Usage")
        self.assertEqual(diagnostics.evaluate(0, c.MEMORY_HIGH_PERCENT, 0)[0]["title"], "High Memory Usage")
        self.assertEqual(diagnostics.evaluate(0, 0, c.STORAGE_LOW_PERCENT)[0]["title"], "Low Storage")
        self.assertEqual(diagnostics.evaluate(0, 0, c.STORAGE_FULL_PERCENT)[0]["title"], "Storage Getting Full")

    def test_just_below_threshold_is_normal(self):
        self.assertEqual(diagnostics.evaluate(c.CPU_HIGH_PERCENT - 0.1, 0, 0)[0]["level"], "normal")

    def test_critical_and_ordering(self):
        result = diagnostics.evaluate(99, 90, 85)
        self.assertEqual(result[0]["level"], "critical")
        self.assertEqual(diagnostics.worst_level(result), "critical")
        levels = [f["level"] for f in result]
        self.assertEqual(levels, sorted(levels, key=lambda lv: diagnostics.LEVEL_ORDER[lv], reverse=True))

    def test_unavailable_metrics_are_skipped_not_guessed(self):
        self.assertEqual(diagnostics.evaluate(None, None, None)[0]["level"], "normal")

    def test_finding_shape(self):
        for finding in diagnostics.evaluate(99, 99, 99):
            self.assertEqual(set(finding), {"level", "title", "message"})


class PlatformTests(unittest.TestCase):
    def test_windows_is_supported(self):
        with mock.patch.object(oscope_platform.sys, "platform", "win32"):
            self.assertTrue(oscope_platform.is_supported())
            self.assertEqual(oscope_platform.platform_label(), "Windows")

    def test_other_os_unsupported_without_dev_switch(self):
        with mock.patch.object(oscope_platform.sys, "platform", "linux"), mock.patch.dict(
            os.environ, {}, clear=False
        ):
            os.environ.pop(oscope_platform.DEV_ENV_VAR, None)
            self.assertFalse(oscope_platform.is_supported())
        self.assertIn("Unsupported operating system", oscope_platform.UNSUPPORTED_MESSAGE)


class SystemInfoTests(unittest.TestCase):
    def test_static_info_never_raises(self):
        info = get_static_info()
        self.assertTrue(info.hostname)
        self.assertTrue(info.os_name)

    def test_memory_and_cpu(self):
        monitor = ResourceMonitor()
        memory = monitor.memory()
        self.assertIsNotNone(memory)
        self.assertGreater(memory.total, 0)
        self.assertEqual(memory.used + memory.available, memory.total)
        self.assertTrue(0 <= memory.percent <= 100)
        cpu = monitor.cpu_percent()
        self.assertTrue(cpu is None or 0 <= cpu <= 100)

    def test_drive_usage(self):
        usage = get_drive_usage(system_drive_path())
        self.assertIsNotNone(usage)
        self.assertEqual(usage.used + usage.free, usage.total)
        self.assertTrue(0 <= usage.percent <= 100)

    def test_bad_drive_returns_none(self):
        self.assertIsNone(get_drive_usage(os.path.join(tempfile.gettempdir(), "definitely", "not", "here")))


class ProcessTests(unittest.TestCase):
    def test_list_contains_this_process(self):
        manager = ProcessManager()
        processes = manager.list_processes()
        pids = {p.pid for p in processes}
        self.assertIn(os.getpid(), pids)
        self.assertNotIn(0, pids)
        for proc in processes:
            self.assertTrue(0 <= proc.cpu_percent <= 100)

    def test_details_for_this_process(self):
        details = dict(ProcessManager().get_details(os.getpid()))
        self.assertIn("Executable", details)
        self.assertIn("Threads", details)

    def test_details_for_missing_process(self):
        details = ProcessManager().get_details(999999999)
        self.assertEqual(details[0][1], "This process is no longer running.")


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "big").mkdir()
        (self.root / "small" / "nested").mkdir(parents=True)
        (self.root / "big" / "a.bin").write_bytes(b"x" * (3 * MB))
        (self.root / "big" / "b.bin").write_bytes(b"x" * (1 * MB))
        (self.root / "small" / "nested" / "c.txt").write_bytes(b"x" * 1000)
        (self.root / "top.txt").write_bytes(b"x" * 500)

    def tearDown(self):
        self._tmp.cleanup()

    def test_counts_and_sizes(self):
        result = DirectoryScanner(str(self.root)).scan()
        self.assertIsNone(result.error)
        self.assertEqual(result.file_count, 4)
        self.assertEqual(result.dir_count, 3)  # big, small, small/nested
        self.assertEqual(result.total_size, 4 * MB + 1500)
        self.assertEqual(result.largest_dirs[0][0], "big")
        self.assertEqual(result.largest_dirs[0][2], 4 * MB)

    def test_large_file_detection_sorted(self):
        result = DirectoryScanner(str(self.root)).scan()
        self.assertEqual([name for name, _, _ in result.large_files[:2]], ["a.bin", "b.bin"])
        over_2mb = [f for f in result.large_files if f[2] >= 2 * MB]
        self.assertEqual([f[0] for f in over_2mb], ["a.bin"])

    def test_nonexistent_directory(self):
        result = DirectoryScanner(str(self.root / "nope")).scan()
        self.assertIsNotNone(result.error)
        self.assertEqual(result.file_count, 0)

    def test_file_instead_of_directory(self):
        result = DirectoryScanner(str(self.root / "top.txt")).scan()
        self.assertIsNotNone(result.error)

    def test_permission_denied_is_counted_and_scan_continues(self):
        real_scandir = os.scandir
        blocked = str(self.root / "small")

        def fake_scandir(path):
            if str(path) == blocked:
                raise PermissionError("denied")
            return real_scandir(path)

        with mock.patch("app.core.storage_manager.os.scandir", side_effect=fake_scandir):
            result = DirectoryScanner(str(self.root)).scan()
        self.assertEqual(result.denied_count, 1)
        self.assertEqual(result.total_size, 4 * MB + 500)  # everything except the blocked folder
        self.assertIsNone(result.error)

    def test_file_vanishing_mid_scan_does_not_crash(self):
        with mock.patch(
            "app.core.storage_manager.DirectoryScanner._record_file", side_effect=FileNotFoundError("gone")
        ):
            result = DirectoryScanner(str(self.root)).scan()
        self.assertGreater(result.skipped_count, 0)

    def test_cancel(self):
        cancel = threading.Event()
        cancel.set()
        result = DirectoryScanner(str(self.root), cancel_event=cancel).scan()
        self.assertTrue(result.cancelled)

    def test_only_n_largest_files_kept(self):
        result = DirectoryScanner(str(self.root), max_large_files=2).scan()
        self.assertEqual(len(result.large_files), 2)
        self.assertEqual(result.large_files[0][0], "a.bin")

    def test_progress_callback(self):
        seen = []
        DirectoryScanner(str(self.root), progress=lambda f, d: seen.append((f, d))).scan()
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], 4)

    def test_scan_does_not_modify_anything(self):
        before = sorted((p.relative_to(self.root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                        for p in self.root.rglob("*"))
        DirectoryScanner(str(self.root)).scan()
        after = sorted((p.relative_to(self.root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                       for p in self.root.rglob("*"))
        self.assertEqual(before, after)


class TreeScannerTests(unittest.TestCase):
    """TreeScanner must agree with DirectoryScanner on every aggregate field,
    while also building a full nested tree for the Storage Analyzer treemap."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "big").mkdir()
        (self.root / "small" / "nested").mkdir(parents=True)
        (self.root / "big" / "a.bin").write_bytes(b"x" * (3 * MB))
        (self.root / "big" / "b.bin").write_bytes(b"x" * (1 * MB))
        (self.root / "small" / "nested" / "c.txt").write_bytes(b"x" * 1000)
        (self.root / "top.txt").write_bytes(b"x" * 500)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tree_matches_directory_scanner_aggregates(self):
        base = DirectoryScanner(str(self.root)).scan()
        tree_result = TreeScanner(str(self.root)).scan()
        self.assertEqual(tree_result.total_size, base.total_size)
        self.assertEqual(tree_result.file_count, base.file_count)
        self.assertEqual(tree_result.dir_count, base.dir_count)
        self.assertEqual(tree_result.largest_dirs, base.largest_dirs)
        self.assertEqual(tree_result.large_files, base.large_files)

    def test_tree_structure_and_sizes(self):
        result = TreeScanner(str(self.root)).scan()
        self.assertIsNotNone(result.tree)
        self.assertEqual(result.tree.size, result.total_size)
        self.assertEqual(result.tree.size, 4 * MB + 1500)

        by_name = {child.name: child for child in result.tree.children}
        self.assertEqual(set(by_name), {"big", "small", "top.txt"})
        self.assertTrue(by_name["big"].is_dir)
        self.assertEqual(by_name["big"].size, 4 * MB)
        self.assertFalse(by_name["top.txt"].is_dir)
        self.assertEqual(by_name["top.txt"].size, 500)

        big_children = {c.name: c for c in by_name["big"].children}
        self.assertEqual(big_children["a.bin"].size, 3 * MB)
        self.assertEqual(big_children["b.bin"].size, 1 * MB)

        small = by_name["small"]
        self.assertEqual(len(small.children), 1)
        nested = small.children[0]
        self.assertEqual(nested.name, "nested")
        self.assertEqual(nested.size, 1000)
        self.assertEqual(nested.children[0].name, "c.txt")

    def test_tree_children_sorted_descending(self):
        result = TreeScanner(str(self.root)).scan()
        sizes = [child.size for child in result.tree.children]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        big = next(child for child in result.tree.children if child.name == "big")
        big_sizes = [child.size for child in big.children]
        self.assertEqual(big_sizes, sorted(big_sizes, reverse=True))

    def test_tree_node_cap_truncates_but_aggregates_stay_correct(self):
        result = TreeScanner(str(self.root), max_tree_nodes=2).scan()
        self.assertTrue(result.tree_truncated)
        self.assertEqual(result.total_size, 4 * MB + 1500)
        self.assertEqual(result.file_count, 4)

    def test_cancel_during_tree_build_returns_partial_tree_without_crash(self):
        cancel = threading.Event()
        cancel.set()
        result = TreeScanner(str(self.root), cancel_event=cancel).scan()
        self.assertTrue(result.cancelled)
        self.assertIsNotNone(result.tree)  # structurally valid, just empty/partial

    def test_permission_denied_during_tree_build_is_counted(self):
        real_scandir = os.scandir
        blocked = str(self.root / "small")

        def fake_scandir(path):
            if str(path) == blocked:
                raise PermissionError("denied")
            return real_scandir(path)

        with mock.patch("app.core.tree_scanner.os.scandir", side_effect=fake_scandir):
            result = TreeScanner(str(self.root)).scan()
        self.assertEqual(result.denied_count, 1)
        self.assertEqual(result.total_size, 4 * MB + 500)  # everything except the blocked folder
        self.assertIsNone(result.error)

    def test_scan_does_not_modify_anything(self):
        before = sorted((p.relative_to(self.root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                        for p in self.root.rglob("*"))
        TreeScanner(str(self.root)).scan()
        after = sorted((p.relative_to(self.root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                       for p in self.root.rglob("*"))
        self.assertEqual(before, after)


class FileCategoryTests(unittest.TestCase):
    def test_category_for_known_and_unknown_extensions(self):
        self.assertEqual(file_categories.category_for("photo.JPG", False), "Images")
        self.assertEqual(file_categories.category_for("video.mp4", False), "Videos")
        self.assertEqual(file_categories.category_for("weird.xyz123", False), "Other")
        self.assertEqual(file_categories.category_for("anything", True), file_categories.FOLDER_CATEGORY)

    def test_category_colors_cover_every_category(self):
        expected = set(file_categories.CATEGORY_NAMES) | {file_categories.FOLDER_CATEGORY}
        self.assertEqual(set(c.CATEGORY_COLORS), expected)


class WindowsBackendTests(unittest.TestCase):
    """Parsing / structure checks for the Windows-only code. These run on any OS."""

    def test_memorystatusex_matches_win32_size(self):
        import ctypes

        self.assertEqual(ctypes.sizeof(windows_backend._MemoryStatusEx), 64)  # MEMORYSTATUSEX is 64 bytes

    def test_tasklist_rows_parsed_by_position(self):
        rows = {
            "/V": [["chrome.exe", "4212", "Console", "1", "820,000 K", "Running", "PC\\user", "0:01:23", "Google Chrome"]],
            "/SVC": [["chrome.exe", "4212", "N/A"]],
        }
        with mock.patch.object(windows_backend, "_run_tasklist", side_effect=lambda args: rows[args[0]]):
            result = windows_backend.query_tasklist(4212)
        self.assertEqual(result, {"Session": "Console", "Window title": "Google Chrome"})

    def test_tasklist_services_and_missing_window_title(self):
        rows = {
            "/V": [["svchost.exe", "1048", "Services", "0", "9,000 K", "Running", "NT AUTHORITY\\SYSTEM", "0:00:10", "N/A"]],
            "/SVC": [["svchost.exe", "1048", "Appinfo,Schedule"]],
        }
        with mock.patch.object(windows_backend, "_run_tasklist", side_effect=lambda args: rows[args[0]]):
            result = windows_backend.query_tasklist(1048)
        self.assertEqual(result["Hosted services"], "Appinfo,Schedule")
        self.assertNotIn("Window title", result)

    def test_tasklist_no_match_returns_empty(self):
        with mock.patch.object(windows_backend, "_run_tasklist", return_value=[["INFO: No tasks are running"]]):
            self.assertEqual(windows_backend.query_tasklist(1), {})

    def test_windows_only_calls_are_safe_elsewhere(self):
        if sys.platform == "win32":
            self.skipTest("only meaningful on non-Windows")
        self.assertIsNone(windows_backend.get_windows_version())
        self.assertIsNone(windows_backend.get_cpu_name())
        self.assertIsNone(windows_backend.get_memory_status())
        self.assertIsNone(windows_backend.get_uptime_seconds())
        self.assertEqual(windows_backend.query_tasklist(1), {})
        self.assertFalse(windows_backend.reveal_in_explorer("C:\\nope.txt"))

    def test_reveal_in_explorer_launches_explorer_with_select(self):
        if sys.platform != "win32":
            self.skipTest("only meaningful on Windows")
        with mock.patch("app.core.windows_backend.subprocess.Popen") as popen:
            self.assertTrue(windows_backend.reveal_in_explorer(r"C:\Users\satya\file.txt"))
        popen.assert_called_once()
        command = popen.call_args[0][0]
        self.assertEqual(command, r'explorer /select,"C:\Users\satya\file.txt"')

    def test_reveal_in_explorer_quotes_only_the_path_when_it_has_spaces(self):
        # Regression test: explorer's /select, switch needs the quote right
        # after the comma. Passing ["explorer", "/select," + path] as an argv
        # list let Python's own quoting wrap the whole token (quote landing
        # before "/select") whenever the path contained a space, so explorer
        # silently opened a default location instead of the target.
        if sys.platform != "win32":
            self.skipTest("only meaningful on Windows")
        with mock.patch("app.core.windows_backend.subprocess.Popen") as popen:
            windows_backend.reveal_in_explorer(r"C:\Users\satya\My Documents\report.txt")
        command = popen.call_args[0][0]
        self.assertEqual(command, r'explorer /select,"C:\Users\satya\My Documents\report.txt"')
        self.assertNotIn('"/select,', command)  # the quote must not land before /select

    def test_reveal_in_explorer_never_raises_on_subprocess_failure(self):
        if sys.platform != "win32":
            self.skipTest("only meaningful on Windows")
        with mock.patch("app.core.windows_backend.subprocess.Popen", side_effect=OSError("boom")):
            self.assertFalse(windows_backend.reveal_in_explorer(r"C:\anything.txt"))

    def test_system_drive_uses_environment_on_windows(self):
        from app.core import storage_manager

        with mock.patch.object(storage_manager.os, "name", "nt"), mock.patch.dict(os.environ, {"SystemDrive": "D:"}):
            self.assertEqual(storage_manager.system_drive_path(), "D:\\")


class EntryPointTests(unittest.TestCase):
    def test_unsupported_os_shows_message_and_exits_cleanly(self):
        import main

        with mock.patch.object(oscope_platform.sys, "platform", "linux"), mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(main, "_fail", return_value=1) as fail:
            os.environ.pop(oscope_platform.DEV_ENV_VAR, None)
            self.assertEqual(main.main(), 1)
        fail.assert_called_once()
        self.assertIn("Unsupported operating system", fail.call_args[0][0])


class ReportTests(unittest.TestCase):
    def _snapshot(self):
        manager = ProcessManager()
        monitor = ResourceMonitor()
        return Snapshot(
            taken_at=datetime.now(),
            cpu_percent=42.0,
            memory=monitor.memory(),
            storage=get_drive_usage(system_drive_path()),
            uptime_seconds=16320,
            processes=manager.list_processes(),
            findings=diagnostics.evaluate(42, 76, 81),
        )

    def test_report_text_and_file(self):
        info = StaticInfo("Windows 11", "Pro, version 23H2", "DESKTOP-TEST", "Test CPU", 8, 4)
        text = report.build_report(info, self._snapshot(), now=datetime(2026, 9, 29, 13, 45, 22))
        for heading in ("OSCOPE SYSTEM REPORT", "OPERATING SYSTEM", "SYSTEM HEALTH", "MEMORY",
                        "TOP PROCESSES", "STORAGE", "DIAGNOSTIC SUMMARY"):
            self.assertIn(heading, text)
        self.assertIn("2026-09-29 13:45:22", text)
        self.assertIn("Hostname: DESKTOP-TEST", text)
        self.assertIn("CPU Usage: 42%", text)

        with tempfile.TemporaryDirectory() as tmp:
            path = report.save_report(text, Path(tmp), now=datetime(2026, 9, 29, 13, 45, 22))
            self.assertEqual(path.name, "oscope_report_2026-09-29_134522.txt")
            self.assertEqual(path.read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
