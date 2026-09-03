"""Open-Meteo's reanalysis, for hours that have already happened.

This exists so the first hundred labels do not have to be collected in real time. A
session surfed last March can be given the same features a live one gets, which is the
difference between the Stage 2 harness arriving in weeks and arriving in months.

Two archives, both ERA5-family. The marine archive answers on the *same* grid cells as
the marine forecast — probed across three spots, the cell a spot resolves to is
identical either way — so analysis rows join to forecast rows on
`(grid_lat, grid_lon, valid_at)` with no extra mapping. Wind comes from the weather
archive on its own land cells and is merged by instant, exactly as the live path does.

None of it is a forecast. It is what happened, not what anyone believed would happen, so
it is written with `is_analysis` set and `persist_analysis_hours` keeps it out of
`forecast_current`. Scoring it answers "is the score's judgement right about real
conditions", never "would we have made the right call at the time".

Coverage, probed September 2026 at 38.9989/-9.4189:

- swell height, period, direction and wind: complete back to at least 2022
- tide (`sea_level_height_msl`): only from late 2022 — absent for 2022-09, present for
  2023-01. Earlier hours still load, they simply carry no tide phase, because
  `attach_tide` skips a null level rather than guessing one
- there is no hole at the recent end: a range ending today came back complete. What the
  last few days lack is finality, not data — they get revised afterwards, which is why
  `persist_analysis_hours` updates an existing hour instead of skipping it
"""

from __future__ import annotations

from datetime import date

import httpx

from gogo.ingest.openmeteo import (
    MARINE_HOURLY,
    MARINE_URL,
    TIMEFORMAT,
    TIMEZONE,
    WEATHER_HOURLY,
    as_list,
    merge_grid_hours,
    require_one_per_spot,
)
from gogo.ingest.protocol import GridHour
from gogo.models import Spot

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE = "archive-era5"

#: Earliest day with tide in the marine archive. Measured, not documented — 2022-09-01
#: returns nulls for `sea_level_height_msl` and 2023-01-01 returns a full series.
TIDE_FROM = date(2023, 1, 1)

#: How close to today an hour has to be to still be provisional. Probing returned a
#: complete series right up to today, so this bounds *revision*, not availability:
#: backfill these days again later and the rows will be corrected in place.
PROVISIONAL_DAYS = 5


class ArchiveSource:
    """Reanalysis for a closed date range. Not a `ForecastSource` — it needs dates."""

    def __init__(self, client: httpx.Client | None = None, timeout: float = 60.0) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(self, spots: list[Spot], start: date, end: date) -> list[GridHour]:
        """Hourly reanalysis for `start`..`end` inclusive, as Lisbon days."""
        if not spots:
            return []
        if end < start:
            raise ValueError(f"range runs backwards: {start} .. {end}")

        window = {
            "latitude": ",".join(str(s.lat) for s in spots),
            "longitude": ",".join(str(s.lon) for s in spots),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": TIMEZONE,
            "timeformat": TIMEFORMAT,
        }

        marine = as_list(
            self._client.get(
                MARINE_URL,
                params={**window, "hourly": MARINE_HOURLY, "cell_selection": "sea"},
            ).raise_for_status().json()
        )
        weather = as_list(
            self._client.get(
                ARCHIVE_URL,
                params={**window, "hourly": WEATHER_HOURLY, "wind_speed_unit": "kn"},
            ).raise_for_status().json()
        )
        require_one_per_spot(marine, weather, spots)

        hours: list[GridHour] = []
        for spot, m, w in zip(spots, marine, weather, strict=True):
            hours.extend(merge_grid_hours(spot, m, w, source=SOURCE))
        return hours
