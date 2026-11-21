import pytest

from humidity_controller.control import (
    Hysteresis,
    SpeedRegulator,
    aggregate,
    corrected_setpoint,
    quantize,
    speed_changed,
)


@pytest.mark.parametrize(
    ("method", "expected"),
    [("mean", 45.0), ("min", 40.0), ("max", 50.0), ("median", 45.0), ("unknown", 45.0)],
)
def test_aggregate(method, expected):
    assert aggregate([40.0, 45.0, 50.0], method) == expected


def test_aggregate_empty_is_none():
    assert aggregate([], "mean") is None


def test_setpoint_shifts_by_device_offset():
    # Device reads 55 where our sensors read 45: to stop at our 50 it must aim at 60.
    assert corrected_setpoint(50, 45, 55, 20) == 60
    # Device reads lower than us → asked for less.
    assert corrected_setpoint(50, 48, 44, 20) == 46


def test_setpoint_correction_is_limited():
    assert corrected_setpoint(50, 40, 70, 10) == 60
    assert corrected_setpoint(50, 70, 40, 10) == 40


def test_setpoint_without_device_reading_is_target():
    assert corrected_setpoint(47.4, 40, None, 10) == 47


def test_setpoint_respects_device_range():
    assert corrected_setpoint(60, 40, 60, 30, device_min=30, device_max=70) == 70
    assert corrected_setpoint(35, 60, 40, 30, device_min=30, device_max=70) == 30


def test_hysteresis_band():
    h = Hysteresis(0, 0)
    assert h.update(48, 50, 3, 0) is False  # inside the band, stays off
    assert h.update(47, 50, 3, 1) is True  # target − hysteresis → on
    assert h.update(49.9, 50, 3, 2) is True  # still below target
    assert h.update(50, 50, 3, 3) is False  # target reached → off
    assert h.update(48, 50, 3, 4) is False


def test_hysteresis_min_times():
    h = Hysteresis(min_on_s=300, min_off_s=600)
    assert h.update(40, 50, 3, 0) is True  # first transition is free
    assert h.update(55, 50, 3, 100) is True  # held: on for only 100 s
    assert h.update(55, 50, 3, 300) is False
    assert h.update(40, 50, 3, 500) is False  # held: off for only 200 s
    assert h.update(40, 50, 3, 900) is True


def test_hysteresis_starts_from_device_state():
    h = Hysteresis(0, 0, running=True)
    assert h.update(48, 50, 3, 0) is True


def test_speed_proportional_and_clamped():
    r = SpeedRegulator(kp=10, ki=0, speed_min=20, speed_max=80)
    assert r.update(48, 50, 30) == 40
    assert r.update(30, 50, 30) == 80
    assert r.update(55, 50, 30) == 20


def test_speed_integral_accumulates_and_is_bounded():
    r = SpeedRegulator(kp=0, ki=1, speed_min=20, speed_max=100)
    # 2 %RH deficit for one minute → +2 %
    assert r.update(48, 50, 60) == pytest.approx(22)
    for _ in range(1000):
        r.update(30, 50, 60)
    assert r.integral == 80  # anti-windup: never beyond the span
    r.reset()
    assert r.integral == 0


def test_speed_integral_does_not_go_negative():
    r = SpeedRegulator(kp=0, ki=1, speed_min=20, speed_max=100)
    r.update(60, 50, 600)
    assert r.integral == 0


def test_quantize():
    assert quantize(42.4, 1) == 42
    assert quantize(42.6, 5, 20, 100) == 45
    assert quantize(150, 1, 0, 100) == 100


def test_speed_changed():
    assert speed_changed(40, None, 20, 100, 5)
    assert not speed_changed(42, 40, 20, 100, 5)
    assert speed_changed(45, 40, 20, 100, 5)
    assert speed_changed(100, 98, 20, 100, 5)  # a bound is always sent
    assert not speed_changed(40, 40, 20, 100, 5)
