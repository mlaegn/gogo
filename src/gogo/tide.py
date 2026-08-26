"""Derive low/mid/high and incoming/outgoing from an hourly sea-level series."""

from __future__ import annotations

from datetime import datetime

from gogo.models import TidePhase, TideTrend


def classify_levels(levels: list[float]) -> list[tuple[TidePhase, TideTrend]]:
    """Classify each hour relative to the series min/max (a few days is fine)."""
    if not levels:
        return []
    lo, hi = min(levels), max(levels)
    span = hi - lo
    if span < 0.15:
        return [("mid", "slack") for _ in levels]

    out: list[tuple[TidePhase, TideTrend]] = []
    n = len(levels)
    for i, z in enumerate(levels):
        frac = (z - lo) / span
        if frac < 0.33:
            phase: TidePhase = "low"
        elif frac < 0.67:
            phase = "mid"
        else:
            phase = "high"

        prev = levels[i - 1] if i else z
        nxt = levels[i + 1] if i + 1 < n else z
        rising = nxt - prev
        if abs(rising) < 0.05:
            trend: TideTrend = "slack"
        elif rising > 0:
            trend = "incoming"
        else:
            trend = "outgoing"
        out.append((phase, trend))
    return out


def attach_tide(
    times: list[datetime],
    levels: list[float | None],
) -> dict[datetime, tuple[TidePhase, TideTrend]]:
    known_t = [t for t, z in zip(times, levels, strict=True) if z is not None]
    known_z = [z for z in levels if z is not None]
    classified = classify_levels(known_z)
    return {t: c for t, c in zip(known_t, classified, strict=True)}
