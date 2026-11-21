"""The humidity controller integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .entry import default_entry_data

PLATFORMS = ["humidifier"]

_LOGGER = logging.getLogger(__name__)


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
