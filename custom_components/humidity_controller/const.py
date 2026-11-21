"""Constants for the humidity controller integration."""

DOMAIN = "humidity_controller"

DEFAULT_NAME = "Humidity Controller"

# How often the loop is evaluated even when no sensor changes. Lets the
# integral term of the speed regulator advance during quiet sensor periods and
# re-asserts device state that somebody changed by hand.
TICK_INTERVAL_SECONDS = 30

# Target humidity range of the controller entity itself (%).
MIN_HUMIDITY = 20
MAX_HUMIDITY = 90
DEFAULT_TARGET_HUMIDITY = 45.0

# --- Multi-sensor aggregation --------------------------------------------
#   mean   — the room on average
#   min    — humidify until the driest spot is satisfied
#   max    — stop as soon as the dampest spot is satisfied (no condensation)
#   median — like mean, but a single misplaced sensor cannot skew the loop
AGGREGATION_MEAN = "mean"
AGGREGATION_MIN = "min"
AGGREGATION_MAX = "max"
AGGREGATION_MEDIAN = "median"
HUMIDITY_AGGREGATIONS = (
    AGGREGATION_MEAN,
    AGGREGATION_MIN,
    AGGREGATION_MAX,
    AGGREGATION_MEDIAN,
)
DEFAULT_AGGREGATION = AGGREGATION_MEAN

# --- Control methods per device -------------------------------------------
# setpoint — humidifier.* stays in its own ``auto`` mode and is handed the
#            target, shifted by (device's own reading − our reading), so it
#            regulates by itself but against *our* sensors.
# speed    — humidifier.* in ``manual``; the fan speed (number.*) follows the
#            humidity deficit through a PI regulator, the device is switched
#            off when the target is reached.
# onoff    — plain on/off by hysteresis with minimum on/off times. The only
#            method for switch / input_boolean, and the fallback for a
#            humidifier.* that lacks the mode / speed entity its method needs.
METHOD_SETPOINT = "setpoint"
METHOD_SPEED = "speed"
METHOD_ONOFF = "onoff"
HUMIDIFIER_METHODS = (METHOD_SETPOINT, METHOD_SPEED, METHOD_ONOFF)
DEFAULT_METHOD = METHOD_SETPOINT

# Humidifier modes the methods rely on (airmx / Tion Iris naming).
DEVICE_MODE_AUTO = "auto"
DEVICE_MODE_MANUAL = "manual"

# Ceiling on the setpoint correction: the value sent to the device never
# differs from the controller target by more than this many %RH. Keeps a
# device whose own sensor sits in a damp corner from being pushed to 90 %.
DEFAULT_MAX_DELTA = 10.0

# A setpoint device is re-sent its target at most this often — the device's
# own reading jitters by ±1 % and every jitter would otherwise be a command.
SETPOINT_MIN_INTERVAL_SECONDS = 60

# Speed regulator defaults: ``kp`` is %fan per %RH of deficit, ``ki`` is
# %fan per (%RH × minute).
DEFAULT_SPEED_MIN = 20.0
DEFAULT_SPEED_MAX = 100.0
DEFAULT_KP = 10.0
DEFAULT_KI = 0.5
# A new fan speed is sent only when it moved at least this much (or hit a
# bound), so the fan doesn't step every tick.
SPEED_MIN_CHANGE = 5.0

# --- Hysteresis (speed and onoff) -----------------------------------------
# Start humidifying at ``target − hysteresis``, stop at ``target``.
DEFAULT_HYSTERESIS = 3.0
DEFAULT_MIN_ON_MINUTES = 5.0
DEFAULT_MIN_OFF_MINUTES = 5.0

# --- Failsafe -------------------------------------------------------------
# When no sensor has delivered a usable reading for this long the devices go
# to a safe state instead of acting on a stale value.
DEFAULT_SENSOR_TIMEOUT_MINUTES = 15.0
FAILSAFE_OFF = "off"
FAILSAFE_DEVICE_AUTO = "device_auto"
FAILSAFE_ACTIONS = (FAILSAFE_OFF, FAILSAFE_DEVICE_AUTO)
DEFAULT_FAILSAFE_ACTION = FAILSAFE_DEVICE_AUTO

# --- Presets ----------------------------------------------------------------
# "none" = no preset: keep whatever target was set by hand, no schedule.
PRESET_NONE = "none"
DEFAULT_PRESETS = {"normal": 45.0, "sleep": 50.0, "away": 40.0}
DEFAULT_PRESET_ORDER = ["normal", "sleep", "away"]
