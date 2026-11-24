import datetime

from humidity_controller.const import METHOD_ONOFF, METHOD_SETPOINT, METHOD_SPEED
from humidity_controller.entry import (
    default_entry_data,
    migrate_to_speed,
    pick_speed_entity,
    sync_device_config,
)
from humidity_controller.schedule import coerce_points, target_at, validate_time


def t(s):
    return datetime.time.fromisoformat(s)


def test_validate_time():
    assert validate_time("07:05") == "07:05"
    assert validate_time(datetime.time(7, 5)) == "07:05"
    assert validate_time("07:05:30") == "07:05"
    assert validate_time("25:00") is None
    assert validate_time(None) is None


def test_coerce_points_sorts_dedups_and_drops_garbage():
    raw = [
        {"time": "22:00", "humidity": 50},
        {"time": "07:00:00", "humidity": "45"},
        {"time": "22:00", "humidity": 55},
        {"time": "bad", "humidity": 1},
        {"time": "08:00"},
        "junk",
    ]
    assert coerce_points(raw) == [
        {"time": "07:00", "humidity": 45.0},
        {"time": "22:00", "humidity": 55.0},
    ]
    assert coerce_points("21:00=40") == []


def test_target_at_empty_and_single():
    assert target_at([], t("12:00")) is None
    assert target_at([{"time": "10:00", "humidity": 42}], t("03:00")) == 42


def test_target_at_interpolates_and_wraps():
    sched = [{"time": "06:00", "humidity": 40}, {"time": "18:00", "humidity": 52}]
    assert target_at(sched, t("06:00")) == 40
    assert target_at(sched, t("12:00")) == 46
    assert target_at(sched, t("18:00")) == 52
    # 18:00 → 06:00 over midnight: midpoint at 00:00
    assert target_at(sched, t("00:00")) == 46


def test_default_entry_data_has_presets():
    data = default_entry_data("Спальня")
    assert data["name"] == "Спальня"
    assert data["preset_order"] == list(data["preset_humidities"])
    assert all(data["preset_schedules"][p] == [] for p in data["preset_order"])


def test_sync_device_config_keeps_survivors():
    prev = {"humidifier.a": {"method": "setpoint", "speed_entity": "number.a"}}
    out = sync_device_config(["humidifier.a", "humidifier.b", "switch.c"], prev)
    assert out["humidifier.a"]["method"] == METHOD_SETPOINT
    assert out["humidifier.a"]["speed_entity"] == "number.a"
    assert out["humidifier.a"]["max_delta"] > 0  # defaults filled in
    assert out["humidifier.b"]["method"] == METHOD_SPEED  # new default
    assert out["humidifier.b"]["speed_entity"] is None  # found at runtime
    assert out["switch.c"] == {"method": METHOD_ONOFF}
    assert set(out) == {"humidifier.a", "humidifier.b", "switch.c"}


def test_pick_speed_entity():
    airmx = [
        "humidifier.a3s_v2_1",
        "number.a3s_v2_1_fan_speed",
        "sensor.a3s_v2_1_humidity",
        "switch.a3s_v2_1_ionization",
    ]
    assert pick_speed_entity(airmx) == "number.a3s_v2_1_fan_speed"
    assert pick_speed_entity(["number.x_target", "number.x_motor_speed"]) == "number.x_motor_speed"
    assert pick_speed_entity(["humidifier.x", "number.x_level"]) == "number.x_level"
    assert pick_speed_entity(["number.x_a", "number.x_b"]) is None  # ambiguous
    assert pick_speed_entity(["humidifier.x", "sensor.x_speed"]) is None
    assert pick_speed_entity([]) is None


def test_migrate_to_speed_moves_only_setpoint_humidifiers():
    data = {
        "name": "base",
        "device_config": {
            "humidifier.a": {"method": "setpoint", "speed_entity": None, "kp": 7.0},
            "humidifier.b": {"method": "onoff"},
            "humidifier.c": {"method": "speed", "speed_entity": "number.c"},
            "switch.d": {"method": "onoff"},
        },
    }
    out = migrate_to_speed(data)
    assert out["name"] == "base"
    assert out["device_config"]["humidifier.a"] == {
        "method": "speed", "speed_entity": None, "kp": 7.0,
    }
    assert out["device_config"]["humidifier.b"] == {"method": "onoff"}
    assert out["device_config"]["humidifier.c"]["speed_entity"] == "number.c"
    assert out["device_config"]["switch.d"] == {"method": "onoff"}
    assert data["device_config"]["humidifier.a"]["method"] == "setpoint"  # input untouched
    assert migrate_to_speed({})["device_config"] == {}
