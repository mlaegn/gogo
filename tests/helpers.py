"""Shared builders. One definition of a plausible grid-hour, so forecast and analysis
tests cannot disagree about what a row looks like."""

from datetime import datetime

from gogo.clock import UTC
from gogo.ingest.protocol import GridHour

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
