from __future__ import annotations

from typing import Any

import httpx

from gogo.clock import from_unixtime
from gogo.ingest.protocol import GridHour
from gogo.models import Spot

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

# The timezone still decides where day boundaries fall for `forecast_days` and for the
# archive's `start_date`/`end_date`; unixtime only changes how the instants come back on
# the wire, unambiguously.
TIMEZONE = "Europe/Lisbon"
TIMEFORMAT = "unixtime"
SOURCE = "open-meteo"

# The six after the first line are stored and not scored, deliberately. They were added
# because a forecast is the one thing the archive cannot give back later: what the ocean
# did is recoverable, what we believed it would do is not. Measured over 2688 spot-hours
# of live forecast before adding them:
#
#   swell_wave_peak_period — a median 1.5 s above the mean period the score gates on,
#     which is the number surfers actually quote. Feeding it instead would lift a hard
#     veto on 13.8% of hours, so the gate may be reading a different quantity from the
#     one its thresholds were written in. S12/S13 territory, and the harness decides.
#     Note its horizon: measured at ~69 h, where every other variable here runs the
#     full ~145 h. A score that gated on it would veto everything past day three, so
#     any future use needs a fallback to the mean period at long lead. Storing it
#     records where that boundary was on the day, which an as-of policy has to know.
#   wave_height, wave_period — the combined sea. The size gate reads the swell partition
#     alone and disagrees with the whole sea on 14% of hours, every single time in the
#     same direction: swell under the spot's minimum while the real sea is in range. A
#     one-directional over-veto on small days is exactly the censoring the plan warns of.
#   secondary_swell_* — a genuinely separate second train only 2.5% of the time (the
#     rest is one swell split by the partitioner). Kept as insurance for S14 because
#     those hours are unrecoverable, not because the case is strong.
MARINE_HOURLY = (
    "swell_wave_height,swell_wave_direction,swell_wave_period,"
    "wind_wave_height,sea_level_height_msl,sea_surface_temperature,"
    "swell_wave_peak_period,wave_height,wave_period,"
    "secondary_swell_wave_height,secondary_swell_wave_direction,"
    "secondary_swell_wave_period"
)
WEATHER_HOURLY = "wind_speed_10m,wind_direction_10m,wind_gusts_10m"


def as_list(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One spot comes back as an object, several as an array."""
    return payload if isinstance(payload, list) else [payload]


def require_one_per_spot(
    marine: list[dict[str, Any]], weather: list[dict[str, Any]], spots: list[Spot]
) -> None:
    if len(marine) != len(spots) or len(weather) != len(spots):
        raise RuntimeError(
            f"Open-Meteo returned {len(marine)} marine / {len(weather)} weather "
            f"for {len(spots)} spots"
        )


def merge_grid_hours(
    spot: Spot,
    marine: dict[str, Any],
    weather: dict[str, Any],
    source: str = SOURCE,
) -> list[GridHour]:
    """Marine hours joined to wind by instant, keyed to the marine cell.

    Shared by the live and archive paths on purpose: the two differ only in which
    endpoint filled these dicts, and a field mapping that drifted between them would
    make analysis rows silently incomparable to the forecast rows they exist to be
    compared against.
    """
    mh, wh = marine["hourly"], weather["hourly"]
    times = [from_unixtime(t) for t in mh["time"]]
    wind_at = {from_unixtime(t): i for i, t in enumerate(wh["time"])}

    out: list[GridHour] = []
    for i, valid_at in enumerate(times):
        wi = wind_at.get(valid_at)
        if wi is None:
            continue

        # Every field the score gates on. A null here is a hole in the feed, not a
        # zero, and the two are not interchangeable: 0 kn aligned with a spot's
        # offshore bearing scores full marks, so coercing a missing wind reading
        # would turn a gap into the most flattering hour of the day, while a
        # coerced 0 s period vetoes the spot outright. Dropping the hour instead
        # ends the run, which is already what a gap in the data means.
        gates = (
            mh["swell_wave_height"][i],
            mh["swell_wave_direction"][i],
            mh["swell_wave_period"][i],
            wh["wind_speed_10m"][wi],
            wh["wind_direction_10m"][wi],
        )
        if any(value is None for value in gates):
            continue
        hs, swell_from, period, wind_kn, wind_from = gates

        # Everything below is optional. A null stays a null rather than dropping the
        # hour or becoming a zero: nothing gates on these, so an absent reading is
        # simply an absent reading and the hour is still fully scorable without it.
        def optional(series: str) -> float | None:
            column = mh.get(series)
            return column[i] if column else None

        out.append(
            GridHour(
                requested_lat=spot.lat,
                requested_lon=spot.lon,
                grid_lat=marine["latitude"],
                grid_lon=marine["longitude"],
                valid_at=valid_at,
                swell_height_m=hs,
                swell_from_deg=swell_from,
                swell_period_s=period,
                wind_wave_height_m=mh["wind_wave_height"][i] or 0.0,
                wind_speed_kn=wind_kn,
                wind_from_deg=wind_from,
                wind_gusts_kn=wh["wind_gusts_10m"][wi] or 0.0,
                sea_level_m=mh["sea_level_height_msl"][i],
                sea_surface_temp_c=optional("sea_surface_temperature"),
                swell_peak_period_s=optional("swell_wave_peak_period"),
                combined_height_m=optional("wave_height"),
                combined_period_s=optional("wave_period"),
                swell2_height_m=optional("secondary_swell_wave_height"),
                swell2_from_deg=optional("secondary_swell_wave_direction"),
                swell2_period_s=optional("secondary_swell_wave_period"),
                source=source,
            )
        )
    return out


class OpenMeteoSource:
    """Marine (sea cell, best_match) + weather (land cell, knots)."""

    def __init__(self, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(self, spots: list[Spot], forecast_days: int = 7) -> list[GridHour]:
        if not spots:
            return []
        lats = ",".join(str(s.lat) for s in spots)
        lons = ",".join(str(s.lon) for s in spots)

        marine = as_list(
            self._client.get(
                MARINE_URL,
                params={
                    "latitude": lats,
                    "longitude": lons,
                    "hourly": MARINE_HOURLY,
                    "timezone": TIMEZONE,
                    "timeformat": TIMEFORMAT,
                    "forecast_days": forecast_days,
                    "cell_selection": "sea",
                },
            ).raise_for_status().json()
        )
        weather = as_list(
            self._client.get(
                WEATHER_URL,
                params={
                    "latitude": lats,
                    "longitude": lons,
                    "hourly": WEATHER_HOURLY,
                    "timezone": TIMEZONE,
                    "timeformat": TIMEFORMAT,
                    "forecast_days": forecast_days,
                    "wind_speed_unit": "kn",
                    "cell_selection": "land",
                },
            ).raise_for_status().json()
        )
        require_one_per_spot(marine, weather, spots)

        hours: list[GridHour] = []
        for spot, m, w in zip(spots, marine, weather, strict=True):
            hours.extend(merge_grid_hours(spot, m, w))
        return hours
