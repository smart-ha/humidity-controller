"""Shape of the config entry data, shared by setup and the config flow.

No Home Assistant imports: the tests load it together with ``const``.
"""
from __future__ import annotations

from .const import (
    DEFAULT_AGGREGATION,
    DEFAULT_FAILSAFE_ACTION,
    DEFAULT_HYSTERESIS,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_MAX_DELTA,
    DEFAULT_METHOD,
    DEFAULT_MIN_OFF_MINUTES,
    DEFAULT_MIN_ON_MINUTES,
    DEFAULT_NAME,
    DEFAULT_PRESET_ORDER,
    DEFAULT_PRESETS,
    DEFAULT_SENSOR_TIMEOUT_MINUTES,
    DEFAULT_SPEED_MAX,
    DEFAULT_SPEED_MIN,
    METHOD_ONOFF,
)


def default_entry_data(name: str | None = None) -> dict:
    return {
        "name": name or DEFAULT_NAME,
        "humidity_sensors": [],
        "humidity_aggregation": DEFAULT_AGGREGATION,
        "devices": [],
        "device_config": {},
        "hysteresis": DEFAULT_HYSTERESIS,
        "min_on_minutes": DEFAULT_MIN_ON_MINUTES,
        "min_off_minutes": DEFAULT_MIN_OFF_MINUTES,
        "sensor_timeout_minutes": DEFAULT_SENSOR_TIMEOUT_MINUTES,
        "failsafe_action": DEFAULT_FAILSAFE_ACTION,
        "preset_order": list(DEFAULT_PRESET_ORDER),
        "preset_humidities": dict(DEFAULT_PRESETS),
        "preset_schedules": {p: [] for p in DEFAULT_PRESET_ORDER},
    }


def default_device_config(entity_id: str) -> dict:
    """Per-device settings; switch / input_boolean only ever do on/off."""
    if entity_id.split(".", 1)[0] != "humidifier":
        return {"method": METHOD_ONOFF}
    return {
        "method": DEFAULT_METHOD,
        "speed_entity": None,
        "max_delta": DEFAULT_MAX_DELTA,
        "speed_min": DEFAULT_SPEED_MIN,
        "speed_max": DEFAULT_SPEED_MAX,
        "kp": DEFAULT_KP,
        "ki": DEFAULT_KI,
    }


def sync_device_config(devices: list[str], previous: dict) -> dict:
    """Device list changed: keep settings of the survivors, defaults for new."""
    out = {}
    for entity_id in devices:
        cfg = default_device_config(entity_id)
        cfg.update(previous.get(entity_id) or {})
        out[entity_id] = cfg
    return out
