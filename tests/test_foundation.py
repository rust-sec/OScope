"""Phase 1 tests: collector base types, platform facade, settings, logging, sampler wiring.

All of this runs on any OS without a display. Anything that needs a real Windows machine
is called out with "WIN-VERIFY" and is covered by docs/WINDOWS_VERIFICATION.md, not here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.collectors.base import Availability, Collector, CollectorContext, Reading  # noqa: E402
from app.collectors.fake import FakeCollector  # noqa: E402
from app.core import platform_ops, report  # noqa: E402
from app.core.process_manager import ProcessManager  # noqa: E402
from app.core.resource_manager import ResourceMonitor  # noqa: E402
from app.core.sampler import Sampler  # noqa: E402
from app.history.settings_store import SETTINGS_FILENAME, AppSettings, SettingsStore  # noqa: E402
from app.utils.logging_setup import LOG_FILENAME, setup_logging  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReadingTests(unittest.TestCase):
    def test_value_only_allowed_when_available(self):
        for status in Availability:
            if status is Availability.AVAILABLE:
                continue
            with self.assertRaises(ValueError):
                Reading("gpu.utilization", status, 42.0)

    def test_available_reading_carries_value(self):
        reading = Reading.available("mem.percent", 61.5, unit="%", source="test")
        self.assertTrue(reading.is_available)
        self.assertEqual(reading.value, 61.5)

    def test_missing_reading_has_no_value(self):
        for status in Availability:
            if status is Availability.AVAILABLE:
                continue
            reading = Reading.missing("temp.cpu", status, detail="reason")
            self.assertIsNone(reading.value)
            self.assertFalse(reading.is_available)

    def test_missing_rejects_available_status(self):
        with self.assertRaises(ValueError):
            Reading.missing("x", Availability.AVAILABLE)

    def test_available_status_without_value_is_not_usable(self):
        self.assertFalse(Reading("x", Availability.AVAILABLE, None).is_available)

    def test_fake_collector_satisfies_protocol(self):
        self.assertIsInstance(FakeCollector(), Collector)


class SamplerWiringTests(unittest.TestCase):
    """Sampler._collect is called directly (no thread), with fake collectors."""

    def _sampler(self, collectors):
        return Sampler(deliver=lambda _snap: None, interval_seconds=2, collectors=collectors, window_size=3)

    def _collect(self, sampler):
        return sampler._collect(ResourceMonitor(), ProcessManager())

    def test_collector_readings_reach_the_snapshot(self):
        reading = Reading.available("fake.value", 7, unit="x")
        sampler = self._sampler([FakeCollector("fake", {"fake.value": reading})])
        snapshot = self._collect(sampler)
        self.assertEqual(snapshot.readings["fake.value"], reading)

    def test_failing_collector_degrades_to_unavailable_and_is_logged(self):
        broken = FakeCollector("broken", error=RuntimeError("boom"))
        healthy = FakeCollector("ok", {"ok.value": Reading.available("ok.value", 1)})
        sampler = self._sampler([broken, healthy])
        with self.assertLogs("oscope.sampler", level="WARNING") as logs:
            snapshot = self._collect(sampler)
        self.assertEqual(snapshot.readings["broken"].status, Availability.UNAVAILABLE)
        self.assertIsNone(snapshot.readings["broken"].value)
        self.assertIn("RuntimeError", snapshot.readings["broken"].detail)
        self.assertTrue(snapshot.readings["ok.value"].is_available)  # others unaffected
        self.assertTrue(any("collector:broken" in line for line in logs.output))

    def test_repeated_failure_is_only_a_warning_once(self):
        sampler = self._sampler([FakeCollector("broken", error=RuntimeError("boom"))])
        with self.assertLogs("oscope.sampler", level="WARNING") as logs:
            self._collect(sampler)
            self._collect(sampler)
            self._collect(sampler)
        self.assertEqual(len([line for line in logs.output if "WARNING" in line]), 1)

    def test_core_metric_failure_is_logged_not_silent(self):
        sampler = self._sampler([])
        resources = ResourceMonitor()
        with mock.patch.object(resources, "cpu_percent", side_effect=OSError("denied")), \
                self.assertLogs("oscope.sampler", level="WARNING") as logs:
            snapshot = sampler._collect(resources, ProcessManager())
        self.assertIsNone(snapshot.cpu_percent)
        self.assertTrue(any("cpu failed" in line for line in logs.output))

    def test_previous_readings_are_passed_to_collectors(self):
        seen = []

        class Recorder:
            key = "recorder"

            def collect(self, ctx: CollectorContext):
                seen.append(dict(ctx.previous))
                return {"recorder.n": Reading.available("recorder.n", len(seen))}

        sampler = self._sampler([Recorder()])
        self._collect(sampler)
        self._collect(sampler)
        self.assertEqual(seen[0], {})
        self.assertEqual(seen[1]["recorder.n"].value, 1)

    def test_ring_buffer_keeps_only_the_latest_samples(self):
        sampler = self._sampler([FakeCollector("fake", {"fake.value": Reading.available("fake.value", 1)})])
        for _ in range(5):
            self._collect(sampler)
        window = sampler.recent_samples()
        self.assertEqual(len(window), 3)  # window_size=3
        self.assertLessEqual(window[0].taken_at, window[-1].taken_at)
        self.assertIn("fake.value", window[-1].readings)

    def test_snapshot_carries_elevation_state(self):
        with mock.patch.object(platform_ops, "is_elevated", return_value=True):
            sampler = self._sampler([])
        self.assertIs(self._collect(sampler).elevated, True)

    def test_default_collectors_never_break_a_snapshot(self):
        snapshot = self._collect(Sampler(deliver=lambda _s: None, interval_seconds=2))
        self.assertIsInstance(snapshot.readings, dict)


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SettingsStore(Path(tmp))
            self.assertTrue(store.save(AppSettings(refresh_interval=5, large_file_mb=120)))
            loaded = SettingsStore(Path(tmp)).load()
        self.assertEqual((loaded.refresh_interval, loaded.large_file_mb), (5, 120))

    def test_missing_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(SettingsStore(Path(tmp)).load(), AppSettings())

    def test_corrupt_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / SETTINGS_FILENAME).write_text("{not json", encoding="utf-8")
            with self.assertLogs("oscope.settings", level="WARNING"):
                self.assertEqual(SettingsStore(Path(tmp)).load(), AppSettings())

    def test_invalid_values_fall_back_per_field(self):
        stored = {"refresh_interval": 7, "large_file_mb": -3}  # 7 is not an offered choice
        self.assertEqual(AppSettings.from_dict(stored), AppSettings())
        mixed = AppSettings.from_dict({"refresh_interval": 10, "large_file_mb": "lots"})
        self.assertEqual(mixed.refresh_interval, 10)
        self.assertEqual(mixed.large_file_mb, AppSettings().large_file_mb)

    def test_wrong_types_are_ignored(self):
        self.assertEqual(AppSettings.from_dict(["not", "a", "dict"]), AppSettings())
        self.assertEqual(AppSettings.from_dict({"refresh_interval": True}), AppSettings())

    def test_without_a_data_folder_nothing_persists_and_nothing_raises(self):
        store = SettingsStore(None)
        self.assertFalse(store.save(AppSettings()))
        self.assertEqual(store.load(), AppSettings())

    def test_save_failure_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SettingsStore(Path(tmp) / "does-not-exist")
            with self.assertLogs("oscope.settings", level="WARNING"):
                self.assertFalse(store.save(AppSettings()))

    def test_saved_file_is_plain_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            SettingsStore(Path(tmp)).save(AppSettings(refresh_interval=3))
            data = json.loads((Path(tmp) / SETTINGS_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(data["refresh_interval"], 3)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], [SETTINGS_FILENAME])  # no temp leftovers


class PlatformOpsTests(unittest.TestCase):
    def test_windows_data_dir_is_local_appdata(self):
        home = Path("/home/u")
        candidates = platform_ops.data_dir_candidates(True, {"LOCALAPPDATA": "/appdata/Local"}, home)
        self.assertEqual(candidates, [Path("/appdata/Local") / "OScope", home / ".oscope"])

    def test_non_windows_uses_home_dot_oscope(self):
        home = Path("/home/u")
        self.assertEqual(platform_ops.data_dir_candidates(False, {"LOCALAPPDATA": "/x"}, home), [home / ".oscope"])

    def test_env_override_wins(self):
        home = Path("/home/u")
        candidates = platform_ops.data_dir_candidates(True, {platform_ops.DATA_DIR_ENV_VAR: "/custom"}, home)
        self.assertEqual(candidates[0], Path("/custom"))

    def test_app_data_dir_creates_and_returns_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "data"
            with mock.patch.dict(os.environ, {platform_ops.DATA_DIR_ENV_VAR: str(target)}):
                self.assertEqual(platform_ops.app_data_dir(), target)
            self.assertTrue(target.is_dir())

    def test_app_data_dir_falls_back_when_override_is_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "a-file"
            blocker.write_text("x")
            with mock.patch.dict(os.environ, {platform_ops.DATA_DIR_ENV_VAR: str(blocker / "sub")}), \
                    mock.patch.object(platform_ops.Path, "home", return_value=Path(tmp)):
                self.assertEqual(platform_ops.app_data_dir(), Path(tmp) / ".oscope")

    def test_app_data_dir_none_when_nothing_works(self):
        with mock.patch.object(platform_ops, "data_dir_candidates", return_value=[]):
            self.assertIsNone(platform_ops.app_data_dir())

    def test_is_reparse_point_false_for_ordinary_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "plain.txt").write_text("x")
            (Path(tmp) / "folder").mkdir()
            with os.scandir(tmp) as entries:
                self.assertFalse(any(platform_ops.is_reparse_point(entry) for entry in entries))

    def test_is_reparse_point_reads_the_windows_attribute(self):
        class FakeStat:
            st_file_attributes = 0x10 | 0x400  # directory + reparse point

        entry = mock.Mock()
        entry.stat.return_value = FakeStat()
        self.assertTrue(platform_ops.is_reparse_point(entry))
        FakeStat.st_file_attributes = 0x10
        self.assertFalse(platform_ops.is_reparse_point(entry))

    def test_open_path_is_false_without_startfile(self):
        with mock.patch.object(platform_ops.os, "startfile", create=True, new=None):
            self.assertFalse(platform_ops.open_path("C:\\"))

    def test_open_path_swallows_errors(self):
        with mock.patch.object(platform_ops.os, "startfile", create=True, side_effect=OSError("nope")):
            self.assertFalse(platform_ops.open_path("C:\\missing"))

    def test_elevation_unknown_off_windows(self):
        if sys.platform == "win32":
            self.skipTest("only meaningful on non-Windows")
        self.assertIsNone(platform_ops.is_elevated())  # WIN-VERIFY the True/False path on Windows


class ReportLocationTests(unittest.TestCase):
    def test_default_reports_dir_lives_in_app_data(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {platform_ops.DATA_DIR_ENV_VAR: tmp}):
            self.assertEqual(report.default_reports_dir(), Path(tmp) / "reports")

    def test_save_report_uses_default_dir_when_none_given(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {platform_ops.DATA_DIR_ENV_VAR: tmp}), \
                mock.patch.object(report, "REPORTS_DIR", None):
            path = report.save_report("hello")
            self.assertEqual(path.parent, Path(tmp) / "reports")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello")

    def test_default_reports_dir_falls_back_to_project_folder(self):
        with mock.patch.object(platform_ops, "app_data_dir", return_value=None):
            self.assertEqual(report.default_reports_dir(), PROJECT_ROOT / "reports")


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("oscope")
        self._saved = (list(self.logger.handlers), self.logger.level, self.logger.propagate)
        self.logger.handlers.clear()

    def tearDown(self):
        for handler in self.logger.handlers:
            handler.close()
        self.logger.handlers[:] = self._saved[0]
        self.logger.setLevel(self._saved[1])
        self.logger.propagate = self._saved[2]

    def test_writes_to_a_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = setup_logging(Path(tmp))
            logging.getLogger("oscope.test").warning("hello log")
            for handler in logger.handlers:
                handler.flush()
            text = (Path(tmp) / LOG_FILENAME).read_text(encoding="utf-8")
            for handler in logger.handlers:
                handler.close()
        self.assertIn("hello log", text)

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup_logging(Path(tmp))
            setup_logging(Path(tmp))
            self.assertEqual(len(logging.getLogger("oscope").handlers), 1)

    def test_without_a_folder_it_stays_silent(self):
        with mock.patch.object(platform_ops, "app_data_dir", return_value=None):
            logger = setup_logging()
        self.assertTrue(all(isinstance(h, logging.NullHandler) for h in logger.handlers))


class OsIsolationTests(unittest.TestCase):
    """OS-specific checks may only appear in the modules whose job is to hold them."""

    ALLOWED = {
        "app/core/platform_ops.py",
        "app/core/platform.py",
        "app/core/windows_backend.py",
    }
    FORBIDDEN = re.compile(r"os\.name|startfile|0x400\b|SystemDrive|sys\.platform|\bctypes\b|\bwinreg\b")

    def test_os_specific_code_stays_in_its_layer(self):
        offenders = []
        for path in (PROJECT_ROOT / "app").rglob("*.py"):
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            # app/collectors/windows/ is the other layer allowed to touch Windows interfaces (Phase 2)
            if relative in self.ALLOWED or relative.startswith("app/collectors/windows/"):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if self.FORBIDDEN.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()}")
        self.assertEqual(offenders, [], "OS-specific code outside platform_ops/windows_backend:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
