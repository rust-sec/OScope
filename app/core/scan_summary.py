"""Plain-language notes about what a folder scan could and could not see (no GUI code, so it is testable)."""

from __future__ import annotations

from app.core.tree_scanner import TreeScanResult
from app.utils.formatting import format_bytes

METHOD_NOTES = (
    "Sizes are logical file sizes. Space used on disk can differ because of cluster rounding, compression or sparse files.",
    "Files with several names (hard links) are counted once per name.",
    "Links (symlinks and junctions) are not followed, so nothing is counted twice or looped on.",
    "\"Everything\" means everything this program is allowed to read; OScope does not bypass Windows security.",
)


def scan_notes(result: TreeScanResult) -> list[tuple[str, str]]:
    """``[(kind, text)]`` with kind ``"warn"`` (affects what the totals mean) or ``"info"``."""
    notes: list[tuple[str, str]] = []
    if result.denied_count:
        notes.append(("warn", f"{result.denied_count:,} item{_s(result.denied_count)} could not be read (access denied), "
                              "so their size is not included."))
    if result.linked_skipped:
        notes.append(("warn", f"{result.linked_skipped:,} link{_s(result.linked_skipped)} (symlinks or junctions) "
                              "were not followed."))
    if result.cloud_files:
        notes.append(("warn", f"{result.cloud_files:,} cloud-only file{_s(result.cloud_files)} "
                              f"({format_bytes(result.cloud_bytes)}) are stored online, not on this PC, and are not counted."))
    if result.folded_files:
        notes.append(("info", f"{result.folded_files:,} small file{_s(result.folded_files)} are grouped into "
                              "\"(N smaller files)\" items; their sizes are included."))
    if result.hidden_files:
        notes.append(("info", f"Includes {result.hidden_files:,} hidden file{_s(result.hidden_files)} "
                              f"({format_bytes(result.hidden_bytes)})."))
    return notes


def scan_details_text(result: TreeScanResult) -> str:
    """The "Details" view of a scan: totals, every note, and the paths that could not be read or followed."""
    lines = [
        f"Scan of {result.root}",
        f"{format_bytes(result.total_size)} in {result.file_count:,} files and {result.dir_count:,} folders",
    ]
    if result.cancelled:
        lines.append("The scan was stopped early, so these totals are partial.")
    if result.elevated is not None:
        lines.append("Scanned as administrator." if result.elevated else "Scanned as a standard user.")
    lines += ["", "WHAT THIS MEANS"]
    notes = scan_notes(result)
    lines += [f"  - {text}" for _kind, text in notes] or ["  - Everything in this folder could be read."]
    lines += [f"  - {note}" for note in METHOD_NOTES]

    if result.denied_paths:
        lines += ["", f"COULD NOT BE READ ({result.denied_count:,})"]
        lines += [f"  {path}" for path in result.denied_paths]
        if result.denied_overflow:
            lines.append(f"  ... and {result.denied_overflow:,} more")
    if result.linked_paths:
        lines += ["", f"LINKS NOT FOLLOWED ({result.linked_skipped:,})"]
        lines += [f"  {path}  ({kind})" for path, kind in result.linked_paths]
        if result.linked_skipped > len(result.linked_paths):
            lines.append(f"  ... and {result.linked_skipped - len(result.linked_paths):,} more")
    return "\n".join(lines) + "\n"


def _s(count: int) -> str:
    return "" if count == 1 else "s"
