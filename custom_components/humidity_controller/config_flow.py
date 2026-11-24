"""Config flow for humidity controller."""
from __future__ import annotations

import copy
from functools import partial

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

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
    DOMAIN,
    ENTRY_MINOR_VERSION,
    FAILSAFE_ACTIONS,
    HUMIDIFIER_METHODS,
    HUMIDITY_AGGREGATIONS,
    MAX_HUMIDITY,
    MIN_HUMIDITY,
    PRESET_NONE,
)
from .devices import find_speed_entity
from .entry import default_entry_data, sync_device_config
from .schedule import VALUE_KEY, coerce_points, validate_time

# Labels of menus whose entries are built at runtime (a dict ``menu_options``
# is shown verbatim, translations don't apply to it).
_LABELS = {
    "en": {
        "device_list": "➕ Add / remove devices",
        "preset_new": "➕ New preset",
        "init": "← Back",
        "points": "point(s)",
        "no_humidifiers": "(no humidifier.* devices — switches only do on/off)",
    },
    "ru": {
        "device_list": "➕ Добавить / убрать устройства",
        "preset_new": "➕ Новый пресет",
        "init": "← Назад",
        "points": "точк.",
        "no_humidifiers": "(нет устройств humidifier.* — выключатели только вкл/выкл)",
    },
}
_METHOD_LABELS = {
    "en": {"setpoint": "setpoint", "speed": "fan speed", "onoff": "on/off"},
    "ru": {"setpoint": "уставка", "speed": "скорость", "onoff": "вкл/выкл"},
}


def _humidity_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=MIN_HUMIDITY,
            max=MAX_HUMIDITY,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="%",
        )
    )


def _number(min_v, max_v, step, unit=None) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_v,
            max=max_v,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement=unit,
        )
    )


def _select(options, key) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(options),
            mode=selector.SelectSelectorMode.DROPDOWN,
            translation_key=key,
        )
    )


def _sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            filter=[
                {"domain": "sensor", "device_class": "humidity"},
                {"domain": "input_number"},
            ],
            multiple=True,
        )
    )


def _device_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=["humidifier", "switch", "input_boolean"], multiple=True
        )
    )


def _clean_ids(raw) -> list[str]:
    return [e for e in (raw or []) if e]


def _clean_name(raw) -> str:
    return str(raw or "").strip()


class HumidityControllerFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup: name, sensors and devices. Everything else in options."""

    VERSION = 1
    MINOR_VERSION = ENTRY_MINOR_VERSION

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            name = _clean_name(user_input.get("name")) or DEFAULT_NAME
            data = default_entry_data(name)
            data["humidity_sensors"] = _clean_ids(user_input.get("humidity_sensors"))
            data["humidity_aggregation"] = user_input.get(
                "humidity_aggregation", DEFAULT_AGGREGATION
            )
            data["devices"] = _clean_ids(user_input.get("devices"))
            data["device_config"] = sync_device_config(data["devices"], {})
            return self.async_create_entry(title=name, data=data)

        schema = vol.Schema(
            {
                vol.Required("name", default=DEFAULT_NAME): str,
                vol.Optional("humidity_sensors", default=[]): _sensor_selector(),
                vol.Required(
                    "humidity_aggregation", default=DEFAULT_AGGREGATION
                ): _select(HUMIDITY_AGGREGATIONS, "humidity_aggregation"),
                vol.Optional("devices", default=[]): _device_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HumidityControllerOptionsFlow()


class HumidityControllerOptionsFlow(config_entries.OptionsFlow):
    """Menu-driven options.

    ``init`` — Sensors & failsafe / Devices / Regulation / Presets / Save.
    Devices and presets are collections: their menus list one entry per
    item (``device_pick_<i>`` / ``preset_pick_<i>``, resolved in
    ``__getattr__``). Edits accumulate in ``self.data`` and are committed
    only by ``finish``; closing the dialog discards them.
    """

    def __getattr__(self, name: str):
        for prefix, handler in (
            ("async_step_device_pick_", self._pick_device),
            ("async_step_preset_pick_", self._pick_preset),
        ):
            if name.startswith(prefix):
                try:
                    index = int(name[len(prefix):])
                except ValueError:
                    break
                return partial(handler, index)
        raise AttributeError(name)

    def _label(self, key: str) -> str:
        lang = "ru" if str(self.hass.config.language or "").startswith("ru") else "en"
        return _LABELS[lang][key]

    def _method_label(self, method: str) -> str:
        lang = "ru" if str(self.hass.config.language or "").startswith("ru") else "en"
        return _METHOD_LABELS[lang].get(method, method)

    def _friendly(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        name = state.attributes.get("friendly_name") if state else None
        return f"{name} ({entity_id})" if name else entity_id

    # ------------------------------------------------------------------
    # Root
    # ------------------------------------------------------------------

    async def async_step_init(self, user_input=None):
        if not hasattr(self, "data"):
            self.data = {
                **default_entry_data(self.config_entry.title),
                **copy.deepcopy(dict(self.config_entry.data)),
            }
            self._preset: str | None = None
        return self.async_show_menu(
            step_id="init",
            menu_options=["sensors", "devices", "regulation", "presets", "finish"],
        )

    async def async_step_finish(self, user_input=None):
        self.hass.config_entries.async_update_entry(
            self.config_entry, title=self.data.get("name", DEFAULT_NAME), data=self.data
        )
        await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        return self.async_create_entry(title="", data={})

    # ------------------------------------------------------------------
    # Sensors & failsafe
    # ------------------------------------------------------------------

    async def async_step_sensors(self, user_input=None):
        if user_input is not None:
            if user_input.get("_back"):
                return await self.async_step_init()
            self.data["name"] = _clean_name(user_input.get("name")) or DEFAULT_NAME
            self.data["humidity_sensors"] = _clean_ids(user_input.get("humidity_sensors"))
            self.data["humidity_aggregation"] = user_input["humidity_aggregation"]
            self.data["sensor_timeout_minutes"] = float(user_input["sensor_timeout_minutes"])
            self.data["failsafe_action"] = user_input["failsafe_action"]
            return await self.async_step_init()

        d = self.data
        schema = {
            vol.Required("name", default=d.get("name", DEFAULT_NAME)): str,
            vol.Optional(
                "humidity_sensors", default=_clean_ids(d.get("humidity_sensors"))
            ): _sensor_selector(),
            vol.Required(
                "humidity_aggregation",
                default=d.get("humidity_aggregation", DEFAULT_AGGREGATION),
            ): _select(HUMIDITY_AGGREGATIONS, "humidity_aggregation"),
            vol.Required(
                "sensor_timeout_minutes",
                default=float(d.get("sensor_timeout_minutes", DEFAULT_SENSOR_TIMEOUT_MINUTES)),
            ): _number(1, 720, 1, "min"),
            vol.Required(
                "failsafe_action",
                default=d.get("failsafe_action", DEFAULT_FAILSAFE_ACTION),
            ): _select(FAILSAFE_ACTIONS, "failsafe_action"),
            vol.Required("_back", default=False): bool,
        }
        return self.async_show_form(step_id="sensors", data_schema=vol.Schema(schema))

    # ------------------------------------------------------------------
    # Regulation (hysteresis + min on/off)
    # ------------------------------------------------------------------

    async def async_step_regulation(self, user_input=None):
        if user_input is not None:
            if user_input.get("_back"):
                return await self.async_step_init()
            for key in ("hysteresis", "min_on_minutes", "min_off_minutes"):
                self.data[key] = float(user_input[key])
            return await self.async_step_init()

        d = self.data
        schema = {
            vol.Required(
                "hysteresis", default=float(d.get("hysteresis", DEFAULT_HYSTERESIS))
            ): _number(0.5, 20, 0.5, "%"),
            vol.Required(
                "min_on_minutes",
                default=float(d.get("min_on_minutes", DEFAULT_MIN_ON_MINUTES)),
            ): _number(0, 120, 1, "min"),
            vol.Required(
                "min_off_minutes",
                default=float(d.get("min_off_minutes", DEFAULT_MIN_OFF_MINUTES)),
            ): _number(0, 120, 1, "min"),
            vol.Required("_back", default=False): bool,
        }
        return self.async_show_form(step_id="regulation", data_schema=vol.Schema(schema))

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    def _devices_menu(self):
        options: dict[str, str] = {"device_list": self._label("device_list")}
        devices = _clean_ids(self.data.get("devices"))
        config = self.data.get("device_config") or {}
        for i, entity_id in enumerate(devices):
            if entity_id.split(".", 1)[0] != "humidifier":
                continue
            method = (config.get(entity_id) or {}).get("method", DEFAULT_METHOD)
            options[f"device_pick_{i}"] = (
                f"{self._friendly(entity_id)} — {self._method_label(method)}"
            )
        options["init"] = self._label("init")
        lines = [
            f"• {self._friendly(e)} — "
            + self._method_label((config.get(e) or {}).get("method", DEFAULT_METHOD))
            for e in devices
        ] or ["—"]
        if not any(e.startswith("humidifier.") for e in devices):
            lines.append(self._label("no_humidifiers"))
        return self.async_show_menu(
            step_id="devices",
            menu_options=options,
            description_placeholders={"summary": "\n".join(lines)},
        )

    async def async_step_devices(self, user_input=None):
        return self._devices_menu()

    async def async_step_device_list(self, user_input=None):
        if user_input is not None:
            if user_input.get("_back"):
                return self._devices_menu()
            devices = _clean_ids(user_input.get("devices"))
            self.data["devices"] = devices
            self.data["device_config"] = sync_device_config(
                devices, self.data.get("device_config") or {}
            )
            return self._devices_menu()
        schema = {
            vol.Optional(
                "devices", default=_clean_ids(self.data.get("devices"))
            ): _device_selector(),
            vol.Required("_back", default=False): bool,
        }
        return self.async_show_form(step_id="device_list", data_schema=vol.Schema(schema))

    async def _pick_device(self, index: int, user_input=None):
        devices = _clean_ids(self.data.get("devices"))
        if index >= len(devices):
            return self._devices_menu()
        self._device = devices[index]
        return await self.async_step_device(None)

    async def async_step_device(self, user_input=None):
        entity_id = self._device
        config = self.data.setdefault("device_config", {})
        cfg = dict(config.get(entity_id) or {})
        if user_input is not None:
            if user_input.get("_back"):
                return self._devices_menu()
            errors = {}
            if user_input["speed_min"] > user_input["speed_max"]:
                errors["speed_max"] = "speed_range"
            if not errors:
                cfg.update(
                    method=user_input["method"],
                    speed_entity=user_input.get("speed_entity") or None,
                    max_delta=float(user_input["max_delta"]),
                    speed_min=float(user_input["speed_min"]),
                    speed_max=float(user_input["speed_max"]),
                    kp=float(user_input["kp"]),
                    ki=float(user_input["ki"]),
                )
                config[entity_id] = cfg
                return self._devices_menu()
            return self.async_show_form(
                step_id="device",
                data_schema=self._device_schema(cfg),
                errors=errors,
                description_placeholders={"device": self._friendly(entity_id)},
            )
        return self.async_show_form(
            step_id="device",
            data_schema=self._device_schema(cfg),
            description_placeholders={"device": self._friendly(entity_id)},
        )

    def _device_schema(self, cfg: dict) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(
                    "method", default=cfg.get("method", DEFAULT_METHOD)
                ): _select(HUMIDIFIER_METHODS, "method"),
                vol.Required(
                    "max_delta", default=float(cfg.get("max_delta", DEFAULT_MAX_DELTA))
                ): _number(0, 40, 1, "%"),
                vol.Optional(
                    "speed_entity",
                    description={
                        "suggested_value": cfg.get("speed_entity")
                        or find_speed_entity(self.hass, self._device)
                    },
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["number", "input_number"])
                ),
                vol.Required(
                    "speed_min", default=float(cfg.get("speed_min", DEFAULT_SPEED_MIN))
                ): _number(0, 100, 1, "%"),
                vol.Required(
                    "speed_max", default=float(cfg.get("speed_max", DEFAULT_SPEED_MAX))
                ): _number(0, 100, 1, "%"),
                vol.Required("kp", default=float(cfg.get("kp", DEFAULT_KP))): _number(
                    0, 100, 0.5
                ),
                vol.Required("ki", default=float(cfg.get("ki", DEFAULT_KI))): _number(
                    0, 20, 0.05
                ),
                vol.Required("_back", default=False): bool,
            }
        )

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    def _presets(self) -> list[str]:
        return [p for p in (self.data.get("preset_order") or []) if p]

    def _presets_menu(self):
        options: dict[str, str] = {}
        humidities = self.data.get("preset_humidities") or {}
        schedules = self.data.get("preset_schedules") or {}
        for i, name in enumerate(self._presets()):
            label = f"{name} — {humidities.get(name, DEFAULT_TARGET_HUMIDITY):g} %"
            points = coerce_points(schedules.get(name))
            if points:
                label += f" (+{len(points)} {self._label('points')})"
            options[f"preset_pick_{i}"] = label
        options["preset_new"] = self._label("preset_new")
        options["init"] = self._label("init")
        return self.async_show_menu(step_id="presets", menu_options=options)

    async def async_step_presets(self, user_input=None):
        return self._presets_menu()

    def _preset_name_error(self, name: str, current: str | None) -> str | None:
        if not name or name == PRESET_NONE:
            return "invalid_name"
        if name != current and name in self._presets():
            return "duplicate_name"
        return None

    async def async_step_preset_new(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input.get("_back"):
                return self._presets_menu()
            name = _clean_name(user_input.get("name"))
            error = self._preset_name_error(name, None)
            if error:
                errors["name"] = error
            else:
                self.data.setdefault("preset_order", []).append(name)
                self.data.setdefault("preset_humidities", {})[name] = float(user_input["humidity"])
                self.data.setdefault("preset_schedules", {})[name] = []
                return self._presets_menu()
        schema = {
            vol.Required("name"): str,
            vol.Required("humidity", default=DEFAULT_TARGET_HUMIDITY): _humidity_selector(),
            vol.Required("_back", default=False): bool,
        }
        return self.async_show_form(
            step_id="preset_new", data_schema=vol.Schema(schema), errors=errors
        )

    async def _pick_preset(self, index: int, user_input=None):
        presets = self._presets()
        if index >= len(presets):
            return self._presets_menu()
        self._preset = presets[index]
        return self._preset_menu()

    def _points(self) -> list[dict]:
        return coerce_points((self.data.get("preset_schedules") or {}).get(self._preset))

    def _preset_menu(self):
        points = self._points()
        options = ["preset_edit", "preset_point_add"]
        if points:
            options.append("preset_bulk")
        options += ["preset_delete", "presets"]
        humidity = (self.data.get("preset_humidities") or {}).get(
            self._preset, DEFAULT_TARGET_HUMIDITY
        )
        summary = "\n".join(f"  {p['time']} → {p[VALUE_KEY]:g} %" for p in points) or "—"
        return self.async_show_menu(
            step_id="preset",
            menu_options=options,
            description_placeholders={
                "preset": self._preset,
                "humidity": f"{humidity:g}",
                "summary": summary,
            },
        )

    async def async_step_preset(self, user_input=None):
        return self._preset_menu()

    async def async_step_preset_edit(self, user_input=None):
        old = self._preset
        humidities = self.data.setdefault("preset_humidities", {})
        errors = {}
        if user_input is not None:
            if user_input.get("_back"):
                return self._preset_menu()
            name = _clean_name(user_input.get("name"))
            error = self._preset_name_error(name, old)
            if error:
                errors["name"] = error
            else:
                if name != old:
                    order = self.data["preset_order"]
                    order[order.index(old)] = name
                    humidities.pop(old, None)
                    schedules = self.data.setdefault("preset_schedules", {})
                    schedules[name] = schedules.pop(old, [])
                    self._preset = name
                humidities[name] = float(user_input["humidity"])
                return self._preset_menu()
        schema = {
            vol.Required("name", default=old): str,
            vol.Required(
                "humidity", default=float(humidities.get(old, DEFAULT_TARGET_HUMIDITY))
            ): _humidity_selector(),
            vol.Required("_back", default=False): bool,
        }
        return self.async_show_form(
            step_id="preset_edit",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"preset": old},
        )

    async def async_step_preset_point_add(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input.get("_back"):
                return self._preset_menu()
            t = validate_time(user_input.get("time"))
            if t is None:
                errors["time"] = "invalid_time"
            else:
                points = self._points() + [{"time": t, VALUE_KEY: float(user_input["humidity"])}]
                self.data.setdefault("preset_schedules", {})[self._preset] = coerce_points(points)
                return self._preset_menu()
        default = (self.data.get("preset_humidities") or {}).get(
            self._preset, DEFAULT_TARGET_HUMIDITY
        )
        schema = {
            vol.Required("time", default="00:00"): selector.TimeSelector(),
            vol.Required("humidity", default=float(default)): _humidity_selector(),
            vol.Required("_back", default=False): bool,
        }
        return self.async_show_form(
            step_id="preset_point_add",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"preset": self._preset},
        )

    async def async_step_preset_bulk(self, user_input=None):
        points = self._points()
        if not points:
            return self._preset_menu()
        errors = {}
        if user_input is not None:
            if user_input.get("_back"):
                return self._preset_menu()
            kept = []
            for i in range(len(points)):
                if user_input.get(f"row_{i}_delete"):
                    continue
                t = validate_time(user_input.get(f"row_{i}_time"))
                if t is None:
                    errors[f"row_{i}_time"] = "invalid_time"
                    continue
                kept.append({"time": t, VALUE_KEY: float(user_input[f"row_{i}_humidity"])})
            if not errors:
                self.data.setdefault("preset_schedules", {})[self._preset] = coerce_points(kept)
                return self._preset_menu()
        schema: dict = {}
        for i, p in enumerate(points):
            schema[vol.Required(f"row_{i}_time", default=p["time"])] = selector.TimeSelector()
            schema[vol.Required(f"row_{i}_humidity", default=float(p[VALUE_KEY]))] = (
                _humidity_selector()
            )
            schema[vol.Required(f"row_{i}_delete", default=False)] = bool
        schema[vol.Required("_back", default=False)] = bool
        return self.async_show_form(
            step_id="preset_bulk",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"preset": self._preset},
        )

    async def async_step_preset_delete(self, user_input=None):
        if user_input is not None:
            if user_input.get("_back") or not user_input.get("confirm"):
                return self._preset_menu()
            name = self._preset
            self.data["preset_order"] = [p for p in self._presets() if p != name]
            (self.data.get("preset_humidities") or {}).pop(name, None)
            (self.data.get("preset_schedules") or {}).pop(name, None)
            self._preset = None
            return self._presets_menu()
        schema = {
            vol.Required("confirm", default=False): bool,
            vol.Required("_back", default=False): bool,
        }
        return self.async_show_form(
            step_id="preset_delete",
            data_schema=vol.Schema(schema),
            description_placeholders={"preset": self._preset},
        )
