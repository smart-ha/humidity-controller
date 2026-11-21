"""Regulation math for humidity_controller.

Pure stdlib — no Home Assistant imports and no package-relative imports, so
the tests load this module on its own. Everything that touches ``hass`` lives
in ``humidifier.py``; everything that decides *what* to send lives here.
"""
from __future__ import annotations

import statistics


def aggregate(values: list[float], method: str) -> float | None:
    """Collapse readings into one value. ``None`` when there is nothing."""
    if not values:
        return None
    if method == "min":
        return min(values)
    if method == "max":
        return max(values)
    if method == "median":
        return statistics.median(values)
    return statistics.fmean(values)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def corrected_setpoint(
    target: float,
    measured: float,
    device_reading: float | None,
    max_delta: float,
    device_min: float | None = None,
    device_max: float | None = None,
) -> int:
    """Target for a device that regulates by its own sensor.

    The device sees ``device_reading`` where we see ``measured``. To make it
    stop exactly when *our* sensors reach ``target`` it is asked for
    ``target + (device_reading − measured)``. The correction is limited to
    ``±max_delta`` and the result to the device's own range, then rounded to a
    whole percent (humidifier.set_humidity takes an int).
    """
    offset = 0.0 if device_reading is None else device_reading - measured
    value = target + clamp(offset, -max_delta, max_delta)
    if device_min is not None:
        value = max(value, device_min)
    if device_max is not None:
        value = min(value, device_max)
    return int(round(value))


class Hysteresis:
    """On/off decision with a dead band and minimum on/off times.

    Starts at ``target − hysteresis`` and stops at ``target``. A transition
    is held back until the current state has lasted ``min_on`` / ``min_off``
    seconds (counted from our own last transition — the first one is free).
    """

    def __init__(self, min_on_s: float, min_off_s: float, running: bool = False) -> None:
        self.min_on_s = min_on_s
        self.min_off_s = min_off_s
        self.running = running
        self._since: float | None = None

    def update(self, measured: float, target: float, hysteresis: float, now: float) -> bool:
        if self.running:
            want = measured < target
        else:
            want = measured <= target - hysteresis
        if want != self.running:
            if self._since is not None:
                hold = self.min_on_s if self.running else self.min_off_s
                if now - self._since < hold:
                    return self.running
            self.running = want
            self._since = now
        return self.running

    def force(self, running: bool, now: float) -> None:
        """Set the state from outside (controller off, failsafe)."""
        if running != self.running:
            self.running = running
            self._since = now


class SpeedRegulator:
    """PI from humidity deficit to fan speed (%).

    ``kp`` — %fan per %RH of deficit; ``ki`` — %fan per (%RH·minute). The
    output is ``speed_min + kp·e + I`` clamped to ``[speed_min, speed_max]``;
    the integral is clamped to the same span (anti-windup), so a long dry
    spell cannot keep the fan at full speed after the room is satisfied.
    """

    def __init__(self, kp: float, ki: float, speed_min: float, speed_max: float) -> None:
        self.kp = kp
        self.ki = ki
        self.speed_min = speed_min
        self.speed_max = max(speed_min, speed_max)
        self.integral = 0.0

    def reset(self) -> None:
        self.integral = 0.0

    def update(self, measured: float, target: float, dt_s: float) -> float:
        error = target - measured
        span = self.speed_max - self.speed_min
        self.integral = clamp(self.integral + self.ki * error * dt_s / 60.0, 0.0, span)
        return clamp(self.speed_min + self.kp * error + self.integral, self.speed_min, self.speed_max)


def quantize(value: float, step: float | None, low: float | None = None, high: float | None = None) -> float:
    """Round to the entity's step and keep inside its range."""
    if step and step > 0:
        value = round(value / step) * step
    if low is not None:
        value = max(value, low)
    if high is not None:
        value = min(value, high)
    return round(value, 3)


def speed_changed(new: float, current: float | None, low: float, high: float, min_change: float) -> bool:
    """Is ``new`` worth sending over ``current``?"""
    if current is None:
        return True
    if abs(new - current) < 1e-6:
        return False
    if new in (low, high):
        return True
    return abs(new - current) >= min_change
