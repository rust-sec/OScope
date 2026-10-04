"""Tests for the pure squarified-treemap layout algorithm (no Tkinter needed).

Run from the project folder:   python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.gui.treemap_layout import Rect, layout_children, squarify  # noqa: E402


def _rects_overlap(a: Rect, b: Rect) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


@dataclass
class _Item:
    name: str
    size: int


class SquarifyTests(unittest.TestCase):
    def test_empty_input_returns_empty_list(self):
        self.assertEqual(squarify([], 0, 0, 100, 100), [])

    def test_single_item_fills_rect(self):
        rects = squarify([50.0], 0, 0, 10, 5)
        self.assertEqual(len(rects), 1)
        r = rects[0]
        self.assertAlmostEqual(r.x, 0)
        self.assertAlmostEqual(r.y, 0)
        self.assertAlmostEqual(r.w * r.h, 50.0)

    def test_areas_conserved(self):
        sizes = [40.0, 30.0, 20.0, 10.0]
        w, h = 20.0, 5.0  # area 100, matches sum(sizes)
        rects = squarify(sizes, 0, 0, w, h)
        self.assertEqual(len(rects), len(sizes))
        for size, rect in zip(sizes, rects):
            self.assertAlmostEqual(rect.w * rect.h, size, places=6)

    def test_no_overlaps(self):
        sizes = [50.0, 25.0, 12.5, 6.25, 6.25]
        rects = squarify(sizes, 0, 0, 10, 10)
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                self.assertFalse(_rects_overlap(rects[i], rects[j]), f"rects {i} and {j} overlap")

    def test_all_items_covered_exactly_once(self):
        sizes = [30.0, 30.0, 20.0, 20.0]
        rects = squarify(sizes, 0, 0, 10, 10)
        self.assertAlmostEqual(sum(r.w * r.h for r in rects), sum(sizes), places=6)

    def test_aspect_ratio_is_reasonable(self):
        # A classic squarify fixture: roughly-equal-area items in a square
        # should never degenerate into razor-thin slivers.
        sizes = [25.0, 25.0, 25.0, 25.0]
        rects = squarify(sizes, 0, 0, 10, 10)
        for r in rects:
            ratio = max(r.w / r.h, r.h / r.w)
            self.assertLess(ratio, 3.0)


class LayoutChildrenTests(unittest.TestCase):
    def test_sorts_and_zips_back_to_original_objects(self):
        items = [_Item("small", 10), _Item("big", 100), _Item("medium", 40)]
        pairs = layout_children(items, 0, 0, 20, 10)
        names_in_order = [item.name for _, item in pairs]
        self.assertEqual(names_in_order, ["big", "medium", "small"])

    def test_empty_children_returns_empty_list(self):
        self.assertEqual(layout_children([], 0, 0, 100, 100), [])

    def test_zero_size_item_still_gets_a_sliver(self):
        items = [_Item("a", 100), _Item("zero", 0)]
        pairs = layout_children(items, 0, 0, 20, 10)
        rects = [rect for rect, _ in pairs]
        self.assertEqual(len(rects), 2)
        for r in rects:
            self.assertGreater(r.w, 0)
            self.assertGreater(r.h, 0)

    def test_areas_proportional_to_size(self):
        items = [_Item("a", 300), _Item("b", 100)]
        pairs = layout_children(items, 0, 0, 20, 20)
        areas = {item.name: rect.w * rect.h for rect, item in pairs}
        self.assertAlmostEqual(areas["a"] / areas["b"], 3.0, places=3)


if __name__ == "__main__":
    unittest.main()
