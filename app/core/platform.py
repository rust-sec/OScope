"""Platform detection.

OScope supports Windows only. This module is the single place that decides
whether the current operating system is supported.

Developer switch: setting the environment variable ``OSCOPE_DEV=1`` lets the
application start on a non-Windows machine so the OS-independent logic can be
tested. Windows-only metrics then report "Unavailable on this platform".
"""

from __future__ import annotations

import os
import platform as _stdlib_platform  # the standard-library module, not this file
import sys

WINDOWS = "Windows"
DEV_ENV_VAR = "OSCOPE_DEV"

UNSUPPORTED_MESSAGE = (
    "Unsupported operating system.\n"
    "OScope currently supports Windows only."
)


def detect_platform() -> str:
    """Return ``"Windows"`` or the name of whatever else we are running on."""
    if sys.platform.startswith("win"):
        return WINDOWS
    return _stdlib_platform.system() or "Unknown"


def is_windows() -> bool:
    return detect_platform() == WINDOWS


def is_dev_mode() -> bool:
    """True when the developer override is on AND we are not really on Windows."""
    return not is_windows() and os.environ.get(DEV_ENV_VAR) == "1"


def is_supported() -> bool:
    return is_windows() or is_dev_mode()


def platform_label() -> str:
    """Text for the header badge."""
    if is_windows():
        return WINDOWS
    return f"{detect_platform()} (dev mode)"
