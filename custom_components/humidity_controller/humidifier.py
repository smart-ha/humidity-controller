"""Humidity controller humidifier entity."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.humidifier import (
    HumidifierAction,
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity

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
    DEFAULT_SENSOR_TIMEOUT_MINUTES,
    DEFAULT_SPEED_MAX,
    DEFAULT_SPEED_MIN,
    DEFAULT_TARGET_HUMIDITY,
    DEVICE_MODE_AUTO,
    DEVICE_MODE_MANUAL,
    FAILSAFE_OFF,
    MAX_HUMIDITY,
    METHOD_ONOFF,
    METHOD_SETPOINT,
    METHOD_SPEED,
    MIN_HUMIDITY,
    PRESET_NONE,
    SETPOINT_MIN_INTERVAL_SECONDS,
    SPEED_MIN_CHANGE,
    TICK_INTERVAL_SECONDS,
)
from .control import (
    Hysteresis,
    SpeedRegulator,
    aggregate,
    corrected_setpoint,
    quantize,
    speed_changed,
)
from .schedule import target_at

_LOGGER = logging.getLogger(__name__)

_UNUSABLE = (STATE_UNAVAILABLE, STATE_UNKNOWN, None, "")

# A setpoint device is re-sent at once (ignoring the rate limit) when the new
# value moved at least this far from the last one sent — a preset change must
# not wait a minute.
SETPOINT_JUMP = 3


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the humidity controller entity."""
    async_add_entities([HumidityControllerEntity(config_entry)])


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _usable(state: State | None) -> bool:
    return state is not None and state.state not in _UNUSABLE


@dataclass
class _Device:
    """Runtime of one managed device."""

    entity_id: str
    config: dict
    hysteresis: Hysteresis | None = None
    speed: SpeedRegulator | None = None
    last_eval: float | None = None
    last_setpoint: int | None = None
    last_setpoint_ts: float | None = None
    method: str | None = None  # effective method on the last evaluation
    status: dict = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    def reset(self) -> None:
        """Forget regulation state (controller off, failsafe, device back)."""
        self.hysteresis = None
        if self.speed is not None:
            self.speed.reset()
        self.last_eval = None
        self.last_setpoint = None
        self.last_setpoint_ts = None


class HumidityControllerEntity(HumidifierEntity, RestoreEntity):
    """One zone: aggregated humidity sensors driving a set of devices."""

    _attr_device_class = HumidifierDeviceClass.HUMIDIFIER
    _attr_supported_features = HumidifierEntityFeature.MODES
    _attr_min_humidity = MIN_HUMIDITY
    _attr_max_humidity = MAX_HUMIDITY
    _attr_should_poll = False
    _attr_translation_key = "controller"

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        data = config_entry.data
        self._name = data.get("name", DEFAULT_NAME)
        self._attr_name = self._name
        self._attr_unique_id = config_entry.entry_id

        self._preset_order: list[str] = [
            p for p in (data.get("preset_order") or []) if p and p != PRESET_NONE
        ]
        self._preset_humidities: dict = data.get("preset_humidities") or {}
        self._preset_schedules: dict = data.get("preset_schedules") or {}
        self._attr_available_modes = [PRESET_NONE, *self._preset_order]

        self._sensors: list[str] = [s for s in (data.get("humidity_sensors") or []) if s]
        self._aggregation = data.get("humidity_aggregation") or DEFAULT_AGGREGATION
        self._hysteresis = float(data.get("hysteresis", DEFAULT_HYSTERESIS))
        self._min_on_s = float(data.get("min_on_minutes", DEFAULT_MIN_ON_MINUTES)) * 60
        self._min_off_s = float(data.get("min_off_minutes", DEFAULT_MIN_OFF_MINUTES)) * 60
        self._timeout_s = (
            float(data.get("sensor_timeout_minutes", DEFAULT_SENSOR_TIMEOUT_MINUTES)) * 60
        )
        self._failsafe_action = data.get("failsafe_action") or DEFAULT_FAILSAFE_ACTION

        device_config = data.get("device_config") or {}
        self._devices = [
            _Device(entity_id, dict(device_config.get(entity_id) or {}))
            for entity_id in (data.get("devices") or [])
            if entity_id
        ]

        self._is_on = False
        self._mode = self._preset_order[0] if self._preset_order else PRESET_NONE
        self._target: float = DEFAULT_TARGET_HUMIDITY
        self._measured: float | None = None
        self._readings: dict[str, float | None] = {}
        self._action = HumidifierAction.OFF
        self._failsafe = False
        # Monotonic time of the last usable reading. Starts at setup, so a
        # zone whose sensors never come up still reaches the failsafe.
        self._last_reading_ts = time.monotonic()
        self._lock = asyncio.Lock()
        self._unsubs: list = []

    # ------------------------------------------------------------------
    # Entity properties
    # ------------------------------------------------------------------

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def target_humidity(self) -> float:
        return self._target

    @property
    def current_humidity(self) -> float | None:
        return None if self._measured is None else round(self._measured, 1)

    @property
    def action(self) -> HumidifierAction:
        return self._action

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "humidity_readings": dict(self._readings),
            "humidity_sensors_unavailable": [
                s for s, v in self._readings.items() if v is None
            ],
            "humidity_aggregation": self._aggregation,
            "failsafe": self._failsafe,
            "devices": {d.entity_id: dict(d.status) for d in self._devices},
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last = await self.async_get_last_state()
        if last is not None:
            self._is_on = last.state == STATE_ON
            mode = last.attributes.get("mode")
            if mode in self._attr_available_modes:
                self._mode = mode
            target = _float(last.attributes.get("humidity"))
            if target is not None:
                self._target = target
            _LOGGER.info(
                "humidity_controller[%s]: restored on=%s mode=%s target=%s",
                self._name, self._is_on, self._mode, self._target,
            )
        self._apply_preset_target()

        if self._sensors:
            self._unsubs.append(
                async_track_state_change_event(self.hass, self._sensors, self._on_sensor)
            )
        device_ids = [d.entity_id for d in self._devices]
        if device_ids:
            self._unsubs.append(
                async_track_state_change_event(self.hass, device_ids, self._on_device)
            )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._on_tick, dt.timedelta(seconds=TICK_INTERVAL_SECONDS)
            )
        )
        self.hass.async_create_task(self._async_evaluate())

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await super().async_will_remove_from_hass()

    @callback
    def _on_sensor(self, _event: Event) -> None:
        self.hass.async_create_task(self._async_evaluate())

    @callback
    def _on_tick(self, _now) -> None:
        self.hass.async_create_task(self._async_evaluate())

    @callback
    def _on_device(self, event: Event) -> None:
        """A managed device came back from unavailable: reconcile it now."""
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if not _usable(new) or _usable(old):
            return
        entity_id = event.data.get("entity_id")
        for device in self._devices:
            if device.entity_id == entity_id:
                device.reset()
        _LOGGER.info("humidity_controller[%s]: %s is back, reconciling", self._name, entity_id)
        self.hass.async_create_task(self._async_evaluate())

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        self.async_write_ha_state()
        await self._async_evaluate()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        self._action = HumidifierAction.OFF
        async with self._lock:
            for device in self._devices:
                device.reset()
                await self._ensure_power(device.entity_id, False)
                device.status = {"method": device.method, "running": False}
        self.async_write_ha_state()

    async def async_set_humidity(self, humidity: int) -> None:
        """A hand-set target overrides the preset (drops to ``none``)."""
        self._target = float(humidity)
        self._mode = PRESET_NONE
        self.async_write_ha_state()
        await self._async_evaluate()

    async def async_set_mode(self, mode: str) -> None:
        if mode not in self._attr_available_modes:
            raise HomeAssistantError(f"Unknown mode {mode}")
        self._mode = mode
        self._apply_preset_target()
        self.async_write_ha_state()
        await self._async_evaluate()

    # ------------------------------------------------------------------
    # Control loop
    # ------------------------------------------------------------------

    def _apply_preset_target(self) -> None:
        """Active preset → target: schedule point if any, else its default."""
        if self._mode == PRESET_NONE:
            return
        scheduled = target_at(
            self._preset_schedules.get(self._mode) or [], dt.datetime.now().time()
        )
        if scheduled is not None:
            self._target = round(scheduled, 1)
            return
        default = _float(self._preset_humidities.get(self._mode))
        if default is not None:
            self._target = default

    def _read_sensors(self) -> float | None:
        readings: dict[str, float | None] = {}
        for entity_id in self._sensors:
            state = self.hass.states.get(entity_id)
            readings[entity_id] = _float(state.state) if _usable(state) else None
        self._readings = readings
        return aggregate([v for v in readings.values() if v is not None], self._aggregation)

    async def _async_evaluate(self) -> None:
        if self._lock.locked():
            # A sensor burst while the previous pass is still sending
            # commands: that pass already sees the fresh reading.
            return
        async with self._lock:
            try:
                await self._evaluate()
            except Exception:  # noqa: BLE001 — never kill the loop
                _LOGGER.exception("humidity_controller[%s]: evaluation failed", self._name)
        self.async_write_ha_state()

    async def _evaluate(self) -> None:
        now = time.monotonic()
        self._apply_preset_target()
        measured = self._read_sensors()

        if measured is not None:
            self._measured = measured
            self._last_reading_ts = now
            if self._failsafe:
                _LOGGER.warning(
                    "humidity_controller[%s]: sensors are back (%.1f%%), leaving failsafe",
                    self._name, measured,
                )
                self._failsafe = False
                for device in self._devices:
                    device.reset()

        if not self._is_on:
            self._action = HumidifierAction.OFF
            return

        if measured is None:
            if now - self._last_reading_ts < self._timeout_s:
                # Transient gap: keep the devices and the UI as they are —
                # except a just-switched-on entity, which is not "off".
                if self._action == HumidifierAction.OFF:
                    self._action = HumidifierAction.IDLE
                return
            if not self._failsafe:
                _LOGGER.warning(
                    "humidity_controller[%s]: no sensor reading for %.0f min, failsafe '%s'",
                    self._name, (now - self._last_reading_ts) / 60, self._failsafe_action,
                )
                self._failsafe = True
                self._measured = None
            humidifying = False
            for device in self._devices:
                humidifying |= await self._drive_failsafe(device)
            self._action = HumidifierAction.HUMIDIFYING if humidifying else HumidifierAction.IDLE
            return

        humidifying = False
        for device in self._devices:
            humidifying |= await self._drive(device, measured, now)
        self._action = HumidifierAction.HUMIDIFYING if humidifying else HumidifierAction.IDLE

    def _effective_method(self, device: _Device, state: State) -> str:
        """Configured method, or ``onoff`` when the device can't do it."""
        if device.domain != "humidifier":
            return METHOD_ONOFF
        method = device.config.get("method", DEFAULT_METHOD)
        has_modes = bool(
            int(state.attributes.get("supported_features") or 0) & HumidifierEntityFeature.MODES
        )
        modes = state.attributes.get("available_modes") or []
        if method == METHOD_SETPOINT:
            if has_modes and DEVICE_MODE_AUTO not in modes:
                return METHOD_ONOFF
            return METHOD_SETPOINT
        if method == METHOD_SPEED:
            speed_entity = device.config.get("speed_entity")
            if (
                not speed_entity
                or not _usable(self.hass.states.get(speed_entity))
                or not has_modes
                or DEVICE_MODE_MANUAL not in modes
            ):
                return METHOD_ONOFF
            return METHOD_SPEED
        return METHOD_ONOFF

    async def _drive(self, device: _Device, measured: float, now: float) -> bool:
        """Drive one device for this tick. Returns "is it humidifying"."""
        state = self.hass.states.get(device.entity_id)
        if not _usable(state):
            device.status = {"method": device.method, "unavailable": True}
            return False

        method = self._effective_method(device, state)
        if method != device.method:
            if device.method is not None:
                _LOGGER.info(
                    "humidity_controller[%s]: %s method %s → %s",
                    self._name, device.entity_id, device.method, method,
                )
            device.reset()
            device.method = method

        if method == METHOD_SETPOINT:
            return await self._drive_setpoint(device, state, measured, now)

        if device.hysteresis is None:
            device.hysteresis = Hysteresis(
                self._min_on_s, self._min_off_s, running=state.state == STATE_ON
            )
        running = device.hysteresis.update(measured, self._target, self._hysteresis, now)

        if method == METHOD_SPEED:
            await self._drive_speed(device, running, measured, now)
        else:
            await self._ensure_power(device.entity_id, running)
            device.status = {"method": method, "running": running}
        device.last_eval = now
        return running

    async def _drive_setpoint(
        self, device: _Device, state: State, measured: float, now: float
    ) -> bool:
        attrs = state.attributes
        own_reading = _float(attrs.get("current_humidity"))
        value = corrected_setpoint(
            self._target,
            measured,
            own_reading,
            float(device.config.get("max_delta", DEFAULT_MAX_DELTA)),
            _float(attrs.get("min_humidity")),
            _float(attrs.get("max_humidity")),
        )
        await self._ensure_power(device.entity_id, True)
        await self._ensure_mode(device.entity_id, DEVICE_MODE_AUTO)

        current = _float(attrs.get("humidity"))
        if current is None or int(round(current)) != value:
            due = (
                device.last_setpoint_ts is None
                or device.last_setpoint is None
                or now - device.last_setpoint_ts >= SETPOINT_MIN_INTERVAL_SECONDS
                or abs(value - device.last_setpoint) >= SETPOINT_JUMP
            )
            if due:
                await self._call("humidifier", "set_humidity", device.entity_id, humidity=value)
                device.last_setpoint = value
                device.last_setpoint_ts = now

        own_action = attrs.get("action")
        if own_action is not None:
            humidifying = own_action == HumidifierAction.HUMIDIFYING
        else:
            humidifying = measured < self._target
        device.status = {
            "method": METHOD_SETPOINT,
            "setpoint": value,
            "device_humidity": own_reading,
            "running": humidifying,
        }
        return humidifying

    async def _drive_speed(
        self, device: _Device, running: bool, measured: float, now: float
    ) -> None:
        cfg = device.config
        speed_min = float(cfg.get("speed_min", DEFAULT_SPEED_MIN))
        speed_max = float(cfg.get("speed_max", DEFAULT_SPEED_MAX))
        if device.speed is None:
            device.speed = SpeedRegulator(
                float(cfg.get("kp", DEFAULT_KP)),
                float(cfg.get("ki", DEFAULT_KI)),
                speed_min,
                speed_max,
            )
        if not running:
            device.speed.reset()
            await self._ensure_power(device.entity_id, False)
            device.status = {"method": METHOD_SPEED, "running": False}
            return

        dt_s = TICK_INTERVAL_SECONDS if device.last_eval is None else max(now - device.last_eval, 0.0)
        raw = device.speed.update(measured, self._target, dt_s)
        speed_entity = cfg["speed_entity"]
        speed_state = self.hass.states.get(speed_entity)
        attrs = speed_state.attributes
        entity_min = _float(attrs.get("min"))
        entity_max = _float(attrs.get("max"))
        low = max(speed_min, entity_min) if entity_min is not None else speed_min
        high = min(speed_max, entity_max) if entity_max is not None else speed_max
        speed = quantize(raw, _float(attrs.get("step")), low, high)

        await self._ensure_power(device.entity_id, True)
        await self._ensure_mode(device.entity_id, DEVICE_MODE_MANUAL)
        current = _float(speed_state.state)
        if speed_changed(speed, current, low, high, SPEED_MIN_CHANGE):
            domain = speed_entity.split(".", 1)[0]
            await self._call(domain, "set_value", speed_entity, value=speed)
        device.status = {
            "method": METHOD_SPEED,
            "running": True,
            "speed": speed,
        }

    async def _drive_failsafe(self, device: _Device) -> bool:
        """No readings: switch off, or hand the device its own auto mode."""
        device.reset()
        state = self.hass.states.get(device.entity_id)
        if not _usable(state):
            device.status = {"method": "failsafe", "unavailable": True}
            return False
        if self._failsafe_action == FAILSAFE_OFF or device.domain != "humidifier":
            await self._ensure_power(device.entity_id, False)
            device.status = {"method": "failsafe", "running": False}
            return False
        attrs = state.attributes
        value = corrected_setpoint(
            self._target, self._target, None, 0,
            _float(attrs.get("min_humidity")), _float(attrs.get("max_humidity")),
        )
        await self._ensure_power(device.entity_id, True)
        await self._ensure_mode(device.entity_id, DEVICE_MODE_AUTO)
        current = _float(attrs.get("humidity"))
        if current is None or int(round(current)) != value:
            await self._call("humidifier", "set_humidity", device.entity_id, humidity=value)
        device.status = {"method": "failsafe", "setpoint": value, "running": True}
        return attrs.get("action") == HumidifierAction.HUMIDIFYING

    # ------------------------------------------------------------------
    # State-aware, idempotent service callers
    # ------------------------------------------------------------------

    async def _call(self, domain: str, service: str, entity_id: str, **data) -> None:
        try:
            await self.hass.services.async_call(
                domain, service, {"entity_id": entity_id, **data}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 — one device must not stall the zone
            _LOGGER.warning(
                "humidity_controller[%s]: %s.%s on %s failed: %s",
                self._name, domain, service, entity_id, err,
            )

    async def _ensure_power(self, entity_id: str, on: bool) -> None:
        state = self.hass.states.get(entity_id)
        if not _usable(state):
            return
        if (state.state == STATE_ON) == on:
            return
        domain = entity_id.split(".", 1)[0]
        await self._call(domain, "turn_on" if on else "turn_off", entity_id)

    async def _ensure_mode(self, entity_id: str, mode: str) -> None:
        state = self.hass.states.get(entity_id)
        if not _usable(state):
            return
        if not int(state.attributes.get("supported_features") or 0) & HumidifierEntityFeature.MODES:
            return
        if mode not in (state.attributes.get("available_modes") or []):
            return
        if state.attributes.get("mode") == mode:
            return
        await self._call("humidifier", "set_mode", entity_id, mode=mode)
