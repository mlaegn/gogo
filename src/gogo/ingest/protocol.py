from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel

from gogo.clock import UtcDatetime
from gogo.models import Spot


class GridHour(BaseModel):
    """One hour at the *model* grid cell Open-Meteo actually used.

    The fields the score reads are required. The rest are optional and default to None,
    which is what lets a payload written before a field existed re-hydrate unchanged —
    there is no migration for adding one, because the stored form is JSONB.
    """

    requested_lat: float
    requested_lon: float
    grid_lat: float
    grid_lon: float
    valid_at: UtcDatetime

    # Scored today.
    swell_height_m: float
    swell_from_deg: float
    swell_period_s: float
    wind_wave_height_m: float
    wind_speed_kn: float
    wind_from_deg: float
    wind_gusts_kn: float
    sea_level_m: float | None

    # Stored, not scored. Kept because a forecast cannot be recovered afterwards: the
    # archive can say what the ocean did, never what we thought it would do. Each one
    # answers a question Stage 3 will ask and cannot ask without the history.
    sea_surface_temp_c: float | None = None
    #: Peak period. Higher than `swell_period_s` (the mean) by a median 1.5 s here, and
    #: it is the number a surf forecast means by "11 seconds". Only about the first
    #: three days of the horizon carry it, so None here often means "too far out"
    #: rather than "missing" — do not read an absence as a short-period sea.
    swell_peak_period_s: float | None = None
    #: The combined sea, swell and wind wave together — what you see from the cliff.
    combined_height_m: float | None = None
    combined_period_s: float | None = None
    #: The second swell train, where there is one.
    swell2_height_m: float | None = None
    swell2_from_deg: float | None = None
    swell2_period_s: float | None = None

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
