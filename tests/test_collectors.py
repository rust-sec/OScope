"""Phase 2 tests: evidence collectors, parsers, availability mapping, process grouping.

IMPORTANT: the sample outputs below (typeperf CSV, PowerShell lines, registry values) are
written by hand from the documented formats. They prove OScope's parsing and its honesty
rules; they do NOT prove what a real Windows PC prints. Real behaviour is checked with
``python main.py --probe`` and docs/WINDOWS_VERIFICATION.md ("WIN-VERIFY").
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.grouping import BROWSER_KEYS, group_processes  # noqa: E402
from app.collectors import labels  # noqa: E402
from app.collectors.base import Availability, CollectorContext, Reading  # noqa: E402
from app.collectors.disk_activity import DiskActivityCollector  # noqa: E402
from app.collectors.fake import FakeCollector  # noqa: E402
from app.collectors.probe import run_probe  # noqa: E402
from app.collectors.registry import build_default_collectors  # noqa: E402
from app.collectors.windows import gpu, power, thermal, typeperf  # noqa: E402
from app.collectors.windows.background import BackgroundProbe, ProbeError, ProbeOutcome  # noqa: E402
from app.collectors.windows.cmd import RC_LAUNCH_FAILED, RC_TIMEOUT, run_command  # noqa: E402
from app.collectors.windows.memory import MemoryCollector  # noqa: E402
from app.collectors.windows.startup import StartupCollector, approved_state  # noqa: E402
from app.collectors.windows.winreg_reader import RegistryReader  # noqa: E402
from app.core import windows_backend  # noqa: E402
from app.core.process_manager import ProcessInfo  # noqa: E402

CTX = CollectorContext()


class StaticSource:
    """A stand-in for a BackgroundProbe: always returns the same outcome."""

    def __init__(self, value=None, error=None, pending=False):
        self._outcome = None if pending else ProbeOutcome(value, error, 0.0)

    def latest(self):
        return self._outcome


# --------------------------------------------------------------------------- #
# typeperf parsing and the counter reader
# --------------------------------------------------------------------------- #
PAGES_CSV = (
    '"(PDH-CSV 4.0)","\\\\DESKTOP-1\\Memory\\Pages/sec"\r\n'
    '"10/05/2026 12:00:00.123","12.345678"\r\n'
    "The command completed successfully.\r\n"
)
GPU_CSV = (
    '"(PDH-CSV 4.0)",'
    '"\\\\PC\\GPU Engine(pid_100_luid_0x00000000_0x0000F2A1_phys_0_eng_0_engtype_3D)\\Utilization Percentage",'
    '"\\\\PC\\GPU Engine(pid_200_luid_0x00000000_0x0000F2A1_phys_0_eng_0_engtype_3D)\\Utilization Percentage",'
    '"\\\\PC\\GPU Engine(pid_100_luid_0x00000000_0x0000F2A1_phys_0_eng_3_engtype_VideoEncode)\\Utilization Percentage"\r\n'
    '"10/05/2026 12:00:00.123","30.5","20.25","9.0"\r\n'
    "The command completed successfully.\r\n"
)


class TypeperfParsingTests(unittest.TestCase):
    def test_parses_value_and_strips_host(self):
        sample = typeperf.parse_typeperf_csv(PAGES_CSV)
        self.assertEqual(sample.values, {"\\Memory\\Pages/sec": 12.345678})

    def test_bom_and_blank_lines_tolerated(self):
        sample = typeperf.parse_typeperf_csv("\ufeff\r\n" + PAGES_CSV + "\r\n\r\n")
        self.assertIn("\\Memory\\Pages/sec", sample.values)

    def test_error_text_yields_no_values_and_keeps_message(self):
        sample = typeperf.parse_typeperf_csv("Error:\r\nNo valid counters.\r\n")
        self.assertEqual(sample.values, {})
        self.assertIn("No valid counters", sample.message)

    def test_blank_cell_is_skipped_not_zero(self):
        text = '"(PDH-CSV 4.0)","\\\\PC\\A\\x","\\\\PC\\A\\y"\r\n"t","","5"\r\n'
        self.assertEqual(typeperf.parse_typeperf_csv(text).values, {"\\A\\y": 5.0})

    def test_header_without_data_row(self):
        sample = typeperf.parse_typeperf_csv('"(PDH-CSV 4.0)","\\\\PC\\A\\x"\r\n')
        self.assertEqual(sample.values, {})

    def test_empty_text(self):
        self.assertEqual(typeperf.parse_typeperf_csv("").values, {})


class CounterReaderTests(unittest.TestCase):
    def _reader(self, code, output=""):
        return typeperf.make_counter_reader(r"\Memory\Pages/sec", lambda args, timeout: (code, output))

    def test_success(self):
        self.assertEqual(self._reader(0, PAGES_CSV)().values["\\Memory\\Pages/sec"], 12.345678)

    def test_command_line_has_no_shell_and_one_sample(self):
        seen = {}

        def runner(args, timeout):
            seen["args"], seen["timeout"] = list(args), timeout
            return 0, PAGES_CSV

        typeperf.make_counter_reader(r"\Memory\Pages/sec", runner)()
        self.assertEqual(seen["args"], ["typeperf", r"\Memory\Pages/sec", "-sc", "1"])
        self.assertGreater(seen["timeout"], 0)

    def test_launch_failure_and_timeout_raise_probe_error(self):
        with self.assertRaisesRegex(ProbeError, "could not be started"):
            self._reader(RC_LAUNCH_FAILED)()
        with self.assertRaisesRegex(ProbeError, "timed out"):
            self._reader(RC_TIMEOUT)()

    def test_garbage_output_raises_probe_error_mentioning_language(self):
        with self.assertRaisesRegex(ProbeError, "language-specific"):
            self._reader(0, "Error: The specified counter was not found.")()


class RunCommandTests(unittest.TestCase):
    def test_missing_program_is_a_return_code_not_an_exception(self):
        self.assertEqual(run_command(["definitely-not-a-real-program-xyz"]), (RC_LAUNCH_FAILED, ""))

    def test_runs_a_real_program_without_a_shell(self):
        code, output = run_command([sys.executable, "-c", "print('hi')"], timeout=20)
        self.assertEqual(code, 0)
        self.assertIn("hi", output)

    def test_timeout_is_reported(self):
        code, _ = run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
        self.assertEqual(code, RC_TIMEOUT)


# --------------------------------------------------------------------------- #
# BackgroundProbe
# --------------------------------------------------------------------------- #
class BackgroundProbeTests(unittest.TestCase):
    def test_refresh_stores_value(self):
        probe = BackgroundProbe(lambda: 42, 60, autostart=False)
        self.assertIsNone(probe.latest())  # nothing yet, and it does not block
        outcome = probe.refresh()
        self.assertEqual((outcome.value, outcome.error), (42, None))
        self.assertEqual(probe.latest().value, 42)

    def test_probe_error_message_is_kept(self):
        def fail():
            raise ProbeError("tool missing")

        outcome = BackgroundProbe(fail, 60, autostart=False).refresh()
        self.assertIsNone(outcome.value)
        self.assertEqual(outcome.error, "tool missing")

    def test_unexpected_exception_is_contained_and_logged(self):
        def boom():
            raise RuntimeError("boom")

        probe = BackgroundProbe(boom, 60, name="t", autostart=False)
        with self.assertLogs("oscope.probe", level="WARNING"):
            outcome = probe.refresh()
        self.assertEqual(outcome.error, "RuntimeError")

    def test_background_thread_produces_a_result_without_blocking_the_caller(self):
        release = threading.Event()

        def slow():
            release.wait(5)
            return "done"

        probe = BackgroundProbe(slow, 60, name="slowtest")
        started = time.monotonic()
        self.assertIsNone(probe.latest())               # returns at once although the probe is blocked
        self.assertLess(time.monotonic() - started, 1.0)
        release.set()
        deadline = time.time() + 5
        while probe.latest() is None and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(probe.latest().value, "done")
        probe.close()


# --------------------------------------------------------------------------- #
# GPU
# --------------------------------------------------------------------------- #
class GpuTests(unittest.TestCase):
    def test_processes_on_one_engine_add_up_and_busiest_engine_wins(self):
        sample = typeperf.parse_typeperf_csv(GPU_CSV)
        percent, engine = gpu.busiest_engine(sample)
        self.assertAlmostEqual(percent, 50.75)  # 30.5 + 20.25 on the 3D engine, not the 9.0 encode engine
        self.assertEqual(engine, "3D")

    def test_total_is_capped_at_100(self):
        sample = typeperf.CounterSample({
            "\\GPU Engine(pid_1_luid_0x0_0x1_phys_0_eng_0_engtype_3D)\\Utilization Percentage": 80.0,
            "\\GPU Engine(pid_2_luid_0x0_0x1_phys_0_eng_0_engtype_3D)\\Utilization Percentage": 70.0,
        })
        self.assertEqual(gpu.busiest_engine(sample)[0], 100.0)

    def test_engines_on_different_gpus_are_not_mixed(self):
        sample = typeperf.CounterSample({
            "\\GPU Engine(pid_1_luid_0x0_0xA_phys_0_eng_0_engtype_3D)\\Utilization Percentage": 40.0,
            "\\GPU Engine(pid_1_luid_0x0_0xB_phys_0_eng_0_engtype_3D)\\Utilization Percentage": 45.0,
        })
        self.assertEqual(gpu.busiest_engine(sample)[0], 45.0)  # max, not 85

    def test_unrelated_and_malformed_columns_are_ignored(self):
        sample = typeperf.CounterSample({"\\Memory\\Pages/sec": 5.0, "\\GPU Engine(weird)\\Utilization Percentage": 9.0})
        self.assertIsNone(gpu.busiest_engine(sample))

    def test_collector_reports_both_readings_consistently(self):
        collector = gpu.GpuCollector(StaticSource(typeperf.parse_typeperf_csv(GPU_CSV)))
        readings = collector.collect(CTX)
        self.assertAlmostEqual(readings["gpu.utilization"].value, 50.75)
        self.assertEqual(readings["gpu.busiest_engine"].value, "3D")

    def test_collector_states_when_no_data(self):
        for source, expected_detail in (
            (StaticSource(pending=True), "still running"),
            (StaticSource(error="typeperf timed out"), "timed out"),
            (StaticSource(typeperf.CounterSample({"\\Memory\\Pages/sec": 1.0})), "WDDM"),
        ):
            readings = gpu.GpuCollector(source).collect(CTX)
            for reading in readings.values():
                self.assertEqual(reading.status, Availability.UNAVAILABLE)
                self.assertIsNone(reading.value)
                self.assertIn(expected_detail, reading.detail)


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
class MemoryCollectorTests(unittest.TestCase):
    def test_commit_and_pages_available(self):
        pages = StaticSource(typeperf.CounterSample({"\\Memory\\Pages/sec": 7.5}))
        readings = MemoryCollector(lambda: (6 * 2**30, 8 * 2**30), pages).collect(CTX)
        self.assertEqual(readings["mem.commit_percent"].value, 75.0)
        self.assertEqual(readings["mem.commit_used_bytes"].value, 6 * 2**30)
        self.assertEqual(readings["mem.commit_limit_bytes"].value, 8 * 2**30)
        self.assertEqual(readings["mem.pages_per_sec"].value, 7.5)

    def test_commit_failure_is_unavailable_not_zero(self):
        readings = MemoryCollector(lambda: None, None).collect(CTX)
        for name in ("mem.commit_percent", "mem.commit_used_bytes", "mem.commit_limit_bytes"):
            self.assertEqual(readings[name].status, Availability.UNAVAILABLE)
        self.assertEqual(readings["mem.pages_per_sec"].status, Availability.NOT_SUPPORTED)

    def test_pages_states(self):
        commit = lambda: (1, 2)  # noqa: E731
        self.assertIn("still running", MemoryCollector(commit, StaticSource(pending=True)).collect(CTX)["mem.pages_per_sec"].detail)
        failed = MemoryCollector(commit, StaticSource(error="timed out")).collect(CTX)["mem.pages_per_sec"]
        self.assertEqual((failed.status, failed.detail), (Availability.UNAVAILABLE, "timed out"))
        missing = MemoryCollector(commit, StaticSource(typeperf.CounterSample({}))).collect(CTX)["mem.pages_per_sec"]
        self.assertFalse(missing.is_available)


# --------------------------------------------------------------------------- #
# Disk activity
# --------------------------------------------------------------------------- #
class DiskActivityTests(unittest.TestCase):
    def _collector(self, samples, times):
        counters = iter(samples)
        clock = iter(times)
        return DiskActivityCollector(lambda: next(counters), lambda: next(clock))

    def test_first_collection_is_warming_up_then_rate_is_computed(self):
        collector = self._collector(
            [SimpleNamespace(read_bytes=1000, write_bytes=0), SimpleNamespace(read_bytes=3000, write_bytes=500)],
            [10.0, 12.0],
        )
        first = collector.collect(CTX)
        self.assertEqual(first["disk.read_bps"].status, Availability.UNAVAILABLE)
        self.assertIn("warming up", first["disk.read_bps"].detail)
        second = collector.collect(CTX)
        self.assertEqual(second["disk.read_bps"].value, 1000.0)   # 2000 B over 2 s
        self.assertEqual(second["disk.write_bps"].value, 250.0)

    def test_counter_reset_skips_the_round(self):
        collector = self._collector(
            [SimpleNamespace(read_bytes=5000, write_bytes=5000), SimpleNamespace(read_bytes=10, write_bytes=10)],
            [1.0, 2.0],
        )
        collector.collect(CTX)
        readings = collector.collect(CTX)
        self.assertFalse(readings["disk.read_bps"].is_available)
        self.assertIn("reset", readings["disk.read_bps"].detail)

    def test_clock_not_advancing_gives_no_rate(self):
        collector = self._collector(
            [SimpleNamespace(read_bytes=1, write_bytes=1), SimpleNamespace(read_bytes=2, write_bytes=2)], [5.0, 5.0]
        )
        collector.collect(CTX)
        self.assertFalse(collector.collect(CTX)["disk.read_bps"].is_available)

    def test_counters_disabled_and_exceptions(self):
        self.assertFalse(DiskActivityCollector(lambda: None).collect(CTX)["disk.read_bps"].is_available)

        def broken():
            raise OSError("no counters")

        readings = DiskActivityCollector(broken).collect(CTX)
        self.assertEqual(readings["disk.write_bps"].status, Availability.UNAVAILABLE)
        self.assertIn("OSError", readings["disk.write_bps"].detail)


# --------------------------------------------------------------------------- #
# Thermal and fan
# --------------------------------------------------------------------------- #
class WmiParsingTests(unittest.TestCase):
    def test_kelvin_tenths_conversion(self):
        self.assertAlmostEqual(thermal.kelvin_tenths_to_celsius(3232), 50.05)

    def test_zones_and_fans(self):
        text = "TZ|ACPI\\ThermalZone\\TZ00_0|3232\nTZ|ACPI\\ThermalZone\\TZ01_0|3032\nFAN|CPU Fan|\n"
        result = thermal.parse_wmi_output(text)
        self.assertEqual([round(c, 2) for _, c in result.zones], [50.05, 30.05])
        self.assertEqual(result.fans, ("CPU Fan",))
        self.assertIsNone(result.zone_error)

    def test_implausible_zones_are_dropped_and_counted(self):
        result = thermal.parse_wmi_output("TZ|a|0\nTZ|b|9999\nTZ|c|notanumber\nTZ|d|3232")
        self.assertEqual(len(result.zones), 1)
        self.assertEqual(result.zones_dropped, 3)

    def test_error_lines_carry_the_hresult(self):
        text = "TZERR|HRESULT 0x80041003,Microsoft.Management...|Access denied \nFANERR|0x80041010|Invalid class"
        result = thermal.parse_wmi_output(text)
        self.assertEqual(result.zone_error[0], "0x80041003")
        self.assertEqual(result.fan_error[0], "0x80041010")

    def test_reader_failures_are_probe_errors(self):
        for code, message in ((RC_LAUNCH_FAILED, "could not be started"), (RC_TIMEOUT, "timed out")):
            reader = thermal.make_wmi_reader(lambda args, timeout, c=code: (c, ""))
            with self.assertRaisesRegex(ProbeError, message):
                reader()

    def test_reader_runs_powershell_without_changing_execution_policy(self):
        seen = {}

        def runner(args, timeout):
            seen["args"] = list(args)
            return 0, "TZ|z|3232\n"

        result = thermal.make_wmi_reader(runner)()
        self.assertEqual(len(result.zones), 1)
        self.assertEqual(seen["args"][0], "powershell.exe")
        self.assertIn("-NoProfile", seen["args"])
        self.assertNotIn("Bypass", " ".join(seen["args"]))


class ThermalCollectorTests(unittest.TestCase):
    def _temp(self, result=None, **kwargs):
        source = StaticSource(result, **kwargs)
        return thermal.ThermalCollector(source).collect(CTX)["temp.acpi_max"]

    def test_available_reports_hottest_zone_and_caveat(self):
        reading = self._temp(thermal.parse_wmi_output("TZ|a|3032\nTZ|b|3232"))
        self.assertTrue(reading.is_available)
        self.assertAlmostEqual(reading.value, 50.05)
        self.assertIn("not necessarily the CPU", reading.detail)

    def test_access_denied_is_permission_restricted(self):
        reading = self._temp(thermal.parse_wmi_output("TZERR|0x80041003|Access denied"))
        self.assertEqual(reading.status, Availability.PERMISSION_RESTRICTED)
        self.assertIsNone(reading.value)

    def test_no_zones_is_not_exposed(self):
        self.assertEqual(self._temp(thermal.parse_wmi_output("")).status, Availability.NOT_EXPOSED)

    def test_invalid_class_is_not_exposed(self):
        self.assertEqual(self._temp(thermal.parse_wmi_output("TZERR|0x80041010|x")).status, Availability.NOT_EXPOSED)

    def test_only_implausible_zones_is_unavailable(self):
        reading = self._temp(thermal.parse_wmi_output("TZ|a|0"))
        self.assertEqual(reading.status, Availability.UNAVAILABLE)
        self.assertIn("implausible", reading.detail)

    def test_unknown_error_is_unavailable_with_code(self):
        reading = self._temp(thermal.parse_wmi_output("TZERR|0x80041099|weird failure"))
        self.assertEqual(reading.status, Availability.UNAVAILABLE)
        self.assertIn("0x80041099", reading.detail)

    def test_probe_not_ready_or_failed(self):
        self.assertIn("still running", self._temp(pending=True).detail)
        self.assertEqual(self._temp(error="PowerShell timed out").detail, "PowerShell timed out")


class FanCollectorTests(unittest.TestCase):
    def _fan(self, result):
        return thermal.FanCollector(StaticSource(result)).collect(CTX)["fan.rpm"]

    def test_fan_speed_is_never_reported_even_when_a_fan_is_listed(self):
        reading = self._fan(thermal.parse_wmi_output("FAN|CPU Fan|3000"))  # DesiredSpeed is not a measurement
        self.assertEqual(reading.status, Availability.NOT_EXPOSED)
        self.assertIsNone(reading.value)
        self.assertIn("no measured fan speed", reading.detail)

    def test_no_fans(self):
        reading = self._fan(thermal.parse_wmi_output(""))
        self.assertEqual(reading.status, Availability.NOT_EXPOSED)
        self.assertIn("not exposed", reading.detail)

    def test_fan_query_error_mapping(self):
        self.assertEqual(self._fan(thermal.parse_wmi_output("FANERR|0x80041003|Access denied")).status,
                         Availability.PERMISSION_RESTRICTED)


# --------------------------------------------------------------------------- #
# Power
# --------------------------------------------------------------------------- #
class PowerTests(unittest.TestCase):
    def test_plugged_in_charging(self):
        readings = power.decode_power_status({"ac_line": 1, "battery_flag": 8, "battery_percent": 64, "saver": 0})
        self.assertIs(readings["power.on_battery"].value, False)
        self.assertEqual(readings["power.battery_percent"].value, 64)
        self.assertIs(readings["power.charging"].value, True)
        self.assertIs(readings["power.battery_saver"].value, False)

    def test_on_battery_with_saver(self):
        readings = power.decode_power_status({"ac_line": 0, "battery_flag": 1, "battery_percent": 20, "saver": 1})
        self.assertIs(readings["power.on_battery"].value, True)
        self.assertIs(readings["power.charging"].value, False)
        self.assertIs(readings["power.battery_saver"].value, True)

    def test_desktop_without_battery_is_not_exposed_not_zero_percent(self):
        readings = power.decode_power_status({"ac_line": 1, "battery_flag": 128, "battery_percent": 255, "saver": 0})
        self.assertEqual(readings["power.battery_percent"].status, Availability.NOT_EXPOSED)
        self.assertEqual(readings["power.charging"].status, Availability.NOT_EXPOSED)
        self.assertIn("no battery", readings["power.battery_percent"].detail)

    def test_unknown_values_are_unavailable(self):
        readings = power.decode_power_status({"ac_line": 255, "battery_flag": 255, "battery_percent": 255, "saver": 0})
        for name in ("power.on_battery", "power.battery_percent", "power.charging"):
            self.assertEqual(readings[name].status, Availability.UNAVAILABLE)
            self.assertIsNone(readings[name].value)

    def test_known_power_modes_are_named(self):
        self.assertEqual(power.decode_power_mode("00000000-0000-0000-0000-000000000000").value, "Balanced")
        self.assertEqual(power.decode_power_mode("DED574B5-45A0-4F42-8737-46345C09C238").value, "Best performance")

    def test_unknown_or_missing_mode_is_not_guessed(self):
        unknown = power.decode_power_mode("12345678-0000-0000-0000-000000000000")
        self.assertEqual(unknown.status, Availability.UNAVAILABLE)
        self.assertIn("unrecognised", unknown.detail)
        self.assertEqual(power.decode_power_mode(None).status, Availability.UNAVAILABLE)

    def test_collector_when_status_unreadable(self):
        readings = power.PowerCollector(lambda: None, lambda: None).collect(CTX)
        self.assertTrue(all(not r.is_available for r in readings.values()))
        self.assertEqual(set(readings), {
            "power.on_battery", "power.battery_percent", "power.charging", "power.battery_saver", "power.mode"})

    def test_backend_helpers(self):
        self.assertEqual(windows_backend.format_guid(0x961CC777, 0x2547, 0x4F9D, bytes.fromhex("81747d86181b8a7a")),
                         "961cc777-2547-4f9d-8174-7d86181b8a7a")
        self.assertEqual(windows_backend.commit_bytes(100, 200, 4096), (409600, 819200))
        self.assertIsNone(windows_backend.commit_bytes(100, 0, 4096))
        self.assertIsNone(windows_backend.commit_bytes(-1, 10, 4096))

    def test_backend_calls_are_safe_off_windows(self):
        if sys.platform == "win32":
            self.skipTest("only meaningful on non-Windows")
        self.assertIsNone(windows_backend.get_commit_charge())
        self.assertIsNone(windows_backend.get_power_status())
        self.assertIsNone(windows_backend.get_power_overlay_guid())
        self.assertIsNone(windows_backend.is_user_admin())


# --------------------------------------------------------------------------- #
# Startup programs
# --------------------------------------------------------------------------- #
class FakeWinreg:
    """A tiny in-memory stand-in for the ``winreg`` module."""

    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = 1

    def __init__(self, keys, denied=()):
        self.keys = keys            # {(hive, subkey): {name: data}}
        self.denied = set(denied)
        self.closed = 0

    def OpenKey(self, root, subkey, _reserved, _access):  # noqa: N802 - mirrors winreg
        if (root, subkey) in self.denied:
            raise PermissionError("denied")
        if (root, subkey) not in self.keys:
            raise FileNotFoundError(subkey)
        return (root, subkey)

    def EnumValue(self, key, index):  # noqa: N802
        items = list(self.keys[key].items())
        if index >= len(items):
            raise OSError("no more data")
        name, data = items[index]
        return name, data, 1

    def CloseKey(self, key):  # noqa: N802
        self.closed += 1


RUN_HKCU = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_HKCU_RUN = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
RUN_HKLM = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"


class StartupTests(unittest.TestCase):
    def _collector(self, keys, denied=(), listings=None, clock=None):
        winreg = FakeWinreg(keys, denied)
        listings = listings or {}

        def listdir(path):
            if path in listings:
                return listings[path]
            raise FileNotFoundError(path)

        return StartupCollector(
            RegistryReader(winreg),
            env={"APPDATA": r"C:\Users\u\AppData\Roaming", "ProgramData": r"C:\ProgramData"},
            listdir=listdir,
            clock=clock or time.monotonic,
        ), winreg

    def test_approved_state_decoding(self):
        self.assertIs(approved_state(b"\x02\x00\x00\x00"), True)
        self.assertIs(approved_state(b"\x06\x00"), True)
        self.assertIs(approved_state(b"\x03\x00\x00\x00"), False)
        self.assertIs(approved_state(b"\x07"), False)
        self.assertIsNone(approved_state(b""))
        self.assertIsNone(approved_state("text"))
        self.assertIsNone(approved_state(None))

    def test_entries_with_enabled_disabled_and_unknown_state(self):
        collector, _ = self._collector({
            ("HKCU", RUN_HKCU): {"Spotify": r"C:\spotify.exe", "OneDrive": "od.exe", "Odd": "x.exe", "NoRecord": "n.exe"},
            ("HKCU", APPROVED_HKCU_RUN): {"Spotify": b"\x03\x00", "OneDrive": b"\x02\x00", "Odd": b"?"[:0]},
        })
        readings = collector.collect(CTX)
        entries = {e.name: e for e in readings["startup.entries"].value}
        self.assertIs(entries["Spotify"].enabled, False)
        self.assertIs(entries["OneDrive"].enabled, True)
        self.assertIsNone(entries["Odd"].enabled)          # empty record: cannot interpret
        self.assertIs(entries["NoRecord"].enabled, True)    # no record means enabled
        self.assertEqual(readings["startup.enabled_count"].value, 2)
        self.assertIn("1 entry with unknown state", readings["startup.enabled_count"].detail)
        self.assertIn("services and scheduled tasks are not included", readings["startup.entries"].detail)

    def test_machine_wide_and_folder_entries(self):
        user_folder = os.path.join(r"C:\Users\u\AppData\Roaming", r"Microsoft\Windows\Start Menu\Programs\Startup")
        collector, _ = self._collector(
            {("HKLM", RUN_HKLM): {"Defender": "def.exe"}},
            listings={user_folder: ["desktop.ini", "Tool.lnk"]},
        )
        entries = collector.collect(CTX)["startup.entries"].value
        locations = {e.name: e.location for e in entries}
        self.assertEqual(locations["Defender"], "Registry (all users)")
        self.assertEqual(locations["Tool.lnk"], "Startup folder (this user)")
        self.assertNotIn("desktop.ini", locations)

    def test_missing_keys_mean_zero_entries_not_an_error(self):
        collector, _ = self._collector({})
        readings = collector.collect(CTX)
        self.assertEqual(readings["startup.enabled_count"].value, 0)

    def test_unreadable_registry_is_unavailable_not_zero(self):
        collector, _ = self._collector(
            {}, denied=[("HKCU", RUN_HKCU), ("HKLM", RUN_HKLM),
                        ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run")]
        )
        readings = collector.collect(CTX)
        self.assertEqual(readings["startup.enabled_count"].status, Availability.UNAVAILABLE)
        self.assertIsNone(readings["startup.enabled_count"].value)

    def test_no_registry_module_is_not_supported(self):
        reader = RegistryReader(winreg_module=None)
        if reader.available:  # real Windows: skip, the fake-free path is exercised elsewhere
            self.skipTest("winreg exists here")
        readings = StartupCollector(reader).collect(CTX)
        self.assertEqual(readings["startup.entries"].status, Availability.NOT_SUPPORTED)

    def test_result_is_cached_for_a_minute(self):
        now = [0.0]
        collector, winreg = self._collector({("HKCU", RUN_HKCU): {"A": "a.exe"}}, clock=lambda: now[0])
        first = collector.collect(CTX)
        opened = winreg.closed
        now[0] = 30.0
        self.assertIs(collector.collect(CTX), first)
        self.assertEqual(winreg.closed, opened)            # no registry access while cached
        now[0] = 61.0
        collector.collect(CTX)
        self.assertGreater(winreg.closed, opened)

    def test_registry_reader_closes_keys(self):
        winreg = FakeWinreg({("HKCU", "k"): {"a": 1}})
        RegistryReader(winreg).values("HKCU", "k")
        self.assertEqual(winreg.closed, 1)


# --------------------------------------------------------------------------- #
# Honesty matrix: with every dependency broken, nothing may claim a value
# --------------------------------------------------------------------------- #
class AvailabilityMatrixTests(unittest.TestCase):
    def test_broken_collectors_never_produce_a_value(self):
        def boom():
            raise OSError("broken")

        wmi_empty = StaticSource(thermal.WmiThermalResult())
        collectors = [
            DiskActivityCollector(lambda: None),
            MemoryCollector(lambda: None, StaticSource(error="x")),
            gpu.GpuCollector(StaticSource(typeperf.CounterSample({"junk": 1.0}))),
            gpu.GpuCollector(StaticSource(error="typeperf timed out")),
            thermal.ThermalCollector(wmi_empty),
            thermal.ThermalCollector(StaticSource(error="PowerShell timed out")),
            thermal.FanCollector(wmi_empty),
            thermal.FanCollector(StaticSource(pending=True)),
            power.PowerCollector(lambda: None, lambda: None),
            StartupCollector(RegistryReader(winreg_module=None)) if not RegistryReader().available else
            StartupCollector(RegistryReader(FakeWinreg({}, denied=[("HKCU", RUN_HKCU)]))),
        ]
        for collector in collectors:
            for name, reading in collector.collect(CTX).items():
                with self.subTest(collector=type(collector).__name__, reading=name):
                    self.assertFalse(reading.is_available, f"{name} claimed a value: {reading}")
                    self.assertIsNone(reading.value)


# --------------------------------------------------------------------------- #
# Registry of collectors, probe command, labels
# --------------------------------------------------------------------------- #
class RegistryTests(unittest.TestCase):
    def test_non_windows_gets_only_cross_platform_collectors(self):
        with mock.patch("app.collectors.registry.oscope_platform.is_windows", return_value=False):
            self.assertEqual([c.key for c in build_default_collectors()], ["disk"])

    def test_windows_gets_every_collector(self):
        with mock.patch("app.collectors.registry.oscope_platform.is_windows", return_value=True):
            collectors = build_default_collectors()
        self.assertEqual([c.key for c in collectors],
                         ["disk", "memory", "gpu", "thermal", "fan", "power", "startup"])

    def test_building_collectors_spawns_nothing(self):
        with mock.patch("app.collectors.registry.oscope_platform.is_windows", return_value=True), \
                mock.patch("app.collectors.windows.cmd.subprocess.run") as run:
            build_default_collectors()
        run.assert_not_called()  # probes only start when first read, on their own thread


class ProbeCommandTests(unittest.TestCase):
    def test_prints_every_reading_with_status(self):
        out = io.StringIO()
        collector = FakeCollector("fake", {
            "a.value": Reading.available("a.value", 5, "units", "src"),
            "b.value": Reading.missing("b.value", Availability.NOT_EXPOSED, "no sensor", "src2"),
        })
        self.assertEqual(run_probe([collector], out=out, rounds=1, pause=0), 0)
        text = out.getvalue()
        self.assertIn("a.value", text)
        self.assertIn("Available", text)
        self.assertIn("5 units", text)
        self.assertIn("Not exposed by hardware", text)
        self.assertIn("no sensor", text)

    def test_a_raising_collector_is_reported_not_fatal(self):
        out = io.StringIO()
        run_probe([FakeCollector("bad", error=RuntimeError("boom"))], out=out, rounds=1, pause=0)
        self.assertIn("RuntimeError", out.getvalue())


class LabelTests(unittest.TestCase):
    def test_describe_available_missing_and_absent(self):
        readings = {
            "gpu.utilization": Reading.available("gpu.utilization", 51.4, "%"),
            "gpu.busiest_engine": Reading.available("gpu.busiest_engine", "3D"),
            "fan.rpm": Reading.missing("fan.rpm", Availability.NOT_EXPOSED, "no fan sensor"),
        }
        self.assertEqual(labels.describe(readings, "gpu.utilization"), ("51% (3D engine)", "Available"))
        self.assertEqual(labels.describe(readings, "fan.rpm"), ("", "Not exposed by hardware"))
        self.assertEqual(labels.detail_for(readings, "fan.rpm"), "no fan sensor")
        self.assertEqual(labels.describe(readings, "temp.acpi_max"), ("", "Not supported on this OS"))  # none registered

    def test_evidence_report_explains_every_row(self):
        readings = {
            "gpu.utilization": Reading.available("gpu.utilization", 51.4, "%", source="PDH:GPU Engine",
                                                 detail="busiest GPU engine, as in Task Manager"),
            "fan.rpm": Reading.missing("fan.rpm", Availability.NOT_EXPOSED, "no fan sensor", "WMI:Win32_Fan"),
            "temp.acpi_max": Reading.missing("temp.acpi_max", Availability.PERMISSION_RESTRICTED, "", "WMI"),
        }
        report = labels.evidence_report(readings)
        for _name, title in labels.EVIDENCE_ROWS:
            self.assertIn(title + ":", report)
        self.assertIn("GPU load: 51%", report)
        self.assertIn("busiest GPU engine", report)
        self.assertIn("Fan speed: Not exposed by hardware\n    no fan sensor", report)
        self.assertIn("Source: WMI:Win32_Fan", report)
        self.assertIn("Permission restricted", report)
        self.assertIn("not collected on this operating system", report)  # rows with no collector
        self.assertIn("never as a guessed number", report)

    def test_value_formats(self):
        readings = {
            "disk.read_bps": Reading.available("disk.read_bps", 5 * 1024 * 1024, "B/s"),
            "power.on_battery": Reading.available("power.on_battery", True),
            "power.battery_percent": Reading.available("power.battery_percent", 80, "%"),
            "power.charging": Reading.available("power.charging", True),
            "temp.acpi_max": Reading.available("temp.acpi_max", 47.3, "°C"),
            "startup.enabled_count": Reading.available("startup.enabled_count", 7),
        }
        self.assertEqual(labels.value_text(readings, "disk.read_bps"), "5.0 MB/s")
        self.assertEqual(labels.value_text(readings, "power.on_battery"), "On battery")
        self.assertEqual(labels.value_text(readings, "power.battery_percent"), "80%, charging")
        self.assertEqual(labels.value_text(readings, "temp.acpi_max"), "47 °C")
        self.assertEqual(labels.value_text(readings, "startup.enabled_count"), "7 enabled")

    def test_every_evidence_row_has_a_label(self):
        names = [name for name, _label in labels.EVIDENCE_ROWS]
        self.assertEqual(len(names), len(set(names)))


# --------------------------------------------------------------------------- #
# Process grouping
# --------------------------------------------------------------------------- #
def proc(pid, name, cpu=0.0, mem=1000):
    return ProcessInfo(pid=pid, name=name, cpu_percent=cpu, memory_bytes=mem, status="Running", ppid=1)


class GroupingTests(unittest.TestCase):
    def test_same_program_is_one_group_regardless_of_case(self):
        groups = group_processes([proc(1, "chrome.exe", 1.0, 100), proc(2, "Chrome.exe", 2.5, 300), proc(3, "notepad.exe")])
        chrome = next(g for g in groups if g.key == "chrome.exe")
        self.assertEqual((chrome.count, chrome.memory_bytes, chrome.cpu_percent), (2, 400, 3.5))
        self.assertEqual(chrome.display, "Google Chrome")
        self.assertEqual(len(groups), 2)

    def test_unknown_program_keeps_its_file_name(self):
        self.assertEqual(group_processes([proc(1, "MyTool.exe")])[0].display, "MyTool.exe")

    def test_sorted_by_memory_total_biggest_first(self):
        groups = group_processes([proc(1, "a.exe", mem=10), proc(2, "b.exe", mem=500), proc(3, "b.exe", mem=500)])
        self.assertEqual([g.key for g in groups], ["b.exe", "a.exe"])

    def test_unreadable_memory_is_counted_not_added(self):
        groups = group_processes([proc(1, "svc.exe", mem=None), proc(2, "svc.exe", mem=200)])
        self.assertEqual((groups[0].memory_bytes, groups[0].restricted_pids, groups[0].count), (200, 1, 2))

    def test_system_hosts_are_flagged(self):
        flags = {g.key: g.is_system for g in group_processes([proc(1, "svchost.exe"), proc(2, "chrome.exe")])}
        self.assertEqual(flags, {"svchost.exe": True, "chrome.exe": False})

    def test_browser_keys_are_known_apps_not_system(self):
        self.assertIn("chrome.exe", BROWSER_KEYS)
        self.assertTrue(all(not g.is_system for g in group_processes([proc(1, key) for key in BROWSER_KEYS])))

    def test_empty(self):
        self.assertEqual(group_processes([]), [])


if __name__ == "__main__":
    unittest.main()
