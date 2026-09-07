"""Shared builders. One definition of a plausible grid-hour, so forecast and analysis
tests cannot disagree about what a row looks like."""

from datetime import date, datetime

from gogo.clock import UTC, from_local_input
from gogo.ingest.protocol import GridHour
from gogo.models import Spot

RIBEIRA_CELL = (38.958, -9.458)


def grid_hour(**kwargs) -> GridHour:
    base = dict(
        requested_lat=38.988,
        requested_lon=-9.419,
        grid_lat=RIBEIRA_CELL[0],
        grid_lon=RIBEIRA_CELL[1],
        valid_at=datetime(2026, 8, 29, 7, 0, tzinfo=UTC),
        swell_height_m=1.3,
        swell_from_deg=290,
        swell_period_s=11.0,
        wind_wave_height_m=0.2,
        wind_speed_kn=8.0,
        wind_from_deg=70,
        wind_gusts_kn=12.0,
        sea_level_m=0.4,
        sea_surface_temp_c=19.0,
    )
    base.update(kwargs)
    return GridHour.model_validate(base)


def grid_day(
    day: date,
    spots: list[Spot],
    from_hour: int = 7,
    until_hour: int = 11,
    **overrides,
) -> list[GridHour]:
    """A local day of identical hours for every spot, so windows come out of the real
    path rather than being constructed by hand.

    Each spot gets its own cell, which keeps the tide series per spot independent. The
    sea level is constant, so every hour classifies as mid tide — a flat input makes the
    run-grouping the only thing a test is measuring.
    """
    return [
        grid_hour(
            requested_lat=spot.lat,
            requested_lon=spot.lon,
            grid_lat=spot.lat,
            grid_lon=spot.lon,
            valid_at=from_local_input(day, f"{hour:02d}:00"),
            **overrides,
        )
        for spot in spots
        for hour in range(from_hour, until_hour)
    ]
