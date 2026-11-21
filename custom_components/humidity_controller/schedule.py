"""Time-of-day humidity schedule for humidity_controller presets.

Pure stdlib helpers — no Home Assistant imports, no package-relative imports,
no I/O, so the tests can load the module on its own. The storage format is a
list of structured points, e.g.
``[{"time": "22:00", "humidity": 50.0}, {"time": "07:00", "humidity": 45.0}]``.
"""
from __future__ import annotations

import datetime

VALUE_KEY = "humidity"


def validate_time(raw) -> str | None:
    """Canonical ``HH:MM`` for a valid time (``HH:MM`` / ``HH:MM:SS`` /
    :class:`datetime.time`), ``None`` otherwise."""
    if raw is None:
        return None
    try:
        if isinstance(raw, datetime.time):
            t = raw
        else:
            t = datetime.time.fromisoformat(str(raw).strip())
    except (ValueError, TypeError):
        return None
    return t.strftime("%H:%M")


def coerce_points(raw) -> list[dict]:
    """Stored points as a clean, time-sorted list of ``{"time", "humidity"}``
    dicts; malformed entries and duplicate times (last wins) are dropped."""
    if not isinstance(raw, list):
        return []
    by_time: dict[str, float] = {}
    for p in raw:
        if not isinstance(p, dict):
            continue
        t = validate_time(p.get("time"))
        if t is None:
            continue
        try:
            v = float(p.get(VALUE_KEY))
        except (TypeError, ValueError):
            continue
        by_time[t] = v
    return [{"time": t, VALUE_KEY: by_time[t]} for t in sorted(by_time)]


def normalize_points(points) -> list[tuple[datetime.time, float]]:
    """Stored points → runtime ``[(time, value), ...]``.

    Accepts dicts with ``time`` / ``humidity`` keys or ``(time, value)``
    sequences. Entries that fail to parse are skipped.
    """
    if not points or not isinstance(points, list):
        return []
    out: list[tuple[datetime.time, float]] = []
    for p in points:
        if isinstance(p, dict):
            t_raw, v_raw = p.get("time"), p.get(VALUE_KEY)
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            t_raw, v_raw = p[0], p[1]
        else:
            continue
        if t_raw is None or v_raw is None:
            continue
        try:
            t = t_raw if isinstance(t_raw, datetime.time) else datetime.time.fromisoformat(str(t_raw))
            v = float(v_raw)
        except (ValueError, TypeError):
            continue
        out.append((t, v))
    return out


def _to_seconds(t: datetime.time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def target_at(schedule, now: datetime.time) -> float | None:
    """Interpolated value for ``now``.

    Empty schedule → ``None``; one point → its value; otherwise linear
    interpolation between adjacent points, cyclic over 24 hours.
    """
    pts = normalize_points(schedule)
    if not pts:
        return None
    if len(pts) == 1:
        return float(pts[0][1])

    pts = sorted(pts, key=lambda p: _to_seconds(p[0]))
    secs = [_to_seconds(t) for t, _ in pts]
    vals = [float(v) for _, v in pts]
    now_s = _to_seconds(now)

    if now_s == secs[-1]:
        return vals[-1]
    for i in range(len(pts) - 1):
        if secs[i] <= now_s < secs[i + 1]:
            span = secs[i + 1] - secs[i]
            frac = (now_s - secs[i]) / span if span else 0.0
            return vals[i] + (vals[i + 1] - vals[i]) * frac

    # Wrap-around: last point → first point through midnight.
    span = (86400 - secs[-1]) + secs[0]
    elapsed = now_s - secs[-1] if now_s >= secs[-1] else (86400 - secs[-1]) + now_s
    frac = elapsed / span if span else 0.0
    return vals[-1] + (vals[0] - vals[-1]) * frac
