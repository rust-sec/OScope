"""Phase 4 tests: history database, recorder, writer thread, retention, queries, "What changed?".

Everything uses temporary folders and fake clocks; nothing touches the real per-user data folder.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import test_analysis as ta  # noqa: E402

from app.analysis import explain, workloads  # noqa: E402
from app.analysis.facts import Facts  # noqa: E402
from app.analysis.orchestrator import run_question  # noqa: E402
from app.analysis.rules import RELATIONSHIP_RULES  # noqa: E402
from app.history import db, queries, retention  # noqa: E402
from app.history.queries import GroupCorrelation, HistoryContext  # noqa: E402
from app.history.recorder import HistoryRecorder  # noqa: E402
from app.history.records import EventRow, MetricRow, ProcessTopRow, insert_event, insert_metric, insert_process_top  # noqa: E402
from app.history.service import HistoryService  # noqa: E402
from app.history.writer import HistoryWriter  # noqa: E402

DAY, HOUR = 86400, 3600
NOW = 1_800_000_000


def memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    db.migrate(conn)
    return conn


def metric(ts, mem=50.0, cpu=20.0, free=100 * 2**30, total=500 * 2**30, commit=None):
    return MetricRow(ts, cpu, mem, commit, None, None, None, None, None, free, total, 15)


# --------------------------------------------------------------------------- #
class DatabaseTests(unittest.TestCase):
    def test_migrate_creates_the_schema_once(self):
        conn = sqlite3.connect(":memory:")
        self.assertEqual(db.migrate(conn), db.MIGRATIONS[-1][0])
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"metric_samples", "process_top", "storage_snapshots", "storage_categories",
                         "storage_dirs", "diagnostic_events"} <= tables)
        self.assertEqual(db.migrate(conn), db.MIGRATIONS[-1][0])  # running again changes nothing
        self.assertIn("dirs_truncated", [r[1] for r in conn.execute("PRAGMA table_info(storage_snapshots)")])

    def test_later_versions_are_applied_in_order_and_only_once(self):
        conn = sqlite3.connect(":memory:")
        migrations = [(1, "CREATE TABLE a(x INTEGER);"), (2, "ALTER TABLE a ADD COLUMN y INTEGER;")]
        self.assertEqual(db.migrate(conn, migrations), 2)
        self.assertEqual(db.migrate(conn, migrations), 2)
        self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(a)")], ["x", "y"])
        migrations.append((3, "ALTER TABLE a ADD COLUMN z INTEGER;"))
        self.assertEqual(db.migrate(conn, migrations), 3)

    def test_a_failing_migration_leaves_the_old_version_intact(self):
        conn = sqlite3.connect(":memory:", isolation_level=None)
        db.migrate(conn, [(1, "CREATE TABLE a(x INTEGER);")])
        with self.assertRaises(sqlite3.Error):
            db.migrate(conn, [(1, "CREATE TABLE a(x INTEGER);"), (2, "CREATE TABLE b(x); THIS IS NOT SQL;")])
        self.assertEqual(db.schema_version(conn), 1)
        self.assertEqual({r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}, {"a"})

    def test_connection_uses_wal_and_foreign_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.open_connection(Path(tmp) / "h.db")
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            conn.close()

    def test_read_only_connection_cannot_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.db"
            conn, _ = db.open_or_recreate(path)
            conn.close()
            reader = db.open_connection(path, read_only=True)
            with self.assertRaises(sqlite3.OperationalError):
                reader.execute("DELETE FROM metric_samples")
            reader.close()

    def test_corrupt_file_is_set_aside_and_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.db"
            path.write_bytes(b"this is definitely not a sqlite database" * 50)
            conn, recreated = db.open_or_recreate(path)
            self.assertTrue(recreated)
            insert_metric(conn, metric(NOW))
            conn.commit()
            conn.close()
            self.assertTrue(list(Path(tmp).glob("h.db.corrupt-*")))
            self.assertEqual(conn.__class__, sqlite3.Connection)

    def test_a_locked_or_unopenable_database_is_never_treated_as_corrupt(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.db"
            conn, _ = db.open_or_recreate(path)
            insert_metric(conn, metric(NOW))
            conn.commit()
            conn.close()
            for message in ("database is locked", "unable to open database file"):
                with mock.patch.object(db, "open_connection", side_effect=sqlite3.OperationalError(message)):
                    with self.assertRaises(sqlite3.OperationalError):
                        db.open_or_recreate(path)
            self.assertFalse(list(Path(tmp).glob("h.db.corrupt-*")))      # nothing was moved aside
            again, recreated = db.open_or_recreate(path)
            self.assertFalse(recreated)
            self.assertEqual(again.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0], 1)  # data intact
            again.close()

    def test_healthy_file_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.db"
            first, _ = db.open_or_recreate(path)
            insert_metric(first, metric(NOW))
            first.commit()
            first.close()
            second, recreated = db.open_or_recreate(path)
            self.assertFalse(recreated)
            self.assertEqual(second.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0], 1)
            second.close()


# --------------------------------------------------------------------------- #
class RecorderTests(unittest.TestCase):
    def run_recorder(self, snapshots, step=2.0, interval=30):
        ops, clock = [], [1000.0]
        recorder = HistoryRecorder(ops.append, lambda: clock[0], interval)
        for snapshot in snapshots:
            recorder.on_snapshot(snapshot)
            clock[0] += step
        conn = memory_db()
        for op in ops:
            op(conn)
        return conn

    def test_many_snapshots_become_few_rows(self):
        readings = dict([ta.avail("mem.commit_percent", 60.0), ta.avail("gpu.utilization", 40.0),
                         ta.avail("temp.acpi_max", 55.0), ta.avail("power.on_battery", True)] + list(ta.busy_readings().items()))
        snaps = [ta.make_snapshot(cpu=10.0 + i, mem_pct=50.0, readings=readings) for i in range(40)]  # 80 s of data
        conn = self.run_recorder(snaps)
        rows = conn.execute("SELECT * FROM metric_samples ORDER BY ts").fetchall()
        self.assertEqual(len(rows), 2)                      # not 40
        first = rows[0]
        self.assertGreaterEqual(first["samples"], 15)
        self.assertAlmostEqual(first["mem_pct"], 50.0)
        self.assertAlmostEqual(first["commit_pct"], 60.0)
        self.assertAlmostEqual(first["gpu_pct"], 40.0)
        self.assertAlmostEqual(first["temp_c"], 55.0)
        self.assertEqual(first["on_battery"], 1)
        self.assertAlmostEqual(first["disk_read_bps"], 90.0 * 2**20)
        self.assertEqual(first["total_bytes"], 500 * 2**30)

    def test_cpu_is_averaged_over_the_window(self):
        snaps = [ta.make_snapshot(cpu=c) for c in ([10.0, 30.0] * 8 + [50.0])]  # 17 snapshots, 2 s apart
        conn = self.run_recorder(snaps)
        row = conn.execute("SELECT cpu, samples FROM metric_samples").fetchone()
        self.assertEqual(row["samples"], 16)             # the window closes after 30 s; the 17th starts the next one
        self.assertAlmostEqual(row["cpu"], (8 * 10 + 8 * 30) / 16)

    def test_missing_readings_are_null_not_zero(self):
        conn = self.run_recorder([ta.make_snapshot() for _ in range(17)])
        row = conn.execute("SELECT * FROM metric_samples").fetchone()
        for column in ("commit_pct", "gpu_pct", "temp_c", "on_battery", "disk_read_bps"):
            self.assertIsNone(row[column], column)

    def test_nothing_is_written_before_the_window_is_over(self):
        conn = self.run_recorder([ta.make_snapshot() for _ in range(5)])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0], 0)

    def test_top_programs_are_kept_every_other_row_and_limited_to_ten(self):
        procs = [ta.proc(i, f"app{i}.exe", mb=10 * i) for i in range(1, 15)]
        conn = self.run_recorder([ta.make_snapshot(processes=procs) for _ in range(70)])   # ~140 s -> 4 metric rows
        metrics = conn.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0]
        tops = conn.execute("SELECT COUNT(DISTINCT ts) FROM process_top").fetchone()[0]
        self.assertGreaterEqual(metrics, 4)
        self.assertEqual(tops, metrics // 2)
        ts = conn.execute("SELECT MIN(ts) FROM process_top").fetchone()[0]
        names = [r["name"] for r in conn.execute("SELECT name FROM process_top WHERE ts=? ORDER BY rank", (ts,))]
        self.assertEqual(len(names), 10)
        self.assertEqual(names[0], "app14.exe")             # rank 1 = most memory

    def test_process_rows_contain_only_names_and_numbers(self):
        procs = [ta.proc(1, "chrome.exe", 100)]
        conn = self.run_recorder([ta.make_snapshot(processes=procs) for _ in range(40)])
        columns = [r[1] for r in conn.execute("PRAGMA table_info(process_top)")]
        self.assertEqual(columns, ["ts", "rank", "name", "proc_count", "cpu", "mem_bytes"])


# --------------------------------------------------------------------------- #
class WriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "h.db"

    def tearDown(self):
        self.tmp.cleanup()

    def count(self, table="metric_samples"):
        conn = db.open_connection(self.path, read_only=True)
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()

    def writer(self, **kwargs):
        writer = HistoryWriter(self.path, **kwargs)
        writer.start()
        self.assertTrue(writer.ready.wait(5))
        self.addCleanup(writer.close)
        return writer

    def test_writes_happen_on_the_history_thread_not_the_caller(self):
        writer = self.writer()
        writer.enqueue(lambda conn: insert_metric(conn, metric(NOW)))
        self.assertTrue(writer.flush())
        self.assertEqual(self.count(), 1)
        self.assertEqual(writer.thread_name_of_last_write, "oscope-history")
        self.assertNotEqual(writer.thread_name_of_last_write, threading.current_thread().name)

    def test_rows_wait_for_the_commit_interval_unless_flushed(self):
        writer = self.writer(commit_interval=1000.0)
        writer.enqueue(lambda conn: insert_metric(conn, metric(NOW)))
        import time
        time.sleep(0.8)
        self.assertEqual(self.count(), 0)                 # batched, not written yet
        self.assertTrue(writer.flush())
        self.assertEqual(self.count(), 1)

    def test_close_writes_whatever_is_pending(self):
        writer = self.writer(commit_interval=1000.0)
        for i in range(5):
            writer.enqueue(lambda conn, i=i: insert_metric(conn, metric(NOW + i)))
        writer.close()
        self.assertEqual(self.count(), 5)

    def test_a_failing_operation_discards_only_its_batch(self):
        writer = self.writer()
        writer.enqueue(lambda conn: insert_metric(conn, metric(NOW)))
        writer.enqueue(lambda conn: conn.execute("INSERT INTO no_such_table VALUES (1)"))
        self.assertTrue(writer.flush())                   # flush still completes
        self.assertEqual(self.count(), 0)                 # the whole batch was rolled back
        self.assertIn("OperationalError", writer.last_error)
        writer.enqueue(lambda conn: insert_metric(conn, metric(NOW + 1)))
        self.assertTrue(writer.flush())
        self.assertEqual(self.count(), 1)                 # and the writer carries on

    def test_a_full_queue_drops_the_oldest_and_counts_them(self):
        writer = HistoryWriter(self.path, max_queue=3)    # not started, so nothing drains
        for i in range(5):
            writer.enqueue(lambda conn, i=i: i)
        self.assertEqual(writer.dropped, 2)
        self.assertEqual(writer._queue.qsize(), 3)

    def test_unusable_location_means_unavailable_not_a_crash(self):
        writer = HistoryWriter(Path(self.tmp.name) / "missing-folder" / "h.db")
        writer.start()
        self.assertTrue(writer.ready.wait(5))
        self.assertFalse(writer.available)
        self.assertIsNotNone(writer.last_error)
        writer.enqueue(lambda conn: None)                 # still never blocks
        writer.close()

    def test_corrupt_database_is_replaced_and_reported(self):
        self.path.write_bytes(b"garbage" * 100)
        writer = self.writer()
        self.assertTrue(writer.recreated)
        writer.enqueue(lambda conn: insert_metric(conn, metric(NOW)))
        self.assertTrue(writer.flush())
        self.assertEqual(self.count(), 1)


# --------------------------------------------------------------------------- #
class RetentionTests(unittest.TestCase):
    def test_old_rows_are_pruned_and_recent_ones_kept(self):
        conn = memory_db()
        for age_days, in ((1,), (13,), (15,), (30,)):
            insert_metric(conn, metric(NOW - age_days * DAY))
        for age_days, in ((2,), (6,), (8,)):
            insert_process_top(conn, [ProcessTopRow(NOW - age_days * DAY, 1, "a.exe", 1, 1.0, 10)])
        for age_days, in ((10,), (80,), (100,)):
            insert_event(conn, EventRow(NOW - age_days * DAY, "slow", "General", "cpu", "warning", "t", []))
        removed = retention.prune(conn, NOW)
        self.assertEqual(removed["metric_samples"], 2)
        self.assertEqual(removed["process_top"], 1)
        self.assertEqual(removed["diagnostic_events"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0], 2)

    def test_only_the_newest_snapshots_of_each_folder_are_kept(self):
        conn = memory_db()
        for root in ("D:\\Videos", "D:\\Photos"):
            for i in range(25):
                cursor = conn.execute(
                    "INSERT INTO storage_snapshots(ts, root, total_size, file_count, dir_count, denied_count, skipped_count) "
                    "VALUES (?,?,?,?,?,?,?)", (NOW + i, root, 1, 1, 1, 0, 0))
                conn.execute("INSERT INTO storage_categories VALUES (?,?,?,?)", (cursor.lastrowid, "Videos", 1, 1))
                conn.execute("INSERT INTO storage_dirs VALUES (?,?,?,?)", (cursor.lastrowid, root, 1, 0))
        retention.prune(conn, NOW + 100)
        for root in ("D:\\Videos", "D:\\Photos"):
            rows = conn.execute("SELECT ts FROM storage_snapshots WHERE root=? ORDER BY ts", (root,)).fetchall()
            self.assertEqual(len(rows), 20)
            self.assertEqual(rows[0]["ts"], NOW + 5)        # the 5 oldest went
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM storage_categories").fetchone()[0], 40)  # children cascaded
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM storage_dirs").fetchone()[0], 40)

    def test_clear_all_empties_every_table_but_keeps_the_schema(self):
        conn = memory_db()
        insert_metric(conn, metric(NOW))
        insert_event(conn, EventRow(NOW, "slow", "General", "cpu", "warning", "t", []))
        retention.clear_all(conn)
        for table in db.DATA_TABLES:
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        insert_metric(conn, metric(NOW))  # still usable


# --------------------------------------------------------------------------- #
class QueryTests(unittest.TestCase):
    def test_recent_metrics_and_events(self):
        conn = memory_db()
        for age in (5 * HOUR, 2 * HOUR, 10 * 60):
            insert_metric(conn, metric(NOW - age))
        insert_event(conn, EventRow(NOW - 100, "slow", "General", "memory", "warning", "High memory use",
                                    [{"label": "Chrome", "value": "3 GB"}]))
        conn.execute("INSERT INTO diagnostic_events(ts, question, workload, finding_id, level, title, evidence_json) "
                     "VALUES (?,?,?,?,?,?,?)", (NOW - 50, "slow", "General", "cpu", "info", "High CPU", "{not json"))
        rows = queries.recent_metrics(conn, NOW - 3 * HOUR)
        self.assertEqual([r.ts for r in rows], [NOW - 2 * HOUR, NOW - 600])
        events = queries.recent_events(conn, NOW - HOUR)
        self.assertEqual(events[0].evidence, [{"label": "Chrome", "value": "3 GB"}])
        self.assertEqual(events[1].evidence, [])           # unreadable evidence does not break the read

    def test_memory_high_groups_counts_moments_not_rows(self):
        conn = memory_db()
        for i in range(12):
            ts = NOW - (12 - i) * 60
            insert_metric(conn, metric(ts, mem=90.0))
            insert_process_top(conn, [ProcessTopRow(ts, 1, "Chrome", 20, 1.0, 5), ProcessTopRow(ts, 2, "Photoshop", 1, 1.0, 4),
                                      ProcessTopRow(ts, 4, "Other", 1, 1.0, 1)])
        for i in range(5):  # low-memory moments must not count
            ts = NOW - 2 * HOUR - i * 60
            insert_metric(conn, metric(ts, mem=40.0))
            insert_process_top(conn, [ProcessTopRow(ts, 1, "Idle", 1, 0.0, 1)])
        groups, samples = queries.memory_high_groups(conn, NOW - 3 * HOUR)
        self.assertEqual(samples, 12)
        self.assertEqual([(g.name, g.hits) for g in groups], [("Chrome", 12), ("Photoshop", 12)])  # rank 4 is outside the top 3

    def test_build_context(self):
        conn = memory_db()
        insert_metric(conn, metric(NOW - 60))
        context = queries.build_context(conn, NOW)
        self.assertEqual(context.now, NOW)
        self.assertEqual(len(context.metrics), 1)
        self.assertEqual(context.memory_high_samples, 0)


# --------------------------------------------------------------------------- #
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = [NOW]

    def service(self, enabled=True):
        service = HistoryService(Path(self.tmp.name), enabled, clock=lambda: self.clock[0])
        self.addCleanup(service.close)
        return service

    def feed(self, service, seconds=100, step=2, **snapshot_kwargs):
        for _ in range(seconds // step):
            service.on_snapshot(ta.make_snapshot(**snapshot_kwargs))
            self.clock[0] += step

    def test_recording_then_reading_back(self):
        service = self.service()
        self.feed(service, seconds=100, mem_pct=70.0)
        context = service.context()
        self.assertIsNotNone(context)
        self.assertGreaterEqual(len(context.metrics), 3)
        self.assertAlmostEqual(context.metrics[0].mem_pct, 70.0)

    def test_disabled_service_records_nothing_and_creates_no_file(self):
        service = self.service(enabled=False)
        self.feed(service)
        self.assertIsNone(service.writer)
        self.assertFalse((Path(self.tmp.name) / db.DB_FILENAME).exists())
        self.assertIn("off", service.status_text())
        service.set_enabled(True)
        self.feed(service)
        self.assertIsNotNone(service.writer)

    def test_turning_it_off_keeps_what_was_stored(self):
        service = self.service()
        self.feed(service)
        service.set_enabled(False)
        self.feed(service)
        self.assertGreaterEqual(len(service.context().metrics), 1)

    def test_no_data_folder_means_unavailable_and_everything_is_a_noop(self):
        service = HistoryService(None, True)
        service.on_snapshot(ta.make_snapshot())
        self.assertIsNone(service.context())
        self.assertFalse(service.clear())
        self.assertIn("unavailable", service.status_text())
        service.close()

    def test_unwritable_location_is_reported(self):
        service = HistoryService(Path(self.tmp.name) / "nope" / "deeper", True, clock=lambda: self.clock[0])
        self.addCleanup(service.close)
        service.on_snapshot(ta.make_snapshot())
        service.writer.ready.wait(5)
        self.assertIn("unavailable", service.status_text())

    def test_notable_findings_become_events_and_repeats_are_throttled(self):
        service = self.service()
        result = run_question("slow", ta.make_snapshot(cpu=92, mem_pct=91, storage_pct=93), ta.make_window(cpu=92, mem=91, storage=93))
        first = service.record_result(result)
        self.assertGreaterEqual(first, 3)
        self.assertEqual(service.record_result(result), 0)             # same findings, same minute
        self.clock[0] += 700
        self.assertEqual(service.record_result(result), first)         # logged again after 10 minutes
        events = service.context().events
        self.assertTrue({"cpu", "memory", "storage"} <= {e.finding_id for e in events})
        normal = run_question("ram", ta.make_snapshot(), ta.make_window())
        self.assertEqual(service.record_result(normal), 0)             # normal findings are not remembered

    def test_event_evidence_holds_labels_and_values_only(self):
        service = self.service()
        service.record_result(run_question("ram", ta.make_snapshot(mem_pct=91), ta.make_window(mem=91)))
        event = service.context().events[0]
        self.assertTrue(all(set(item) == {"label", "value"} for item in event.evidence))
        self.assertLessEqual(len(event.evidence), 8)

    def test_clear_deletes_everything(self):
        service = self.service()
        self.feed(service)
        service.record_result(run_question("ram", ta.make_snapshot(mem_pct=91), ta.make_window(mem=91)))
        self.assertTrue(service.clear())
        context = service.context()
        self.assertEqual((len(context.metrics), len(context.events)), (0, 0))

    def test_context_without_any_file_is_none(self):
        self.assertIsNone(self.service().context())


# --------------------------------------------------------------------------- #
def history(metrics=(), events=(), groups=(), samples=0):
    return HistoryContext(NOW, tuple(metrics), tuple(events), tuple(groups), samples)


def steady(minutes=90, mem=50.0, step=60, **kwargs):
    return [metric(NOW - m * 60, mem=mem, **kwargs) for m in range(minutes, 0, -1)]


class ChangesTests(unittest.TestCase):
    def ask(self, ctx):
        return run_question("changed", ta.make_snapshot(), ta.make_window(), "general", ctx)

    def finding(self, ctx):
        return self.ask(ctx).findings[0]

    def test_without_history_it_says_so_and_does_not_guess(self):
        finding = self.finding(None)
        self.assertEqual(finding.level, "normal")
        self.assertIn("too little recorded history", finding.happening)
        self.assertEqual(finding.contributing[0].strength, ta.EvidenceStrength.UNVERIFIED)

    def test_too_few_measurements(self):
        finding = self.finding(history(metrics=[metric(NOW - 60), metric(NOW - 30)]))
        self.assertIn("only 2 measurements", finding.happening)

    def test_a_steady_pc_shows_nothing_changed(self):
        finding = self.finding(history(metrics=steady()))
        self.assertEqual(finding.level, "normal")
        self.assertIn("Nothing changed much", finding.happening)

    def test_a_memory_jump_is_reported_with_both_numbers(self):
        rows = [metric(NOW - m * 60, mem=45.0) for m in range(90, 15, -1)] + [metric(NOW - m * 60, mem=72.0) for m in range(15, 0, -1)]
        finding = self.finding(history(metrics=rows))
        self.assertEqual(finding.level, "info")
        self.assertIn("memory use (+27 points)", finding.happening)
        row = next(e for e in finding.contributing if e.id == "history.mem_pct")
        self.assertIn("72% on average in the last 15 minutes, 45% in the hour before", row.value_text)

    def test_a_small_change_is_shown_but_not_flagged(self):
        rows = [metric(NOW - m * 60, mem=50.0) for m in range(90, 15, -1)] + [metric(NOW - m * 60, mem=58.0) for m in range(15, 0, -1)]
        finding = self.finding(history(metrics=rows))
        self.assertEqual(finding.level, "normal")
        self.assertTrue(any(e.id == "history.mem_pct" for e in finding.contributing))

    def test_a_big_drop_in_free_space_points_to_the_storage_view(self):
        gb = 2**30
        rows = [metric(NOW - m * 60, free=(200 if m > 60 else 190) * gb - m * 0) for m in range(120, 0, -1)]
        rows = [metric(NOW - m * 60, free=(200 - (120 - m) * 0.1) * gb) for m in range(120, 0, -1)]   # 200 GB -> ~188 GB
        finding = self.finding(history(metrics=rows))
        self.assertEqual(finding.goto, "storage")
        self.assertIn("free space on the system drive", finding.happening)
        self.assertTrue(any(e.id == "history.free" for e in finding.contributing))

    def test_free_space_is_not_compared_over_a_short_span(self):
        rows = [metric(NOW - m * 60, free=(200 - m) * 2**30) for m in range(20, 0, -1)]   # only 20 minutes
        self.assertFalse(any(e.id == "history.free" for e in self.finding(history(metrics=rows)).contributing))

    def test_recent_events_are_listed(self):
        events = [EventRow(NOW - 600, "slow", "General", "memory", "warning", "High memory use", []),
                  EventRow(NOW - 300, "slow", "General", "cpu", "normal", "ignored", [])]
        finding = self.finding(history(metrics=steady(), events=events))
        rows = [e for e in finding.contributing if e.id.startswith("history.event.")]
        self.assertEqual(len(rows), 1)
        self.assertIn("High memory use (warning)", rows[0].value_text)

    def test_correlation_rule_needs_enough_moments_and_a_clear_share(self):
        strong = history(metrics=steady(), groups=[GroupCorrelation("Chrome", 11, 12)], samples=12)
        relation = self.ask(strong).relations
        self.assertEqual([r.rule_id for r in relation], ["history_memory_groups"])
        self.assertIn("association in the recorded data", relation[0].text)
        self.assertEqual(relation[0].evidence[0].strength, ta.EvidenceStrength.CORRELATION)
        few = history(metrics=steady(), groups=[GroupCorrelation("Chrome", 5, 5)], samples=5)
        self.assertEqual(self.ask(few).relations, [])
        weak = history(metrics=steady(), groups=[GroupCorrelation("Chrome", 5, 12)], samples=12)
        self.assertEqual(self.ask(weak).relations, [])
        no_history = self.ask(None)
        self.assertEqual(no_history.relations, [])

    def test_history_rule_belongs_to_the_changed_question_only(self):
        rule = next(r for r in RELATIONSHIP_RULES if r.id == "history_memory_groups")
        self.assertEqual(rule.only_for, frozenset({"changed"}))
        ctx = history(metrics=steady(), groups=[GroupCorrelation("Chrome", 12, 12)], samples=12)
        everything = run_question("everything", ta.make_snapshot(), ta.make_window(), "general", ctx)
        self.assertNotIn("history_memory_groups", [r.rule_id for r in everything.relations])

    def test_history_answers_use_no_causal_wording(self):
        rows = [metric(NOW - m * 60, mem=45.0) for m in range(120, 15, -1)] + [metric(NOW - m * 60, mem=80.0) for m in range(15, 0, -1)]
        events = [EventRow(NOW - 600, "slow", "General", "memory", "warning", "High memory use", [])]
        ctx = history(metrics=rows, events=events, groups=[GroupCorrelation("Chrome", 12, 12)], samples=12)
        for workload in workloads.WORKLOADS:
            result = run_question("changed", ta.make_snapshot(), ta.make_window(), workload, ctx)
            for text in explain.result_texts(result):
                self.assertEqual(explain.find_forbidden(text), [], text)

    def test_facts_history_token(self):
        facts = Facts(ta.make_snapshot(), ta.make_window(), "general", history(metrics=steady(minutes=3)))
        self.assertFalse(facts.has("history"))                    # 3 rows is not enough
        self.assertTrue(Facts(ta.make_snapshot(), ta.make_window(), "general", history(metrics=steady())).has("history"))


# --------------------------------------------------------------------------- #
# Storage snapshots and comparison
# --------------------------------------------------------------------------- #
from app.core.tree_scanner import TreeScanner  # noqa: E402
from app.history import storage as storage_history  # noqa: E402
from app.history.storage import StorageSnapshot  # noqa: E402


def snapshot(id=1, ts=NOW, root="D:\\Media", total=1000, cats=None, dirs=None, denied=0, elevated=False, truncated=False):
    return StorageSnapshot(id, ts, root, total, 10, 2, denied, 0, elevated, truncated,
                           dict(cats or {}), dict(dirs or {}))


def tree_on_disk(tmp: Path, sizes: dict) -> object:
    for relative, size in sizes.items():
        target = tmp / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * size)
    return TreeScanner(str(tmp)).scan()


class StorageCompareTests(unittest.TestCase):
    def test_category_and_folder_deltas(self):
        older = snapshot(cats={"Videos": (500, 2), "Images": (300, 5)}, dirs={"D:\\Media\\Raw": 400, "D:\\Media\\Photos": 300}, total=800)
        newer = snapshot(id=2, ts=NOW + DAY, cats={"Videos": (900, 3), "Images": (250, 5), "Audio": (50, 1)},
                         dirs={"D:\\Media\\Raw": 800, "D:\\Media\\Photos": 250}, total=1200)
        comparison = storage_history.compare(older, newer)
        self.assertEqual(comparison.total_delta, 400)
        deltas = {c.category: c.delta for c in comparison.categories}
        self.assertEqual(deltas, {"Videos": 400, "Images": -50, "Audio": 50})
        self.assertEqual(comparison.categories[0].category, "Videos")            # biggest change first
        changed = {PureWindowsPath(c.path).name: c.delta for c in comparison.changed_dirs}
        self.assertEqual(changed, {"Raw": 400, "Photos": -50})
        self.assertEqual(comparison.warnings, [])

    def test_unchanged_folders_are_left_out(self):
        same = {"D:\\Media\\A": 100}
        comparison = storage_history.compare(snapshot(dirs=same), snapshot(id=2, dirs=dict(same)))
        self.assertEqual(comparison.changed_dirs, [])

    def test_a_new_or_removed_folder_is_exact_when_the_other_list_is_complete(self):
        older = snapshot(dirs={"D:\\Media\\Old": 70})
        newer = snapshot(id=2, dirs={"D:\\Media\\New": 90})
        changes = {PureWindowsPath(c.path).name: (c.delta, c.note) for c in storage_history.compare(older, newer).changed_dirs}
        self.assertEqual(changes["New"], (90, "new folder"))
        self.assertEqual(changes["Old"], (-70, "folder no longer present"))

    def test_missing_from_a_cut_short_list_is_unknown_not_zero(self):
        older = snapshot(dirs={"D:\\Media\\Big": 500}, truncated=True)            # the older list was cut short
        newer = snapshot(id=2, dirs={"D:\\Media\\Big": 500, "D:\\Media\\Mystery": 40})
        comparison = storage_history.compare(older, newer)
        mystery = next(c for c in comparison.changed_dirs if c.path.endswith("Mystery"))
        self.assertIsNone(mystery.delta)                                         # cannot be known
        self.assertEqual((mystery.before, mystery.after), (None, 40))
        self.assertIn("not in the older scan's list", mystery.note)
        self.assertTrue(any("Only the biggest folders" in w for w in comparison.warnings))

    def test_known_changes_come_before_unknown_ones(self):
        older = snapshot(dirs={"D:\\Media\\A": 100}, truncated=True)
        newer = snapshot(id=2, dirs={"D:\\Media\\A": 120, "D:\\Media\\Z": 999})
        order = [c.delta for c in storage_history.compare(older, newer).changed_dirs]
        self.assertEqual(order, [20, None])

    def test_scans_that_are_not_like_for_like_say_so(self):
        older = snapshot(denied=0, elevated=False)
        newer = snapshot(id=2, denied=40, elevated=True)
        warnings = storage_history.compare(older, newer).warnings
        self.assertTrue(any("0 unreadable items before, 40 now" in w for w in warnings))
        self.assertTrue(any("administrator" in w for w in warnings))
        unknown = storage_history.compare(snapshot(elevated=None), snapshot(id=2, elevated=True)).warnings
        self.assertFalse(any("administrator" in w for w in unknown))             # an unknown state is not a mismatch

    def test_different_folders_cannot_be_compared(self):
        with self.assertRaises(ValueError):
            storage_history.compare(snapshot(root="D:\\Media"), snapshot(id=2, root="D:\\Other"))

    def test_same_root_ignores_trailing_separators(self):
        self.assertTrue(storage_history.same_root("/a/b", "/a/b/"))
        self.assertFalse(storage_history.same_root("/a/b", "/a/c"))


class StorageSnapshotStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.scan_root = Path(self.tmp.name) / "scanned"
        self.clock = [NOW]
        self.service = HistoryService(self.data, True, clock=lambda: self.clock[0])
        self.addCleanup(self.service.close)

    def scan(self, sizes):
        import shutil
        if self.scan_root.exists():
            shutil.rmtree(self.scan_root)
        self.scan_root.mkdir()
        return tree_on_disk(self.scan_root, sizes)

    def test_a_scan_round_trips_through_the_database(self):
        result = self.scan({"Videos/a.mp4": 5000, "Videos/b.mp4": 1000, "Docs/r.pdf": 200})
        self.assertTrue(self.service.save_storage_scan(result, elevated=False))
        saved = self.service.storage_snapshots(str(self.scan_root))
        self.assertEqual(len(saved), 1)
        conn = db.open_connection(self.service.path, read_only=True)
        full = storage_history.load_snapshot(conn, saved[0].id)
        conn.close()
        self.assertEqual(full.total_size, 6200)
        self.assertEqual(full.categories["Videos"], (6000, 2))
        self.assertEqual(full.categories["Documents"], (200, 1))
        self.assertEqual({Path(p).name: s for p, s in full.dirs.items()}, {"Videos": 6000, "Docs": 200})
        self.assertIs(full.elevated, False)
        self.assertFalse(full.dirs_truncated)

    def test_cancelled_and_failed_scans_are_not_saved(self):
        result = self.scan({"a.bin": 10})
        result.cancelled = True
        self.assertFalse(self.service.save_storage_scan(result))
        broken = TreeScanner(str(self.scan_root / "missing")).scan()
        self.assertFalse(self.service.save_storage_scan(broken))
        self.assertEqual(self.service.storage_snapshots(str(self.scan_root)), [])

    def test_two_scans_compare_and_changes_are_found(self):
        self.service.save_storage_scan(self.scan({"Videos/a.mp4": 5000, "Docs/r.pdf": 200}))
        self.clock[0] += DAY
        self.service.save_storage_scan(self.scan({"Videos/a.mp4": 5000, "Videos/new.mp4": 3000, "Docs/r.pdf": 150}))
        comparison = self.service.compare_latest(str(self.scan_root))
        self.assertEqual(comparison.total_delta, 2950)
        self.assertEqual({c.category: c.delta for c in comparison.categories}, {"Videos": 3000, "Documents": -50})
        self.assertEqual({Path(c.path).name: c.delta for c in comparison.changed_dirs}, {"Videos": 3000, "Docs": -50})
        self.assertLess(comparison.older.ts, comparison.newer.ts)

    def test_one_scan_is_not_enough_to_compare(self):
        self.service.save_storage_scan(self.scan({"a.bin": 10}))
        self.assertIsNone(self.service.compare_latest(str(self.scan_root)))
        self.assertIsNone(HistoryService(None).compare_latest("x"))

    def test_other_folders_do_not_mix_in(self):
        self.service.save_storage_scan(self.scan({"a.bin": 10}))
        self.assertEqual(self.service.storage_snapshots(str(Path(self.tmp.name) / "elsewhere")), [])

    def test_history_off_saves_nothing(self):
        self.service.set_enabled(False)
        self.assertFalse(self.service.save_storage_scan(self.scan({"a.bin": 10})))

    def test_the_list_of_big_folders_is_flagged_when_it_had_to_be_cut(self):
        sizes = {f"d{i:03}/f.bin": 10 + i for i in range(storage_history.DIR_LIST_LIMIT + 20)}
        self.service.save_storage_scan(self.scan(sizes))
        summary = self.service.storage_snapshots(str(self.scan_root))[0]
        self.assertTrue(summary.dirs_truncated)
        conn = db.open_connection(self.service.path, read_only=True)
        self.assertEqual(len(storage_history.load_snapshot(conn, summary.id).dirs), storage_history.DIR_LIST_LIMIT)
        conn.close()

    def test_a_version_1_database_is_upgraded_in_place_without_losing_data(self):
        path = self.data / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(f"BEGIN;\n{db.SCHEMA_V1}\nPRAGMA user_version = 1;\nCOMMIT;")
        conn.execute("INSERT INTO storage_snapshots(ts, root, total_size, file_count, dir_count, denied_count, skipped_count) "
                     "VALUES (1, 'D:\\Old', 5, 1, 1, 0, 0)")
        conn.commit()
        conn.close()
        upgraded, recreated = db.open_or_recreate(path)
        self.assertFalse(recreated)
        self.assertEqual(db.schema_version(upgraded), 2)
        row = upgraded.execute("SELECT root, dirs_truncated FROM storage_snapshots").fetchone()
        self.assertEqual((row["root"], row["dirs_truncated"]), ("D:\\Old", 0))
        upgraded.close()


if __name__ == "__main__":
    unittest.main()
