"""User settings that survive restarts, kept as one small JSON file in the app-data folder."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from app.analysis.workloads import DEFAULT_WORKLOAD, WORKLOADS
from app.core import platform_ops
from app.utils.constants import LARGE_FILE_THRESHOLD_MB, REFRESH_INTERVAL_CHOICES, REFRESH_INTERVAL_SECONDS

_LOG = logging.getLogger("oscope.settings")
SETTINGS_FILENAME = "settings.json"


@dataclass
class AppSettings:
    """Run-time settings (edited in the Settings dialog)."""

    refresh_interval: int = REFRESH_INTERVAL_SECONDS
    large_file_mb: int = LARGE_FILE_THRESHOLD_MB
    workload: str = DEFAULT_WORKLOAD  # what the user is mostly doing; only changes the order of explanations

    @classmethod
    def from_dict(cls, data: object) -> "AppSettings":
        """Build settings from stored JSON; any missing or invalid field falls back to its default."""
        settings = cls()
        if not isinstance(data, dict):
            return settings
        interval = data.get("refresh_interval")
        if isinstance(interval, int) and not isinstance(interval, bool) and interval in REFRESH_INTERVAL_CHOICES:
            settings.refresh_interval = interval
        threshold = data.get("large_file_mb")
        if isinstance(threshold, int) and not isinstance(threshold, bool) and threshold >= 1:
            settings.large_file_mb = threshold
        workload = data.get("workload")
        if isinstance(workload, str) and workload in WORKLOADS:
            settings.workload = workload
        return settings

    def to_dict(self) -> dict:
        return asdict(self)


class SettingsStore:
    """Loads and saves ``AppSettings``. Never raises: a broken file just means defaults."""

    def __init__(self, directory: Optional[Path]) -> None:
        self._path = directory / SETTINGS_FILENAME if directory is not None else None

    @classmethod
    def default(cls) -> "SettingsStore":
        """Store in the standard app-data folder (persistence is off if there is none)."""
        return cls(platform_ops.app_data_dir())

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def load(self) -> AppSettings:
        if self._path is None or not self._path.is_file():
            return AppSettings()
        try:
            return AppSettings.from_dict(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _LOG.warning("Could not read %s; using default settings", self._path)
            return AppSettings()

    def save(self, settings: AppSettings) -> bool:
        """Write atomically (temp file + rename) so a crash cannot leave a half-written file."""
        if self._path is None:
            return False
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._path.parent, prefix=".settings-", suffix=".tmp", delete=False
            ) as handle:
                temp_name = handle.name
                json.dump(settings.to_dict(), handle, indent=2)
            os.replace(temp_name, self._path)
            return True
        except OSError:
            _LOG.warning("Could not save settings to %s", self._path)
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
            return False
