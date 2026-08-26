"""Direction helpers. All angles are meteorological 'from' degrees, 0–360."""

from __future__ import annotations


def wrap360(deg: float) -> float:
    return deg % 360


def angle_delta(a: float, b: float) -> float:
    """Smallest signed difference a - b in (-180, 180]."""
    d = wrap360(a - b)
    if d > 180:
        d -= 360
    return d


def angle_distance(a: float, b: float) -> float:
    return abs(angle_delta(a, b))


def in_bearing_window(deg: float, start: float, end: float) -> bool:
    """True if deg lies on the clockwise arc from start to end (inclusive)."""
    deg = wrap360(deg)
    start = wrap360(start)
    end = wrap360(end)
    if start <= end:
        return start <= deg <= end
    return deg >= start or deg <= end


def window_center(start: float, end: float) -> float:
    start = wrap360(start)
    end = wrap360(end)
    if start <= end:
        return wrap360((start + end) / 2)
    return wrap360((start + end + 360) / 2)
