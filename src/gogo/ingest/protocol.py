from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel

from gogo.clock import UtcDatetime
from gogo.models import Spot


class GridHour(BaseModel):
    """One hour at the *model* grid cell Open-Meteo actually used."""

    requested_lat: float
    requested_lon: float
    grid_lat: float
    grid_lon: float
    valid_at: UtcDatetime
    swell_height_m: float
    swell_from_deg: float
    swell_period_s: float
    wind_wave_height_m: float
    wind_speed_kn: float
    wind_from_deg: float
    wind_gusts_kn: float
    sea_level_m: float | None
    sea_surface_temp_c: float | None = None
    source: str = "open-meteo"


class ForecastSource(Protocol):
    def fetch(self, spots: list[Spot], forecast_days: int = 7) -> list[GridHour]:
        """Return hourly snapshots. Implementations must batch unique cells."""
        ...


class AnalysisSource(Protocol):
    """A reanalysis of hours that already happened — a different thing to a forecast.

    Separate from `ForecastSource` because the signature is the honest difference: a
    forecast runs forward from now and takes a horizon, an analysis takes a closed past
    range. Anything satisfying this must never be served as a prediction.
    """

    def fetch(self, spots: list[Spot], start: date, end: date) -> list[GridHour]:
        ...
