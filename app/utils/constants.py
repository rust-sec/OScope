"""Central place for every constant in OScope: names, colours, thresholds, defaults.

Diagnostic thresholds live here (and only here) so they can be tuned in one edit.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
APP_NAME = "OScope"
APP_FULL_TITLE = "OScope: A System Health and Resource Analyzer for Windows"
APP_SUBTITLE = "System Health & Resource Analyzer"
APP_TAGLINE = (
    "A lightweight utility for process, CPU, memory, and storage analysis "
    "using Windows operating-system interfaces."
)
UNAVAILABLE = "Unavailable on this platform"

# --------------------------------------------------------------------------- #
# Diagnostic thresholds (percent)
# --------------------------------------------------------------------------- #
CPU_HIGH_PERCENT = 80
CPU_CRITICAL_PERCENT = 95

MEMORY_HIGH_PERCENT = 85
MEMORY_CRITICAL_PERCENT = 95

STORAGE_FULL_PERCENT = 80      # "Storage getting full"  (info)
STORAGE_LOW_PERCENT = 90       # "Low storage"           (warning)
STORAGE_CRITICAL_PERCENT = 95  # "Very low storage"      (critical)

# --------------------------------------------------------------------------- #
# Defaults / limits
# --------------------------------------------------------------------------- #
REFRESH_INTERVAL_SECONDS = 2
REFRESH_INTERVAL_CHOICES = (1, 2, 3, 5, 10)
MIN_SAMPLE_GAP_SECONDS = 1.0        # CPU % is meaningless if sampled faster than this

LARGE_FILE_THRESHOLD_MB = 500
MAX_LARGE_FILES_KEPT = 200          # scanner keeps only the N biggest files in memory
LARGEST_DIRS_SHOWN = 15
TOP_PROCESSES_SHOWN = 5

MAX_TREE_NODES = 300_000            # soft cap on retained treemap nodes; aggregates stay correct past this
TREEMAP_MAX_RECTS_PER_LEVEL = 500   # per-zoom-level render cap; remainder lumped into one "+N more" rect

# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
ACCESS_DENIED_TITLE = "Access denied"
ACCESS_DENIED_MESSAGE = (
    "Some files or directories could not be analyzed because the operating "
    "system denied access."
)

# --------------------------------------------------------------------------- #
# Visual design
# --------------------------------------------------------------------------- #
COLORS = {
    "bg": "#0F1115",
    "bg_alt": "#171A21",
    "card": "#1D212A",
    "border": "#2A2F3B",
    "hover": "#262B36",
    "track": "#2A2F3B",
    "select": "#2B3C6E",
    "text": "#F5F7FA",
    "text_dim": "#9AA3B2",
    "accent": "#5B8CFF",
    "accent_hover": "#7AA2FF",
    "success": "#39D98A",
    "warning": "#F5B942",
    "danger": "#FF5C5C",
}

LEVEL_COLORS = {
    "normal": COLORS["success"],
    "info": COLORS["accent"],
    "warning": COLORS["warning"],
    "critical": COLORS["danger"],
}

# Storage Analyzer treemap: one vivid, clearly distinct colour per file-type
# category, spread across the hue wheel so the map reads as colourful even
# on folder-heavy scans (folders get their own bright colour, not a muted
# tone that blends into the dark background). Keys must match
# file_categories.CATEGORY_NAMES plus file_categories.FOLDER_CATEGORY.
CATEGORY_COLORS = {
    "Folder": "#FFD166",       # warm gold — reads as "folder" at a glance
    "Images": "#4FC3F7",       # sky blue
    "Videos": "#BA68C8",       # magenta / purple
    "Audio": "#66BB6A",        # green
    "Documents": "#7986CB",    # indigo
    "Archives": "#FF8A65",     # orange
    "Executables": "#EF5350",  # red
    "Code": "#26C6DA",         # cyan
    "Other": "#B0BEC5",        # light grey-blue, the one deliberately muted tone
}

FONT_FAMILY = "Segoe UI"
