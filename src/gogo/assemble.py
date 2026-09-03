from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from gogo.clock import to_local
from gogo.ingest.protocol import GridHour
from gogo.models import HourForecast, Spot, WindowScore
from gogo.score import score_window
from gogo.tide import attach_tide


def forecasts_from_grid(hours: list[GridHour]) -> list[HourForecast]:
    """Attach tide phase using the sea-level series of each marine grid cell."""
    by_cell: dict[tuple[float, float], list[GridHour]] = defaultdict(list)
    for h in hours:
        by_cell[(h.grid_lat, h.grid_lon)].append(h)

    out: list[HourForecast] = []
    for group in by_cell.values():
        group = sorted(group, key=lambda g: g.valid_at)
        tides = attach_tide(
            [g.valid_at for g in group],
            [g.sea_level_m for g in group],
        )
        for g in group:
            phase = tides.get(g.valid_at)
            out.append(
                HourForecast(
                    valid_at=g.valid_at,
                    swell_height_m=g.swell_height_m,
                    swell_from_deg=g.swell_from_deg,
                    swell_period_s=g.swell_period_s,
                    wind_wave_height_m=g.wind_wave_height_m,
                    wind_speed_kn=g.wind_speed_kn,
                    wind_from_deg=g.wind_from_deg,
                    wind_gusts_kn=g.wind_gusts_kn,
                    sea_level_m=g.sea_level_m,
                    tide=phase[0] if phase else None,
                    tide_trend=phase[1] if phase else None,
                )
            )
    return out


def saturday_morning(
    hours: list[GridHour], not_before: datetime | None = None
) -> datetime | None:
    """First Saturday 08:00 in the series, else the first 08:00, else first hour.

    The returned instant is UTC; the weekday and hour are read in Europe/Lisbon,
    because "Saturday 08:00" is what a person means, not what UTC says.

    `not_before` drops hours that have already happened. Serving paths pass `now`;
    `forecast_current` keeps every hour ever fetched, so without it a Saturday that has
    been and gone can outrank the one being asked about. Fixtures pass nothing, because
    a golden replay is deliberately historical.
    """
    if not hours:
        return None
    candidates = sorted({h.valid_at for h in hours})
    if not_before is not None:
        candidates = [t for t in candidates if t >= not_before]
    if not candidates:
        return None
    local = [(t, to_local(t)) for t in candidates]
    saturdays = [t for t, lt in local if lt.weekday() == 5 and lt.hour == 8]
    if saturdays:
        return saturdays[0]
    eights = [t for t, lt in local if lt.hour == 8]
    return eights[0] if eights else candidates[0]


def nearest_hour(forecasts: list[HourForecast], when: datetime) -> HourForecast | None:
    if not forecasts:
        return None
    return min(forecasts, key=lambda h: abs((h.valid_at - when).total_seconds()))


def score_spots_at(
    spots: list[Spot],
    hours: list[GridHour],
    when: datetime,
) -> list[WindowScore]:
    """Each spot uses the grid series fetched for its own lat/lon."""
    by_request: dict[tuple[float, float], list[GridHour]] = defaultdict(list)
    for h in hours:
        by_request[(h.requested_lat, h.requested_lon)].append(h)

    ranked: list[WindowScore] = []
    for spot in spots:
        series = forecasts_from_grid(by_request.get((spot.lat, spot.lon), []))
        hour = nearest_hour(series, when)
        if hour is None:
            continue
        ranked.append(score_window(spot, hour))
    ranked.sort(key=lambda w: (-w.score, w.spot_name))
    return ranked
