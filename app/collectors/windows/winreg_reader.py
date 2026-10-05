"""Read-only access to a few registry keys, through an injectable ``winreg`` module."""

from __future__ import annotations

import logging
from typing import Any, Optional

_LOG = logging.getLogger("oscope.registry")


class RegistryReader:
    """Lists the values of registry keys. Never writes. ``winreg_module`` is injected in tests."""

    def __init__(self, winreg_module: Optional[Any] = None) -> None:
        if winreg_module is None:
            try:
                import winreg as winreg_module  # type: ignore[no-redef]  # Windows only
            except ImportError:
                winreg_module = None
        self._winreg = winreg_module

    @property
    def available(self) -> bool:
        return self._winreg is not None

    def values(self, hive: str, subkey: str) -> Optional[dict[str, Any]]:
        """``{value name: data}`` for ``hive`` ("HKCU" / "HKLM") + ``subkey``.

        A key that does not exist is simply empty (``{}``). ``None`` means "could not be read"
        (no registry, access denied, ...), which callers must not mistake for "empty".
        """
        if self._winreg is None:
            return None
        root = {"HKCU": self._winreg.HKEY_CURRENT_USER, "HKLM": self._winreg.HKEY_LOCAL_MACHINE}[hive]
        try:
            key = self._winreg.OpenKey(root, subkey, 0, self._winreg.KEY_READ)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            _LOG.debug("cannot open %s\\%s: %s", hive, subkey, exc)
            return None
        result: dict[str, Any] = {}
        try:
            index = 0
            while True:
                try:
                    name, data, _kind = self._winreg.EnumValue(key, index)
                except OSError:  # no more values
                    break
                result[name] = data
                index += 1
        finally:
            self._winreg.CloseKey(key)
        return result
