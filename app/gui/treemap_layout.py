"""Squarified treemap layout (Bruls, Huizing, van Wijk).

Pure and GUI-independent: no Tkinter import, no dependency on TreeNode.
Given a list of sizes and a bounding rectangle, ``squarify`` lays them out
into non-overlapping rectangles whose areas are proportional to the sizes,
minimizing how "thin" any rectangle gets. ``layout_children`` is the
convenience wrapper the treemap canvas actually calls: it sorts arbitrary
objects by size and zips the resulting rectangles back to them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float


def _worst(row: list[float], length: float) -> float:
    """Worst (largest) aspect ratio if ``row`` were laid out along a side of ``length``.

    Standard squarified-treemap formula (Bruls/Huizing/van Wijk):
    worst(R, w) = max((w^2 * max(R)) / s^2, s^2 / (w^2 * min(R))), where s = sum(R).
    """
    if not row or length <= 0:
        return float("inf")
    total = sum(row)
    if total <= 0:
        return float("inf")
    hi, lo = max(row), min(row)
    if lo <= 0:
        return float("inf")
    length_sq = length * length
    total_sq = total * total
    return max((length_sq * hi) / total_sq, total_sq / (length_sq * lo))


def _layout_row(row: list[float], x: float, y: float, w: float, h: float) -> tuple[list[Rect], float, float, float, float]:
    """Place ``row`` along the shorter side of the (x, y, w, h) rect; return the leftover rect."""
    total = sum(row)
    if w >= h:
        # Row runs vertically down the left edge, thickness = total / h
        thickness = total / h if h > 0 else 0.0
        rects = []
        cursor = y
        for size in row:
            item_h = size / thickness if thickness > 0 else 0.0
            rects.append(Rect(x, cursor, thickness, item_h))
            cursor += item_h
        return rects, x + thickness, y, w - thickness, h
    else:
        thickness = total / w if w > 0 else 0.0
        rects = []
        cursor = x
        for size in row:
            item_w = size / thickness if thickness > 0 else 0.0
            rects.append(Rect(cursor, y, item_w, thickness))
            cursor += item_w
        return rects, x, y + thickness, w, h - thickness


def squarify(sizes: Sequence[float], x: float, y: float, w: float, h: float) -> list[Rect]:
    """Lay out ``sizes`` (sorted descending, all > 0) into ``(x, y, w, h)``.

    Returns one Rect per input size, same order as ``sizes``. Areas are
    exactly proportional to sizes (sizes are pre-scaled by the caller via
    ``layout_children`` so ``sum(sizes) == w * h``).
    """
    remaining = list(sizes)
    if not remaining or w <= 0 or h <= 0:
        return []

    results: list[Rect] = []
    rx, ry, rw, rh = x, y, w, h
    row: list[float] = []

    while remaining:
        side = min(rw, rh)
        item = remaining[0]
        candidate = row + [item]
        if not row or _worst(candidate, side) <= _worst(row, side):
            row = candidate
            remaining.pop(0)
        else:
            rects, rx, ry, rw, rh = _layout_row(row, rx, ry, rw, rh)
            results.extend(rects)
            row = []

    if row:
        rects, rx, ry, rw, rh = _layout_row(row, rx, ry, rw, rh)
        results.extend(rects)

    return results


def layout_children(
    children: Sequence[Any],
    x: float,
    y: float,
    w: float,
    h: float,
    size_of: Callable[[Any], float] = lambda n: n.size,
) -> list[tuple[Rect, Any]]:
    """Sort ``children`` by size descending, scale to fill (w, h), and lay out.

    Each size is floored at 1 so a zero-byte file still gets a sliver
    rectangle (hoverable/clickable), rather than vanishing entirely.
    """
    if not children or w <= 0 or h <= 0:
        return []

    ordered = sorted(children, key=size_of, reverse=True)
    raw_sizes = [max(float(size_of(item)), 1.0) for item in ordered]
    total = sum(raw_sizes)
    area = w * h
    scaled = [size / total * area for size in raw_sizes]

    rects = squarify(scaled, x, y, w, h)
    return list(zip(rects, ordered))
