"""Diagnostic log for OScope itself (not the system report): why a reading was unavailable, etc."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from app.core import platform_ops

LOGGER_NAME = "oscope"
LOG_FILENAME = "oscope.log"
_MAX_BYTES = 512_000
_BACKUPS = 2


def setup_logging(directory: Optional[Path] = None, level: int = logging.INFO) -> logging.Logger:
    """Log to a small rotating file in the app-data folder. Safe to call more than once.

    If no writable folder exists, logging stays silent (a NullHandler) rather than
    printing to the console or failing.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    logger.propagate = False
    target = directory if directory is not None else platform_ops.app_data_dir()
    handler: logging.Handler
    try:
        if target is None:
            raise OSError("no data folder")
        handler = RotatingFileHandler(
            target / LOG_FILENAME, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    except OSError:
        handler = logging.NullHandler()
    logger.addHandler(handler)
    return logger
