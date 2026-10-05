"""Phase 3 tests: trends, detectors, relationship rules, questions, workloads and the wording guard.

All inputs are synthetic snapshots; nothing here measures a real machine.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis import detectors, explain, trends, workloads  # noqa: E402
from app.analysis.facts import Facts  # noqa: E402
from app.analysis.grouping import group_processes  # noqa: E402
from app.analysis.models import EvidenceStrength  # noqa: E402
from app.analysis.orchestrator import run_question  # noqa: E402
from app.analysis.questions import QUESTION_ORDER, QUESTIONS  # noqa: E402
from app.analysis.rules import RELATIONSHIP_RULES, Rule  # noqa: E402
from app.collectors.base import Availability, Reading  # noqa: E402
from app.collectors.windows.startup import StartupEntry  # noqa: E402
from app.core.process_manager import ProcessInfo  # noqa: E402
from app.core.resource_manager import MemoryInfo  # noqa: E402
from app.core.sampler import SampleRecord, Snapshot  # noqa: E402
from app.core.storage_manager import StorageInfo  # noqa: E402

GB, MB = 1024 ** 3, 1024 ** 2
T0 = datetime(2026, 10, 5, 12, 0, 0)


def proc(pid, name, mb=100, cpu=0.5):
    return ProcessInfo(pid=pid, name=name, cpu_percent=cpu, memory_bytes=mb * MB, status="Running", ppid=1)


def avail(name, value, unit=""):
    return name, Reading.available(name, value, unit)


def missing(name, status=Availability.NOT_EXPOSED, detail="no sensor"):
    return name, Reading.missing(name, status, detail)


def make_snapshot(cpu=10.0, mem_pct=40.0, storage_pct=50.0, processes=None, readings=None, uptime=7200.0):
    mem = MemoryInfo(total=16 * GB, used=int(16 * GB * mem_pct / 100), available=int(16 * GB * (100 - mem_pct) / 100), percent=mem_pct)
    storage = StorageInfo("C:\\", 500 * GB, int(500 * GB * storage_pct / 100), int(500 * GB * (100 - storage_pct) / 100), storage_pct)
    processes = processes if processes is not None else [proc(1, "notepad.exe", 20)]
    return Snapshot(
        taken_at=T0, cpu_percent=cpu, memory=mem, storage=storage, uptime_seconds=uptime,
        processes=processes, process_groups=group_processes(processes), readings=dict(readings or {}),
    )


def make_window(n=30, cpu=10.0, mem=40.0, storage=50.0, readings=None, cpus=None):
    cpus = cpus if cpus is not None else [cpu] * n
    return [
        SampleRecord(T0 + timedelta(seconds=2 * i), cpus[i], mem, storage, dict(readings or {})) for i in range(len(cpus))
    ]


def busy_readings(**extra):
    base = dict([
        avail("disk.read_bps", 90.0 * MB, "B/s"), avail("disk.write_bps", 40.0 * MB, "B/s"),
    ])
    base.update(extra)
    return base


def run(question, snapshot, window=None, workload="general"):
    return run_question(question, snapshot, window if window is not None else make_window(), workload)


# --------------------------------------------------------------------------- #
class TrendTests(unittest.TestCase):
    def test_sustained_high_needs_most_of_the_window(self):
        state = trends.assess(make_window(cpus=[90.0] * 8 + [10.0] * 2), "cpu", 80)
        self.assertTrue(state.high)
        self.assertEqual(state.basis, "sustained")

    def test_a_single_spike_is_not_sustained(self):
        state = trends.assess(make_window(cpus=[10.0] * 29 + [100.0]), "cpu", 80)
        self.assertFalse(state.high)
        self.assertEqual(state.maximum, 100.0)

    def test_too_little_history_falls_back_to_now_and_says_so(self):
        state = trends.assess(make_window(cpus=[95.0, 96.0]), "cpu", 80)
        self.assertEqual((state.basis, state.high), ("now", True))

    def test_no_data(self):
        state = trends.assess([], "cpu", 80)
        self.assertEqual((state.basis, state.high, state.latest), ("none", False, None))

    def test_samples_without_a_value_are_skipped(self):
        window = make_window(cpus=[None, 90.0, 90.0, None, 90.0, 90.0, 90.0])
        state = trends.assess(window, "cpu", 80)
        self.assertEqual(state.samples, 5)
        self.assertTrue(state.high)

    def test_disk_total_needs_both_readings(self):
        both = busy_readings()
        only_read = {"disk.read_bps": both["disk.read_bps"]}
        self.assertEqual(trends.series(make_window(n=2, readings=both), "disk.total_bps"), [130.0 * MB] * 2)
        self.assertEqual(trends.series(make_window(n=2, readings=only_read), "disk.total_bps"), [])

    def test_reading_series_ignores_unavailable_and_non_numeric(self):
        readings = dict([missing("gpu.utilization"), avail("power.mode", "Balanced")])
        self.assertEqual(trends.series(make_window(n=3, readings=readings), "gpu.utilization"), [])
        self.assertEqual(trends.series(make_window(n=3, readings=readings), "power.mode"), [])


class FactsTests(unittest.TestCase):
    def test_has_tokens(self):
        facts = Facts(make_snapshot(readings=dict([avail("gpu.utilization", 5.0), missing("temp.acpi_max")])), make_window())
        self.assertTrue(all(facts.has(t) for t in ("cpu", "memory", "storage", "groups", "gpu.utilization")))
        self.assertFalse(facts.has("temp.acpi_max"))   # present but not available
        self.assertFalse(facts.has("fan.rpm"))         # absent

    def test_seconds_and_basis_text(self):
        facts = Facts(make_snapshot(), make_window(n=31))
        self.assertEqual(facts.seconds, 60.0)
        self.assertIn("60 seconds", facts.basis_text(facts.state("cpu", 80)))
        short = Facts(make_snapshot(), make_window(n=2))
        self.assertIn("only just started", short.basis_text(short.state("cpu", 80)))


class RuleContractTests(unittest.TestCase):
    def test_a_rule_cannot_claim_more_than_seen_together(self):
        for strength in (EvidenceStrength.STRONG, EvidenceStrength.OBSERVED):
            with self.assertRaises(ValueError):
                Rule("bad", (), lambda f: True, lambda f: ("x", []), strength=strength)

    def test_rule_ids_are_unique_and_referenced_questions_exist(self):
        ids = [rule.id for rule in RELATIONSHIP_RULES]
        self.assertEqual(len(ids), len(set(ids)))
        for rule in RELATIONSHIP_RULES:
            self.assertLessEqual(rule.strength, EvidenceStrength.CORRELATION)
            for question_id in rule.only_for or ():
                self.assertIn(question_id, QUESTIONS)
        for question in QUESTIONS.values():
            for rule_id in question.rules or ():
                self.assertIn(rule_id, ids)
            for name in question.detectors:
                self.assertIn(name, detectors.DETECTORS)

    def test_strength_labels_are_the_three_user_facing_ones(self):
        labels_used = {explain.strength_label(s) for s in EvidenceStrength}
        self.assertEqual(labels_used, {"Measured", "Seen together", "Could not verify"})


# --------------------------------------------------------------------------- #
class DetectorTests(unittest.TestCase):
    def facts(self, snapshot=None, window=None):
        return Facts(snapshot or make_snapshot(), window if window is not None else make_window())

    def test_cpu_normal_and_high_and_critical(self):
        self.assertEqual(detectors.detect_cpu(self.facts()).level, "normal")
        high = detectors.detect_cpu(self.facts(make_snapshot(cpu=88), make_window(cpu=88)))
        self.assertEqual((high.level, high.title), ("warning", "High CPU load"))
        self.assertIn("last 58 seconds", high.happening)
        critical = detectors.detect_cpu(self.facts(make_snapshot(cpu=98), make_window(cpu=98)))
        self.assertEqual(critical.level, "critical")

    def test_cpu_with_little_history_is_only_info_and_says_so(self):
        finding = detectors.detect_cpu(self.facts(make_snapshot(cpu=99), make_window(cpus=[99.0, 99.0])))
        self.assertEqual(finding.level, "info")
        self.assertIn("cannot yet say whether this is sustained", finding.happening)

    def test_cpu_lists_busy_programs_only(self):
        procs = [proc(1, "idle.exe", cpu=0.1), proc(2, "render.exe", cpu=40.0), proc(3, "render.exe", cpu=10.0)]
        finding = detectors.detect_cpu(self.facts(make_snapshot(cpu=88, processes=procs), make_window(cpu=88)))
        labels = [e.label for e in finding.contributing]
        self.assertIn("render.exe", labels)
        self.assertNotIn("idle.exe", labels)

    def test_cpu_unreadable(self):
        snap = make_snapshot()
        snap.cpu_percent = None
        self.assertEqual(detectors.detect_cpu(self.facts(snap)).level, "normal")
        self.assertIn("could not be read", detectors.detect_cpu(self.facts(snap)).happening)

    def test_memory_levels_and_top_programs(self):
        procs = [proc(1, "chrome.exe", 3000), proc(2, "chrome.exe", 1000), proc(3, "game.exe", 2000)]
        normal = detectors.detect_memory(self.facts(make_snapshot(processes=procs)))
        self.assertEqual(normal.level, "normal")
        self.assertIn("not necessarily a problem", normal.happening)
        high = detectors.detect_memory(self.facts(make_snapshot(mem_pct=91, processes=procs), make_window(mem=91)))
        self.assertEqual(high.level, "warning")
        top = [e for e in high.contributing if e.id.startswith("proc.group.")]
        self.assertEqual(top[0].label, "Google Chrome")
        self.assertIn("2 processes", top[0].value_text)
        self.assertTrue(high.consider)
        critical = detectors.detect_memory(self.facts(make_snapshot(mem_pct=97), make_window(mem=97)))
        self.assertEqual(critical.level, "critical")

    def test_memory_commit_near_limit_is_flagged_even_when_ram_looks_fine(self):
        readings = dict([avail("mem.commit_percent", 93.0, "%"), avail("mem.commit_used_bytes", 30 * GB),
                         avail("mem.commit_limit_bytes", 32 * GB), avail("mem.pages_per_sec", 12.0)])
        finding = detectors.detect_memory(self.facts(make_snapshot(mem_pct=50, readings=readings)))
        self.assertEqual((finding.level, finding.title), ("warning", "Memory commitment is high"))
        ids = {e.id for e in finding.contributing}
        self.assertTrue({"mem.commit", "mem.pages"} <= ids)

    def test_disk_states(self):
        no_data = detectors.detect_disk(self.facts())
        self.assertEqual(no_data.level, "normal")
        self.assertIn("not available yet", no_data.happening)
        idle = detectors.detect_disk(self.facts(make_snapshot(readings=dict([avail("disk.read_bps", 1.0 * MB), avail("disk.write_bps", 0.0)]))))
        self.assertEqual(idle.level, "normal")
        heavy_readings = busy_readings()
        heavy = detectors.detect_disk(self.facts(make_snapshot(readings=heavy_readings), make_window(readings=heavy_readings)))
        self.assertEqual(heavy.level, "info")
        self.assertTrue(any(e.strength == EvidenceStrength.UNVERIFIED for e in heavy.contributing))

    def test_gpu_unavailable_is_stated_not_guessed(self):
        finding = detectors.detect_gpu(self.facts(make_snapshot(readings=dict([missing("gpu.utilization", Availability.UNAVAILABLE, "counter missing")]))))
        self.assertEqual(finding.level, "normal")
        self.assertIn("cannot be read", finding.happening)
        self.assertEqual(finding.contributing[0].strength, EvidenceStrength.UNVERIFIED)

    def test_gpu_high(self):
        readings = dict([avail("gpu.utilization", 95.0, "%"), avail("gpu.busiest_engine", "3D")])
        finding = detectors.detect_gpu(self.facts(make_snapshot(readings=readings), make_window(readings=readings)))
        self.assertEqual(finding.level, "info")
        self.assertIn("3D engine", finding.contributing[0].value_text)

    def test_storage_levels(self):
        expect = {50: "normal", 82: "info", 92: "warning", 97: "critical"}
        for percent, level in expect.items():
            finding = detectors.detect_storage(self.facts(make_snapshot(storage_pct=percent)))
            self.assertEqual(finding.level, level, percent)
        self.assertEqual(detectors.detect_storage(self.facts(make_snapshot(storage_pct=92))).goto, "storage")
        self.assertIsNone(detectors.detect_storage(self.facts(make_snapshot(storage_pct=50))).goto)

    def test_power_states(self):
        on_battery = dict([avail("power.on_battery", True), avail("power.mode", "Balanced"), avail("power.battery_percent", 40)])
        finding = detectors.detect_power(self.facts(make_snapshot(readings=on_battery)))
        self.assertEqual(finding.level, "info")
        self.assertIn("(40%)", finding.happening)
        self.assertIn("Balanced", finding.happening)
        plugged = detectors.detect_power(self.facts(make_snapshot(readings=dict([avail("power.on_battery", False)]))))
        self.assertEqual(plugged.level, "normal")
        unknown = detectors.detect_power(self.facts())
        self.assertIn("cannot be read", unknown.happening)

    def test_thermal_with_and_without_a_sensor(self):
        hot = detectors.detect_thermal(self.facts(make_snapshot(readings=dict([avail("temp.acpi_max", 88.0)]))))
        self.assertEqual(hot.level, "warning")
        self.assertIn("not necessarily the CPU", hot.happening)
        warm = detectors.detect_thermal(self.facts(make_snapshot(readings=dict([avail("temp.acpi_max", 72.0)]))))
        self.assertEqual(warm.level, "info")
        none = detectors.detect_thermal(self.facts(make_snapshot(readings=dict([missing("temp.acpi_max"), missing("fan.rpm")]))))
        self.assertEqual(none.level, "normal")
        self.assertIn("cannot say whether the PC is running hot", none.happening)
        unverified = [e for e in none.contributing if e.strength == EvidenceStrength.UNVERIFIED]
        self.assertEqual({e.id for e in unverified}, {"temp.none", "fan.none"})

    def test_fan_speed_is_never_presented_as_measured(self):
        finding = detectors.detect_thermal(self.facts(make_snapshot(readings=dict([avail("temp.acpi_max", 50.0), missing("fan.rpm")]))))
        fan = next(e for e in finding.contributing if e.id == "fan.none")
        self.assertEqual(fan.strength, EvidenceStrength.UNVERIFIED)

    def test_startup(self):
        entries = tuple(StartupEntry(f"App{i}", "x.exe", "Registry (this user)", True) for i in range(12))
        many = detectors.detect_startup(self.facts(make_snapshot(readings=dict([avail("startup.entries", entries), avail("startup.enabled_count", 12)]))))
        self.assertEqual(many.level, "info")
        self.assertLessEqual(len([e for e in many.contributing if e.id.startswith("startup.") and e.id[8:].isdigit()]), 8)
        few = detectors.detect_startup(self.facts(make_snapshot(readings=dict([avail("startup.entries", entries[:3]), avail("startup.enabled_count", 3)]))))
        self.assertEqual(few.level, "normal")
        self.assertIn("cannot be listed", detectors.detect_startup(self.facts()).happening)


# --------------------------------------------------------------------------- #
class RuleTests(unittest.TestCase):
    def relation(self, rule_id, snapshot, window=None, question="slow"):
        facts = Facts(snapshot, window if window is not None else make_window())
        rule = next(r for r in RELATIONSHIP_RULES if r.id == rule_id)
        return rule.evaluate(facts)

    # browser + memory
    def test_browser_memory_positive_and_negatives(self):
        many = [proc(i, "chrome.exe", 150) for i in range(1, 15)]
        self.assertIsNotNone(self.relation("browser_memory", make_snapshot(mem_pct=90, processes=many), make_window(mem=90)))
        self.assertIsNone(self.relation("browser_memory", make_snapshot(mem_pct=50, processes=many)))                 # memory fine
        self.assertIsNone(self.relation("browser_memory", make_snapshot(mem_pct=90, processes=many[:3]), make_window(mem=90)))  # few processes

    # RAM + disk
    def test_ram_and_disk(self):
        readings = busy_readings()
        snap, window = make_snapshot(mem_pct=90, readings=readings), make_window(mem=90, readings=readings)
        relation = self.relation("ram_and_disk", snap, window)
        self.assertIn("cannot tell which came first", relation.text)
        self.assertIsNone(self.relation("ram_and_disk", make_snapshot(mem_pct=90), make_window(mem=90)))      # disk readings missing: silent
        self.assertIsNone(self.relation("ram_and_disk", make_snapshot(mem_pct=50, readings=readings), make_window(mem=50, readings=readings)))

    def test_ram_and_disk_adds_paging_evidence_only_when_measured(self):
        readings = busy_readings(**dict([avail("mem.pages_per_sec", 250.0)]))
        relation = self.relation("ram_and_disk", make_snapshot(mem_pct=90, readings=readings), make_window(mem=90, readings=readings))
        self.assertIn("mem.pages", {e.id for e in relation.evidence})

    # scratch app + low free space
    def test_scratch_app_with_low_free_space(self):
        procs = [proc(1, "Photoshop.exe", 2000)]
        self.assertIsNotNone(self.relation("low_free_scratch_app", make_snapshot(storage_pct=93, processes=procs)))
        self.assertIsNone(self.relation("low_free_scratch_app", make_snapshot(storage_pct=40, processes=procs)))           # plenty of space
        self.assertIsNone(self.relation("low_free_scratch_app", make_snapshot(storage_pct=93, processes=[proc(1, "notepad.exe")])))  # no such app
        text = self.relation("low_free_scratch_app", make_snapshot(storage_pct=93, processes=procs)).text
        self.assertIn("if that is this drive", text)
        self.assertIn("cannot see which drive", text)

    # battery + power mode + load
    def test_battery_load_requires_known_mode_and_high_load(self):
        readings = dict([avail("power.on_battery", True), avail("power.mode", "Best power efficiency")])
        high = make_window(cpu=92)
        self.assertIsNotNone(self.relation("battery_load", make_snapshot(cpu=92, readings=readings), high))
        self.assertIsNone(self.relation("battery_load", make_snapshot(cpu=20, readings=readings), make_window(cpu=20)))  # load is low
        no_mode = dict([avail("power.on_battery", True), missing("power.mode", Availability.UNAVAILABLE, "unrecognised")])
        self.assertIsNone(self.relation("battery_load", make_snapshot(cpu=92, readings=no_mode), high))                  # mode unknown: silent
        plugged = dict([avail("power.on_battery", False), avail("power.mode", "Balanced")])
        self.assertIsNone(self.relation("battery_load", make_snapshot(cpu=92, readings=plugged), high))

    # sync + disk
    def test_sync_client_active_while_disk_is_busy(self):
        readings = busy_readings()
        procs = [proc(1, "OneDrive.exe", 100, cpu=4.0)]
        window = make_window(readings=readings)
        self.assertIsNotNone(self.relation("sync_disk", make_snapshot(processes=procs, readings=readings), window))
        idle = [proc(1, "OneDrive.exe", 100, cpu=0.0)]
        self.assertIsNone(self.relation("sync_disk", make_snapshot(processes=idle, readings=readings), window))           # present but idle
        self.assertIsNone(self.relation("sync_disk", make_snapshot(processes=procs)))                                      # no disk data

    # startup
    def test_many_startup_programs_with_recent_boot_or_high_memory(self):
        readings = dict([avail("startup.enabled_count", 14)])
        self.assertIsNotNone(self.relation("many_startup", make_snapshot(readings=readings, uptime=300)))
        self.assertIsNotNone(self.relation("many_startup", make_snapshot(mem_pct=90, readings=readings, uptime=86400), make_window(mem=90)))
        self.assertIsNone(self.relation("many_startup", make_snapshot(readings=readings, uptime=86400)))                   # neither
        few = dict([avail("startup.enabled_count", 3)])
        self.assertIsNone(self.relation("many_startup", make_snapshot(readings=few, uptime=300)))

    # load + temperature
    def test_gpu_and_cpu_with_temperature(self):
        gpu = dict([avail("gpu.utilization", 95.0), avail("temp.acpi_max", 80.0)])
        relation = self.relation("gpu_heat", make_snapshot(readings=gpu), make_window(readings=gpu))
        self.assertIn("not the GPU's own temperature", relation.text)
        cool = dict([avail("gpu.utilization", 95.0), avail("temp.acpi_max", 45.0)])
        self.assertIsNone(self.relation("gpu_heat", make_snapshot(readings=cool), make_window(readings=cool)))
        no_temp = dict([avail("gpu.utilization", 95.0)])
        self.assertIsNone(self.relation("gpu_heat", make_snapshot(readings=no_temp), make_window(readings=no_temp)))

        cpu_readings = dict([avail("temp.acpi_max", 82.0)])
        self.assertIsNotNone(self.relation("cpu_heat", make_snapshot(cpu=92, readings=cpu_readings), make_window(cpu=92, readings=cpu_readings)))
        self.assertIsNone(self.relation("cpu_heat", make_snapshot(cpu=20, readings=cpu_readings), make_window(cpu=20, readings=cpu_readings)))

    def test_load_without_sensors_only_when_there_is_no_temperature(self):
        no_sensor = dict([missing("temp.acpi_max")])
        relation = self.relation("load_without_sensors", make_snapshot(cpu=92, readings=no_sensor), make_window(cpu=92))
        self.assertIn("cannot confirm that here", relation.text)
        with_sensor = dict([avail("temp.acpi_max", 60.0)])
        self.assertIsNone(self.relation("load_without_sensors", make_snapshot(cpu=92, readings=with_sensor), make_window(cpu=92)))

    def test_every_relation_is_at_most_seen_together_and_carries_evidence(self):
        readings = busy_readings(**dict([avail("power.on_battery", True), avail("power.mode", "Balanced"),
                                         avail("startup.enabled_count", 14), avail("gpu.utilization", 95.0), avail("temp.acpi_max", 80.0)]))
        procs = [proc(i, "chrome.exe", 150) for i in range(1, 15)] + [proc(99, "Photoshop.exe", 2000), proc(98, "OneDrive.exe", 50, cpu=5.0)]
        snap = make_snapshot(cpu=92, mem_pct=91, storage_pct=93, processes=procs, readings=readings, uptime=300)
        facts = Facts(snap, make_window(cpu=92, mem=91, storage=93, readings=readings))
        fired = [rel for rel in (rule.evaluate(facts) for rule in RELATIONSHIP_RULES) if rel is not None]
        self.assertGreaterEqual(len(fired), 7)
        for relation in fired:
            self.assertLessEqual(relation.strength, EvidenceStrength.CORRELATION)
            self.assertTrue(relation.evidence, relation.rule_id)


# --------------------------------------------------------------------------- #
class QuestionTests(unittest.TestCase):
    def test_every_question_answers_on_an_idle_pc(self):
        for question_id in QUESTION_ORDER:
            result = run(question_id, make_snapshot())
            self.assertEqual(result.question_id, question_id)
            self.assertTrue(result.summary)
            self.assertIsInstance(result.relations, list)

    def test_slow_shows_only_notable_findings_and_says_when_nothing_is_wrong(self):
        result = run("slow", make_snapshot())
        self.assertEqual(result.findings, [])
        self.assertIn("Nothing unusual", result.summary)

    def test_ram_always_shows_the_memory_finding(self):
        result = run("ram", make_snapshot())
        self.assertEqual([f.id for f in result.findings], ["memory"])
        self.assertEqual(result.findings[0].level, "normal")

    def test_heat_without_sensors_says_so_and_lists_what_could_not_be_checked(self):
        snap = make_snapshot(readings=dict([missing("temp.acpi_max"), missing("fan.rpm")]))
        result = run("heat", snap)
        thermal = next(f for f in result.findings if f.id == "thermal")
        self.assertIn("cannot say whether the PC is running hot", thermal.happening)
        unchecked = {r.name for r in result.unchecked}
        self.assertTrue({"temp.acpi_max", "fan.rpm"} <= unchecked)

    def test_storage_hands_off_to_the_storage_view(self):
        result = run("storage", make_snapshot(storage_pct=92))
        self.assertEqual(result.findings[0].goto, "storage")

    def test_everything_has_all_findings_sections_and_unverified_rows(self):
        result = run("everything", make_snapshot(readings=dict([missing("temp.acpi_max")])))
        self.assertEqual({f.id for f in result.findings}, set(detectors.DETECTORS) - {"changes"})  # history has its own question
        titles = [s.title for s in result.sections]
        self.assertIn("Right now", titles)
        self.assertIn("What OScope can measure on this PC", titles)
        sources = next(s for s in result.sections if s.title.startswith("What OScope can measure"))
        temp = next(e for e in sources.rows if e.id == "temp.acpi_max")
        self.assertEqual(temp.strength, EvidenceStrength.UNVERIFIED)

    def test_a_spike_does_not_raise_a_cpu_finding(self):
        window = make_window(cpus=[10.0] * 29 + [100.0])
        result = run("slow", make_snapshot(cpu=100.0), window)
        self.assertNotIn("cpu", [f.id for f in result.findings])

    def test_short_history_adds_a_note(self):
        result = run("slow", make_snapshot(), make_window(cpus=[10.0, 10.0]))
        self.assertTrue(any("only just started" in note for note in result.notes))
        self.assertEqual(run("slow", make_snapshot(), make_window(n=30)).notes, [])

    def test_unreadable_core_metrics_are_listed_as_unchecked(self):
        snap = make_snapshot()
        snap.cpu_percent, snap.memory = None, None
        names = {r.name for r in run("slow", snap).unchecked}
        self.assertTrue({"cpu.percent", "memory.percent"} <= names)

    def test_rules_outside_a_question_do_not_appear(self):
        readings = dict([avail("power.on_battery", True), avail("power.mode", "Balanced")])
        snap = make_snapshot(cpu=92, readings=readings)
        window = make_window(cpu=92, readings=readings)
        self.assertNotIn("battery_load", [r.rule_id for r in run("ram", snap, window).relations])
        self.assertIn("battery_load", [r.rule_id for r in run("heat", snap, window).relations])

    def test_load_without_sensors_is_a_heat_only_rule(self):
        snap = make_snapshot(cpu=92, readings=dict([missing("temp.acpi_max")]))
        window = make_window(cpu=92)
        self.assertIn("load_without_sensors", [r.rule_id for r in run("heat", snap, window).relations])
        self.assertNotIn("load_without_sensors", [r.rule_id for r in run("everything", snap, window).relations])


class WorkloadTests(unittest.TestCase):
    def busy_pc(self):
        readings = dict([avail("gpu.utilization", 95.0), avail("gpu.busiest_engine", "3D")])
        return make_snapshot(cpu=92, readings=readings), make_window(cpu=92, readings=readings)

    def test_workload_only_reorders_within_the_same_severity(self):
        snap, window = self.busy_pc()
        general = [f.id for f in run("slow", snap, window, "general").findings]
        gaming = [f.id for f in run("slow", snap, window, "gaming").findings]
        self.assertEqual(sorted(general), sorted(gaming))               # nothing hidden, nothing added
        self.assertEqual(general[0], "cpu")                             # warning outranks info by default
        self.assertEqual(gaming[0], "cpu")                              # ...and still does for gaming

    def test_same_severity_findings_follow_the_workload(self):
        readings = dict([avail("gpu.utilization", 95.0)] + list(busy_readings().items()))
        window = make_window(readings=readings)
        snap = make_snapshot(readings=readings)
        gaming = [f.id for f in run("slow", snap, window, "gaming").findings]
        video = [f.id for f in run("slow", snap, window, "video").findings]
        self.assertLess(gaming.index("gpu"), gaming.index("disk"))      # gaming emphasises the GPU
        self.assertLess(video.index("disk"), video.index("gpu"))        # video editing emphasises the disk

    def test_workload_never_changes_thresholds(self):
        snap = make_snapshot(cpu=70)
        window = make_window(cpu=70)
        for workload in workloads.WORKLOADS:
            self.assertNotIn("cpu", [f.id for f in run("slow", snap, window, workload).findings])

    def test_unknown_workload_falls_back_to_general(self):
        self.assertEqual(workloads.label("nonsense"), workloads.WORKLOADS["general"])
        self.assertEqual(workloads.rank("nonsense", "cpu"), 0)


# --------------------------------------------------------------------------- #
class WordingGuardTests(unittest.TestCase):
    """OScope may say things were seen together. It must never say one made the other happen."""

    def scenarios(self):
        everything = busy_readings(**dict([
            avail("power.on_battery", True), avail("power.mode", "Best power efficiency"), avail("power.battery_percent", 35),
            avail("startup.enabled_count", 14), avail("startup.entries", tuple(StartupEntry(f"A{i}", "a", "Registry", True) for i in range(14))),
            avail("gpu.utilization", 96.0), avail("gpu.busiest_engine", "3D"), avail("temp.acpi_max", 88.0),
            avail("mem.commit_percent", 95.0), avail("mem.commit_used_bytes", 30 * GB), avail("mem.commit_limit_bytes", 32 * GB),
            avail("mem.pages_per_sec", 400.0),
            missing("fan.rpm"),
        ]))
        procs = [proc(i, "chrome.exe", 200) for i in range(1, 20)] + [proc(90, "Photoshop.exe", 3000, cpu=30.0),
                                                                    proc(91, "OneDrive.exe", 80, cpu=6.0)]
        yield make_snapshot(cpu=95, mem_pct=96, storage_pct=96, processes=procs, readings=everything, uptime=200), \
            make_window(cpu=95, mem=96, storage=96, readings=everything)
        yield make_snapshot(), make_window()
        yield make_snapshot(cpu=92, readings=dict([missing("temp.acpi_max"), missing("fan.rpm")])), make_window(cpu=92)
        yield make_snapshot(cpu=92), make_window(cpus=[92.0, 93.0])

    def test_no_causal_wording_in_any_answer(self):
        for snapshot, window in self.scenarios():
            for question_id in QUESTION_ORDER:
                for workload in workloads.WORKLOADS:
                    result = run_question(question_id, snapshot, window, workload)
                    for text in explain.result_texts(result):
                        self.assertEqual(explain.find_forbidden(text), [], f"{question_id}/{workload}: {text}")

    def test_the_guard_itself_catches_causal_words(self):
        for bad in ("Chrome is causing the slowdown", "slow because of memory", "the culprit is Chrome",
                    "due to Photoshop", "this will fix it", "Chrome is responsible"):
            self.assertTrue(explain.find_forbidden(bad), bad)
        self.assertEqual(explain.find_forbidden("Seen together: high memory and busy disk."), [])

    def test_busy_pc_scenario_actually_exercises_every_rule(self):
        snapshot, window = next(iter(self.scenarios()))
        result = run_question("everything", snapshot, window)
        fired = {r.rule_id for r in result.relations}
        expected = {r.id for r in RELATIONSHIP_RULES if r.only_for is None}
        self.assertEqual(fired, expected)  # so the wording test above covered all of their text

    def test_plain_text_export_contains_every_layer(self):
        snapshot, window = next(iter(self.scenarios()))
        text = explain.result_to_text(run_question("slow", snapshot, window, "photoshop"))
        for heading in ("WHAT WE FOUND", "What is happening:", "What is contributing:",
                        "What you could consider:", "THINGS YOU MAY NOT HAVE NOTICED"):
            self.assertIn(heading, text)
        self.assertIn("[Measured]", text)
        idle = explain.result_to_text(run_question("slow", make_snapshot(), make_window()))
        self.assertIn("No relationships between different measurements were found.", idle)
        self.assertIn("COULD NOT CHECK", idle)  # mem commit / gpu / power etc. are not available in the synthetic idle PC

    def test_results_are_pure(self):
        snapshot, window = next(iter(self.scenarios()))
        first = explain.result_to_text(run_question("slow", snapshot, window))
        second = explain.result_to_text(run_question("slow", snapshot, window))
        self.assertEqual(first, second)


class ReportIntegrationTests(unittest.TestCase):
    def test_report_carries_the_evidence_table_and_the_assessment(self):
        from app.core import report
        from app.core.system_info import StaticInfo

        readings = dict([missing("temp.acpi_max"), avail("gpu.utilization", 50.0)])
        snapshot = make_snapshot(cpu=92, readings=readings)
        info = StaticInfo("Windows 11", "Pro", "TESTPC", "Test CPU", 8, 4)
        text = report.build_report(info, snapshot, now=T0, window=make_window(cpu=92, readings=readings), workload="gaming")
        for heading in ("EVIDENCE SOURCES", "OSCOPE'S ASSESSMENT", "WHAT WE FOUND", "COULD NOT CHECK", "DIAGNOSTIC SUMMARY"):
            self.assertIn(heading, text)
        self.assertIn("Workload: Gaming".lower(), text.lower())
        self.assertIn("never as a guessed number", text)
        for causal in explain.find_forbidden(text):
            self.fail(f"causal wording in report: {causal}")

    def test_report_still_builds_without_history(self):
        from app.core import report
        from app.core.system_info import StaticInfo

        text = report.build_report(StaticInfo("Windows 11", "", "PC", None, None, None), make_snapshot(), now=T0)
        self.assertIn("OSCOPE'S ASSESSMENT", text)


if __name__ == "__main__":
    unittest.main()
