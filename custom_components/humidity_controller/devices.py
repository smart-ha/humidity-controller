"""Home Assistant lookups about managed devices."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .entry import pick_speed_entity


def find_speed_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    """Fan speed ``number.*`` on the same device as ``entity_id``, if any."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None or entry.device_id is None:
        return None
    candidates = [
        e.entity_id
        for e in er.async_entries_for_device(registry, entry.device_id)
        if e.disabled_by is None
    ]
    return pick_speed_entity(candidates)
