"""Temperature and fan evidence from WMI, reported with strict honesty.

What Windows actually offers without a vendor driver:

* ``MSAcpi_ThermalZoneTemperature`` (namespace ``root\\wmi``): temperatures of ACPI thermal
  zones. Whether any exist is up to the PC maker's firmware, it often needs administrator
  rights, and a zone is NOT necessarily the CPU. The reading is therefore called
  ``temp.acpi_max`` and always says so.
* ``Win32_Fan``: lists fan devices but has no measured-speed property, so a fan speed in RPM is
  never available this way. ``fan.rpm`` is reported as NOT_EXPOSED and says why.

Nothing here loads a driver or asks for elevation. Both are read in ONE PowerShell call
(slow to start), on a background probe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.collectors.base import Availability, CollectorContext, Reading
from app.collectors.windows.background import LatestSource, ProbeError
from app.collectors.windows.cmd import RC_LAUNCH_FAILED, RC_TIMEOUT, Runner, run_command

POWERSHELL_TIMEOUT = 15.0
MIN_PLAUSIBLE_C = 0.0
MAX_PLAUSIBLE_C = 120.0

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature |
    ForEach-Object { 'TZ|' + $_.InstanceName + '|' + $_.CurrentTemperature }
} catch { 'TZERR|' + $_.FullyQualifiedErrorId + '|' + $_.Exception.Message }
try {
  Get-CimInstance -ClassName Win32_Fan |
    ForEach-Object { 'FAN|' + $_.Name + '|' + $_.DesiredSpeed }
} catch { 'FANERR|' + $_.FullyQualifiedErrorId + '|' + $_.Exception.Message }
"""

_HRESULT = re.compile(r"0x[0-9A-Fa-f]{8}")
# WMI error codes (wbemcli.h). Access denied = needs rights; the others mean "this device has no such thing".
_ACCESS_DENIED = {"0x80041003"}
_NOT_PRESENT = {"0x80041010", "0x8004100c", "0x8004100e"}  # invalid class / not supported / invalid namespace


@dataclass(frozen=True)
class WmiThermalResult:
    zones: tuple[tuple[str, float], ...] = ()      # (zone name, degrees C) kept after the plausibility check
    zones_dropped: int = 0                          # zones with an implausible value
    zone_error: Optional[tuple[str, str]] = None    # (hresult, message) if the query failed
    fans: tuple[str, ...] = ()                      # names of listed fan devices
    fan_error: Optional[tuple[str, str]] = None


def kelvin_tenths_to_celsius(raw: float) -> float:
    """WMI reports ACPI zone temperatures in tenths of a kelvin."""
    return raw / 10.0 - 273.15


def parse_wmi_output(text: str) -> WmiThermalResult:
    """Parse the ``TZ|name|raw`` / ``FAN|name|speed`` / ``*ERR|id|message`` lines from the script."""
    zones: list[tuple[str, float]] = []
    dropped = 0
    fans: list[str] = []
    zone_error = fan_error = None
    for line in text.splitlines():
        parts = line.strip().split("|", 2)
        tag = parts[0]
        if tag == "TZ" and len(parts) == 3:
            try:
                celsius = kelvin_tenths_to_celsius(float(parts[2]))
            except ValueError:
                dropped += 1
                continue
            if MIN_PLAUSIBLE_C <= celsius <= MAX_PLAUSIBLE_C:
                zones.append((parts[1], celsius))
            else:
                dropped += 1
        elif tag == "FAN" and len(parts) >= 2:
            fans.append(parts[1] or "fan")
        elif tag in ("TZERR", "FANERR") and len(parts) == 3:
            match = _HRESULT.search(parts[1]) or _HRESULT.search(parts[2])
            error = ((match.group(0).lower() if match else ""), parts[2].strip()[:120])
            if tag == "TZERR":
                zone_error = error
            else:
                fan_error = error
    return WmiThermalResult(tuple(zones), dropped, zone_error, tuple(fans), fan_error)


def make_wmi_reader(runner: Runner = run_command):
    """Function (for a BackgroundProbe) that runs the PowerShell query once."""

    def read() -> WmiThermalResult:
        code, output = runner(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT], POWERSHELL_TIMEOUT
        )
        if code == RC_LAUNCH_FAILED:
            raise ProbeError("PowerShell could not be started")
        if code == RC_TIMEOUT:
            raise ProbeError("PowerShell timed out")
        return parse_wmi_output(output)

    return read


def _error_status(error: tuple[str, str]) -> tuple[Availability, str]:
    hresult, message = error
    if hresult in _ACCESS_DENIED:
        return Availability.PERMISSION_RESTRICTED, "Windows requires administrator rights to read this"
    if hresult in _NOT_PRESENT:
        return Availability.NOT_EXPOSED, "this device does not provide it"
    return Availability.UNAVAILABLE, f"query failed ({hresult or 'unknown error'}): {message}"


class ThermalCollector:
    """``temp.acpi_max``: hottest ACPI thermal zone in degrees C (not necessarily the CPU)."""

    key = "thermal"
    _SOURCE = "WMI:MSAcpi_ThermalZoneTemperature"

    def __init__(self, source: LatestSource[WmiThermalResult]) -> None:
        self._source = source

    def collect(self, ctx: CollectorContext) -> dict[str, Reading]:
        name = "temp.acpi_max"
        outcome = self._source.latest()
        if outcome is None:
            return {name: Reading.missing(name, Availability.UNAVAILABLE, "first measurement still running", self._SOURCE)}
        if outcome.value is None:
            return {name: Reading.missing(name, Availability.UNAVAILABLE, outcome.error or "unavailable", self._SOURCE)}
        result = outcome.value
        if result.zone_error is not None:
            status, detail = _error_status(result.zone_error)
            return {name: Reading.missing(name, status, detail, self._SOURCE)}
        if not result.zones:
            detail = (
                f"{result.zones_dropped} ACPI zone(s) gave implausible values and were ignored"
                if result.zones_dropped
                else "this PC reports no ACPI thermal zones"
            )
            status = Availability.UNAVAILABLE if result.zones_dropped else Availability.NOT_EXPOSED
            return {name: Reading.missing(name, status, detail, self._SOURCE)}
        hottest = max(celsius for _, celsius in result.zones)
        return {
            name: Reading.available(
                name,
                hottest,
                "°C",
                self._SOURCE,
                detail=f"hottest of {len(result.zones)} ACPI thermal zone(s); a zone is not necessarily the CPU",
            )
        }


class FanCollector:
    """``fan.rpm``: always NOT_EXPOSED or UNAVAILABLE, because Windows has no measured-RPM property here."""

    key = "fan"
    _SOURCE = "WMI:Win32_Fan"

    def __init__(self, source: LatestSource[WmiThermalResult]) -> None:
        self._source = source

    def collect(self, ctx: CollectorContext) -> dict[str, Reading]:
        name = "fan.rpm"
        outcome = self._source.latest()
        if outcome is None:
            return {name: Reading.missing(name, Availability.UNAVAILABLE, "first measurement still running", self._SOURCE)}
        if outcome.value is None:
            return {name: Reading.missing(name, Availability.UNAVAILABLE, outcome.error or "unavailable", self._SOURCE)}
        result = outcome.value
        if result.fan_error is not None:
            status, detail = _error_status(result.fan_error)
            return {name: Reading.missing(name, status, detail, self._SOURCE)}
        detail = (
            f"{len(result.fans)} fan device(s) listed, but Windows reports no measured fan speed"
            if result.fans
            else "no fan devices are reported to Windows; fan speed is not exposed by this device"
        )
        return {name: Reading.missing(name, Availability.NOT_EXPOSED, detail, self._SOURCE)}
