from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from gogo.ingest.protocol import GridHour
from gogo.models import Spot

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
TIMEZONE = "Europe/Lisbon"

_MARINE_HOURLY = (
    "swell_wave_height,swell_wave_direction,swell_wave_period,"
    "wind_wave_height,sea_level_height_msl,sea_surface_temperature"
)
_WEATHER_HOURLY = "wind_speed_10m,wind_direction_10m,wind_gusts_10m"


def _as_list(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return payload if isinstance(payload, list) else [payload]


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

        marine = _as_list(
            self._client.get(
                MARINE_URL,
                params={
                    "latitude": lats,
                    "longitude": lons,
                    "hourly": _MARINE_HOURLY,
                    "timezone": TIMEZONE,
                    "forecast_days": forecast_days,
                    "cell_selection": "sea",
                },
            ).raise_for_status().json()
        )
        weather = _as_list(
            self._client.get(
                WEATHER_URL,
                params={
                    "latitude": lats,
                    "longitude": lons,
                    "hourly": _WEATHER_HOURLY,
                    "timezone": TIMEZONE,
                    "forecast_days": forecast_days,
                    "wind_speed_unit": "kn",
                    "cell_selection": "land",
                },
            ).raise_for_status().json()
        )
        if len(marine) != len(spots) or len(weather) != len(spots):
            raise RuntimeError(
                f"Open-Meteo returned {len(marine)} marine / {len(weather)} weather "
                f"for {len(spots)} spots"
            )

        hours: list[GridHour] = []
        for spot, m, w in zip(spots, marine, weather, strict=True):
            hours.extend(self._merge(spot, m, w))
        return hours

    def _merge(self, spot: Spot, marine: dict[str, Any], weather: dict[str, Any]) -> list[GridHour]:
        mh, wh = marine["hourly"], weather["hourly"]
        times = [datetime.fromisoformat(t) for t in mh["time"]]
        wind_at = {datetime.fromisoformat(t): i for i, t in enumerate(wh["time"])}

        out: list[GridHour] = []
        for i, valid_at in enumerate(times):
            hs = mh["swell_wave_height"][i]
            if hs is None:
                continue
            wi = wind_at.get(valid_at)
            if wi is None:
                continue
            sst = (mh.get("sea_surface_temperature") or [None] * len(times))[i]
            out.append(
                GridHour(
                    requested_lat=spot.lat,
                    requested_lon=spot.lon,
                    grid_lat=marine["latitude"],
                    grid_lon=marine["longitude"],
                    valid_at=valid_at,
                    swell_height_m=hs,
                    swell_from_deg=mh["swell_wave_direction"][i] or 0.0,
                    swell_period_s=mh["swell_wave_period"][i] or 0.0,
                    wind_wave_height_m=mh["wind_wave_height"][i] or 0.0,
                    wind_speed_kn=wh["wind_speed_10m"][wi] or 0.0,
                    wind_from_deg=wh["wind_direction_10m"][wi] or 0.0,
                    wind_gusts_kn=wh["wind_gusts_10m"][wi] or 0.0,
                    sea_level_m=mh["sea_level_height_msl"][i],
                    sea_surface_temp_c=sst,
                )
            )
        return out
