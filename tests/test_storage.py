"""Phase 5 tests (scanner side): tree roll-ups, folding, denied/linked/cloud/hidden reporting, helpers.

Platform behaviour is injected (scandir, flags, reparse detection) because this sandbox runs as root on
Linux, where permission errors, junctions and cloud placeholders cannot be created for real.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import tree_scanner, tree_utils  # noqa: E402
from app.core.platform_ops import FileFlags  # noqa: E402
from app.core.tree_scanner import ScanStatus, TreeNode, TreeScanner  # noqa: E402

MB = 1024 * 1024


def write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def assert_children_add_up(test: unittest.TestCase, node: TreeNode) -> None:
    """The core promise: a folder's size is exactly the sum of its children (recursively)."""
    stack = [node]
    while stack:
        current = stack.pop()
        if current.is_dir:
            test.assertEqual(sum(c.size for c in current.children), current.size, current.path)
            stack.extend(current.children)


class ScannerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def scan(self, **kwargs):
        return TreeScanner(str(self.root), **kwargs).scan()


class RollUpTests(ScannerTestCase):
    def setUp(self):
        super().setUp()
        write(self.root / "a" / "one.bin", 3 * MB)
        write(self.root / "a" / "deep" / "two.txt", 1000)
        write(self.root / "a" / "deep" / "three.txt", 500)
        write(self.root / "b" / "x.mp4", 2 * MB)
        write(self.root / "top.txt", 100)

    def test_counts_and_modified_times_roll_up(self):
        tree = self.scan().tree
        a = next(c for c in tree.children if c.name == "a")
        self.assertEqual((a.file_count, a.dir_count), (3, 1))
        self.assertEqual((tree.file_count, tree.dir_count), (5, 3))
        two = next(c for c in a.children if c.name == "deep").children[0]
        self.assertIsNotNone(two.mtime)
        self.assertEqual(two.file_count, 0)

    def test_children_always_add_up(self):
        assert_children_add_up(self, self.scan().tree)

    def test_category_totals_are_exact_and_cover_every_byte(self):
        result = self.scan()
        totals = result.category_totals
        self.assertEqual(totals["Videos"], (2 * MB, 1))
        self.assertEqual(totals["Documents"], (1600, 3))
        self.assertEqual(sum(size for size, _count in totals.values()), result.total_size)
        self.assertEqual(sum(count for _size, count in totals.values()), result.file_count)

    def test_largest_dirs_match_the_old_scanner_for_untied_sizes(self):
        from app.core.storage_manager import DirectoryScanner

        base = DirectoryScanner(str(self.root)).scan()
        self.assertEqual(self.scan().largest_dirs, base.largest_dirs)


class FoldingTests(ScannerTestCase):
    def test_smallest_files_are_folded_and_nothing_is_lost(self):
        for i in range(1, 11):
            write(self.root / "many" / f"f{i:02}.bin", i * 1000)       # 1 KB .. 10 KB
        result = self.scan(max_files_per_folder=3)
        many = next(c for c in result.tree.children if c.name == "many")
        files = [c for c in many.children if not c.synthetic]
        folded = [c for c in many.children if c.synthetic]
        self.assertEqual(sorted(c.name for c in files), ["f08.bin", "f09.bin", "f10.bin"])   # the three largest kept
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0].size, sum(i * 1000 for i in range(1, 8)))
        self.assertEqual(folded[0].file_count, 7)
        self.assertIn("7 smaller files", folded[0].name)
        self.assertEqual(folded[0].path, "")
        self.assertEqual(many.size, 55_000)                               # folder size is still exact
        self.assertEqual(result.folded_files, 7)
        self.assertFalse(result.tree_truncated)                           # a per-folder fold is not a truncation
        assert_children_add_up(self, result.tree)

    def test_a_folder_within_the_limit_has_no_synthetic_item(self):
        for i in range(3):
            write(self.root / "few" / f"f{i}.bin", 100)
        result = self.scan(max_files_per_folder=3)
        self.assertEqual(result.folded_files, 0)
        self.assertFalse(any(c.synthetic for c in result.tree.children[0].children))

    def test_compaction_keeps_memory_bounded_for_a_huge_flat_folder(self):
        for i in range(500):
            write(self.root / "flat" / f"f{i:04}.bin", 10 + i)
        scanner = TreeScanner(str(self.root), max_files_per_folder=50)
        result = scanner.scan()
        flat = result.tree.children[0]
        self.assertEqual(len(flat.children), 51)                           # 50 largest + one folded item
        self.assertLessEqual(scanner._tree_nodes_built, 50)                # the cap counts surviving nodes
        self.assertEqual(flat.size, sum(10 + i for i in range(500)))
        self.assertEqual(result.folded_files, 450)
        kept = sorted(c.size for c in flat.children if not c.synthetic)
        self.assertEqual(kept, sorted(10 + i for i in range(450, 500)))     # exactly the 50 biggest
        assert_children_add_up(self, result.tree)

    def test_global_node_cap_still_keeps_every_folder_exact(self):
        for i in range(6):
            write(self.root / "d1" / f"a{i}.bin", 100 * (i + 1))
            write(self.root / "d2" / f"b{i}.bin", 50 * (i + 1))
        result = self.scan(max_tree_nodes=4)
        self.assertTrue(result.tree_truncated)
        self.assertEqual(result.total_size, 2100 + 1050)
        self.assertEqual(result.file_count, 12)
        self.assertGreater(result.folded_files, 0)
        assert_children_add_up(self, result.tree)


class HiddenAndCloudTests(ScannerTestCase):
    def test_hidden_items_are_flagged_and_counted(self):
        write(self.root / ".secret.txt", 2000)
        write(self.root / "visible.txt", 1000)
        write(self.root / ".cache" / "blob.bin", 5000)
        result = self.scan()
        by_name = {c.name: c for c in result.tree.children}
        self.assertTrue(by_name[".secret.txt"].hidden)
        self.assertTrue(by_name[".cache"].hidden)
        self.assertFalse(by_name["visible.txt"].hidden)
        self.assertEqual(result.hidden_files, 2)
        self.assertEqual(result.hidden_bytes, 7000)
        self.assertEqual(result.total_size, 8000)                           # hidden items are still counted in sizes

    def test_system_flag_comes_from_the_platform(self):
        write(self.root / "pagefile.sys", 100)
        write(self.root / "normal.txt", 100)
        flags = lambda entry: FileFlags(hidden=True, system=True) if entry.name == "pagefile.sys" else FileFlags()  # noqa: E731
        by_name = {c.name: c for c in self.scan(flags_fn=flags).tree.children}
        self.assertTrue(by_name["pagefile.sys"].system and by_name["pagefile.sys"].hidden)
        self.assertFalse(by_name["normal.txt"].system)

    def test_cloud_only_placeholders_take_no_space_and_are_reported(self):
        write(self.root / "local.bin", 1000)
        write(self.root / "OneDrive" / "cloud_big.psd", 50_000)
        write(self.root / "OneDrive" / "cloud_small.txt", 2_000)
        write(self.root / "OneDrive" / "kept.docx", 300)
        flags = lambda entry: FileFlags(cloud_placeholder=entry.name.startswith("cloud_"))  # noqa: E731
        result = self.scan(flags_fn=flags)
        self.assertEqual(result.cloud_files, 2)
        self.assertEqual(result.cloud_bytes, 52_000)
        self.assertEqual(result.total_size, 1300)                           # only what is really on this disk
        self.assertEqual(result.file_count, 2)                              # local.bin and kept.docx
        names = {n.name for n in tree_utils.iter_nodes(result.tree)}
        self.assertNotIn("cloud_big.psd", names)
        self.assertNotIn("cloud_big.psd", [name for name, _p, _s in result.large_files])
        self.assertEqual(sum(size for size, _c in result.category_totals.values()), 1300)
        assert_children_add_up(self, result.tree)


class DeniedTests(ScannerTestCase):
    def setUp(self):
        super().setUp()
        write(self.root / "ok" / "a.bin", 1000)
        write(self.root / "locked" / "secret.bin", 9999)
        write(self.root / "top.txt", 10)

    def blocking(self, *blocked):
        real = os.scandir

        def scandir(path):
            if str(path) in blocked:
                raise PermissionError("denied")
            return real(path)

        return scandir

    def test_an_unreadable_folder_is_marked_unknown_and_listed(self):
        locked = str(self.root / "locked")
        result = self.scan(scandir_fn=self.blocking(locked))
        node = next(c for c in result.tree.children if c.name == "locked")
        self.assertTrue(node.denied)
        self.assertEqual(node.size, 0)
        self.assertEqual(result.denied_count, 1)
        self.assertEqual(result.denied_paths, [locked])
        self.assertEqual(result.total_size, 1010)                            # what could be read
        self.assertFalse(next(c for c in result.tree.children if c.name == "ok").denied)

    def test_a_denied_root_is_reported_not_an_error(self):
        result = self.scan(scandir_fn=self.blocking(str(self.root)))
        self.assertIsNone(result.error)
        self.assertTrue(result.tree.denied)
        self.assertEqual(result.denied_paths, [str(self.root)])
        self.assertEqual(result.total_size, 0)

    def test_an_unopenable_root_is_an_error(self):
        def broken(path):
            raise OSError("device not ready")

        result = self.scan(scandir_fn=broken)
        self.assertEqual(result.error, "The selected folder could not be opened.")

    def test_a_denied_item_inside_a_folder_is_listed_with_its_path(self):
        def flags(entry):
            if entry.name == "top.txt":
                raise PermissionError("denied")
            return FileFlags()

        result = self.scan(flags_fn=flags)
        self.assertIn(str(self.root / "top.txt"), result.denied_paths)
        self.assertEqual(result.denied_count, 1)

    def test_the_list_of_denied_paths_is_capped_but_the_count_is_not(self):
        for i in range(8):
            write(self.root / f"d{i}" / "f.bin", 10)
        blocked = [str(self.root / f"d{i}") for i in range(8)]
        with mock.patch.object(tree_scanner, "MAX_DENIED_PATHS", 3):
            result = self.scan(scandir_fn=self.blocking(*blocked))
        self.assertEqual(result.denied_count, 8)
        self.assertEqual(len(result.denied_paths), 3)
        self.assertEqual(result.denied_overflow, 5)

    def test_a_folder_that_fails_while_being_listed_is_skipped_not_fatal(self):
        real = os.scandir
        ok_dir = str(self.root / "ok")

        def flaky(path):
            if str(path) != ok_dir:
                return real(path)

            def generate():
                with real(path) as entries:
                    yield next(entries)
                raise OSError("disk error")

            return generate()

        write(self.root / "ok" / "b.bin", 500)
        result = self.scan(scandir_fn=flaky)
        self.assertIsNone(result.error)
        self.assertGreaterEqual(result.skipped_count, 1)
        assert_children_add_up(self, result.tree)


class LinkTests(ScannerTestCase):
    def test_symlinks_are_not_followed_but_are_listed(self):
        write(self.root / "real" / "data.bin", 1000)
        try:
            os.symlink(self.root / "real", self.root / "link_to_real", target_is_directory=True)
            os.symlink(self.root, self.root / "real" / "loop", target_is_directory=True)  # a cycle
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available here")
        result = self.scan()
        self.assertEqual(result.total_size, 1000)                            # counted once, not via the links
        self.assertEqual(result.linked_skipped, 2)
        kinds = {Path(path).name: kind for path, kind in result.linked_paths}
        self.assertEqual(kinds, {"link_to_real": "symlink", "loop": "symlink"})

    def test_junctions_and_other_reparse_points_are_listed_as_such(self):
        write(self.root / "real" / "data.bin", 1000)
        write(self.root / "junction" / "inside.bin", 7777)
        is_reparse = lambda entry: entry.name == "junction"  # noqa: E731
        result = self.scan(is_reparse_fn=is_reparse)
        self.assertEqual(result.total_size, 1000)
        self.assertEqual(result.linked_skipped, 1)
        self.assertEqual(result.linked_paths[0][1], "junction or link")

    def test_the_list_of_links_is_capped_but_the_count_is_not(self):
        for i in range(5):
            write(self.root / f"j{i}" / "f", 1)
        with mock.patch.object(tree_scanner, "MAX_LINK_PATHS", 2):
            result = self.scan(is_reparse_fn=lambda entry: True)
        self.assertEqual(result.linked_skipped, 5)
        self.assertEqual(len(result.linked_paths), 2)


class ProgressAndCancelTests(ScannerTestCase):
    def setUp(self):
        super().setUp()
        for i in range(30):
            write(self.root / f"dir{i % 5}" / f"f{i}.bin", 1000 + i)

    def test_status_reports_files_bytes_and_the_current_path(self):
        seen: list[ScanStatus] = []
        result = self.scan(status=seen.append)
        self.assertTrue(seen)
        last = seen[-1]
        self.assertEqual((last.files, last.dirs, last.bytes), (30, 5, result.total_size))
        self.assertTrue(last.path.startswith(str(self.root)))

    def test_progress_and_status_work_together_and_never_exceed_the_final_totals(self):
        counts = []
        statuses = []
        with mock.patch.object(tree_scanner.time, "monotonic", side_effect=iter(range(0, 10_000, 1))):
            result = self.scan(progress=lambda f, d: counts.append((f, d)), status=statuses.append)
        self.assertEqual(counts[-1], (result.file_count, result.dir_count))
        self.assertTrue(all(s.files <= result.file_count and s.bytes <= result.total_size for s in statuses))

    def test_cancelling_mid_scan_leaves_a_consistent_partial_tree(self):
        cancel = threading.Event()
        calls = []

        def flags(entry):
            calls.append(entry.name)
            if len(calls) == 12:
                cancel.set()
            return FileFlags()

        result = self.scan(cancel_event=cancel, flags_fn=flags)
        self.assertTrue(result.cancelled)
        self.assertLess(result.file_count, 30)
        self.assertEqual(result.tree.size, result.total_size)
        assert_children_add_up(self, result.tree)

    def test_scanning_never_modifies_the_folder(self):
        def snapshot():
            return sorted((p.relative_to(self.root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                          for p in self.root.rglob("*"))

        before = snapshot()
        self.scan()
        self.assertEqual(before, snapshot())


# --------------------------------------------------------------------------- #
class TreeUtilsTests(ScannerTestCase):
    def setUp(self):
        super().setUp()
        write(self.root / "Videos" / "Raw" / "clip_one.mov", 4000)
        write(self.root / "Videos" / "Raw" / "clip_two.mov", 3000)
        write(self.root / "Videos" / "notes.txt", 10)
        write(self.root / "Docs" / "Report.pdf", 500)
        write(self.root / "Docs" / "deep" / "er" / "est" / "x.txt", 5)
        self.tree = self.scan().tree

    def test_find_chain_returns_every_ancestor(self):
        chain = tree_utils.find_chain(self.tree, str(self.root / "Videos" / "Raw" / "clip_one.mov"))
        self.assertEqual([n.name for n in chain][1:], ["Videos", "Raw", "clip_one.mov"])
        self.assertIs(chain[0], self.tree)

    def test_find_chain_for_the_root_unknown_and_empty_paths(self):
        self.assertEqual(tree_utils.find_chain(self.tree, str(self.root)), [self.tree])
        self.assertEqual(tree_utils.find_chain(self.tree, str(self.root / "nope" / "x")), [])
        self.assertEqual(tree_utils.find_chain(self.tree, str(Path("/somewhere/else"))), [])
        self.assertEqual(tree_utils.find_chain(self.tree, ""), [])

    def test_find_chain_does_not_confuse_prefix_siblings(self):
        write(self.root / "Videos2" / "a.bin", 1)
        tree = self.scan().tree
        chain = tree_utils.find_chain(tree, str(self.root / "Videos2" / "a.bin"))
        self.assertEqual([n.name for n in chain][1:], ["Videos2", "a.bin"])

    def test_search_is_case_insensitive_sorted_by_size_and_skips_synthetic_items(self):
        found, more = tree_utils.search(self.tree, "CLIP")
        self.assertEqual([n.name for n in found], ["clip_one.mov", "clip_two.mov"])
        self.assertFalse(more)
        self.assertEqual(tree_utils.search(self.tree, "   ")[0], [])
        self.assertEqual(tree_utils.search(self.tree, "videos")[0][0].name, "Videos")   # folders match too

    def test_search_limit_and_category_filter(self):
        found, more = tree_utils.search(self.tree, ".", limit=2)
        self.assertEqual(len(found), 2)
        self.assertTrue(more)
        docs, _ = tree_utils.search(self.tree, "", category="Documents")
        self.assertEqual(docs, [])
        only_videos, _ = tree_utils.search(self.tree, "clip", category="Videos")
        self.assertEqual(len(only_videos), 2)
        none, _ = tree_utils.search(self.tree, "clip", category="Audio")
        self.assertEqual(none, [])

    def test_top_dirs_respects_depth_and_limit(self):
        names = [Path(p).name for p, _s, _d in tree_utils.top_dirs(self.tree, max_depth=2)]
        self.assertIn("Raw", names)
        self.assertNotIn("est", names)                                       # depth 4
        limited = tree_utils.top_dirs(self.tree, max_depth=3, limit=2)
        self.assertEqual(len(limited), 2)
        self.assertEqual([d for _p, _s, d in tree_utils.top_dirs(self.tree, max_depth=1)], [1, 1])
        sizes = [s for _p, s, _d in tree_utils.top_dirs(self.tree)]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


if __name__ == "__main__":
    unittest.main()
