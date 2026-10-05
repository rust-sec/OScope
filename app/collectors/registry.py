"""Chooses which collectors run on this machine.

Platform dispatch lives here and only here. OScope is Windows-only for now; a
future Linux build would add its collectors in one more branch of this function
without touching the Sampler, analysis or GUI code.
"""

from __future__ import annotations

from app.collectors.base import Collector


def build_default_collectors() -> list[Collector]:
    """Collectors for the current platform.

    Empty until the Windows collectors land (Phase 2); the Sampler already
    accepts whatever this returns.
    """
    return []
