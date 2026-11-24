"""The humidity controller integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import ENTRY_MINOR_VERSION
from .entry import default_entry_data, migrate_to_speed

PLATFORMS = ["humidifier"]

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring an older entry up to the current minor version."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        hass.config_entries.async_update_entry(
            entry, data=migrate_to_speed(dict(entry.data)), minor_version=ENTRY_MINOR_VERSION
        )
        _LOGGER.info(
            "humidity_controller[%s]: humidifiers moved from setpoint to fan speed control",
            entry.title,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one humidity zone from a config entry."""
    # Fill in keys added after the entry was written, so readers never have
    # to guard for a missing setting.
    data = {**default_entry_data(entry.data.get("name") or entry.title), **entry.data}
    if data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=data)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
