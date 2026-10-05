"""Power source, battery and Windows power mode.

AC / battery / battery saver come from ``GetSystemPowerStatus`` and are reliable.
The Windows "power mode" slider is an *overlay scheme* GUID; only GUIDs we recognise are
named. An unrecognised GUID is reported as unavailable, never guessed.
"""

from __future__ import annotations

from typing import Callable, Optional

from app.collectors.base import Availability, CollectorContext, Reading

AC_OFFLINE, AC_ONLINE = 0, 1
BATTERY_FLAG_CHARGING = 8
BATTERY_FLAG_NO_BATTERY = 128
UNKNOWN_BYTE = 255

# Windows 10 1709+ / Windows 11 "power mode" overlay GUIDs.
# From memory of Microsoft's documentation, not yet checked on a real machine: WIN-VERIFY.
OVERLAY_NAMES = {
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "Best power efficiency",
    "00000000-0000-0000-0000-000000000000": "Balanced",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "Better performance",
    "ded574b5-45a0-4f42-8737-46345c09c238": "Best performance",
}

_STATUS_SOURCE = "GetSystemPowerStatus"
_MODE_SOURCE = "PowerGetEffectiveOverlayScheme"


def decode_power_status(raw: dict[str, int]) -> dict[str, Reading]:
    """Turn the raw status fields into readings (pure, so it is testable without Windows)."""
    readings: dict[str, Reading] = {}
    ac_line = raw.get("ac_line", UNKNOWN_BYTE)
    flag = raw.get("battery_flag", UNKNOWN_BYTE)
    percent = raw.get("battery_percent", UNKNOWN_BYTE)

    if ac_line == AC_ONLINE:
        readings["power.on_battery"] = Reading.available("power.on_battery", False, source=_STATUS_SOURCE)
    elif ac_line == AC_OFFLINE:
        readings["power.on_battery"] = Reading.available("power.on_battery", True, source=_STATUS_SOURCE)
    else:
        readings["power.on_battery"] = Reading.missing(
            "power.on_battery", Availability.UNAVAILABLE, "Windows does not know the power source", _STATUS_SOURCE
        )

    if flag == BATTERY_FLAG_NO_BATTERY:
        no_battery = "this PC has no battery"
        readings["power.battery_percent"] = Reading.missing(
            "power.battery_percent", Availability.NOT_EXPOSED, no_battery, _STATUS_SOURCE
        )
        readings["power.charging"] = Reading.missing(
            "power.charging", Availability.NOT_EXPOSED, no_battery, _STATUS_SOURCE
        )
    else:
        if percent <= 100:
            readings["power.battery_percent"] = Reading.available(
                "power.battery_percent", percent, "%", _STATUS_SOURCE
            )
        else:
            readings["power.battery_percent"] = Reading.missing(
                "power.battery_percent", Availability.UNAVAILABLE, "battery level unknown", _STATUS_SOURCE
            )
        if flag == UNKNOWN_BYTE:
            readings["power.charging"] = Reading.missing(
                "power.charging", Availability.UNAVAILABLE, "charging state unknown", _STATUS_SOURCE
            )
        else:
            readings["power.charging"] = Reading.available(
                "power.charging", bool(flag & BATTERY_FLAG_CHARGING), source=_STATUS_SOURCE
            )

    saver = raw.get("saver", 0)
    readings["power.battery_saver"] = Reading.available("power.battery_saver", saver == 1, source=_STATUS_SOURCE)
    return readings


def decode_power_mode(guid: Optional[str]) -> Reading:
    name = "power.mode"
    if guid is None:
        return Reading.missing(name, Availability.UNAVAILABLE, "Windows power mode could not be read", _MODE_SOURCE)
    label = OVERLAY_NAMES.get(guid.lower())
    if label is None:
        return Reading.missing(name, Availability.UNAVAILABLE, f"unrecognised power mode ({guid})", _MODE_SOURCE)
    return Reading.available(name, label, source=_MODE_SOURCE)


class PowerCollector:
    key = "power"

    def __init__(
        self,
        status_fn: Callable[[], Optional[dict[str, int]]],
        overlay_fn: Callable[[], Optional[str]],
    ) -> None:
        self._status_fn = status_fn
        self._overlay_fn = overlay_fn

    def collect(self, ctx: CollectorContext) -> dict[str, Reading]:
        raw = self._status_fn()
        if raw is None:
            readings = {
                name: Reading.missing(name, Availability.UNAVAILABLE, "power status could not be read", _STATUS_SOURCE)
                for name in ("power.on_battery", "power.battery_percent", "power.charging", "power.battery_saver")
            }
        else:
            readings = decode_power_status(raw)
        readings["power.mode"] = decode_power_mode(self._overlay_fn())
        return readings
