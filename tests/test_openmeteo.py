from datetime import datetime

import httpx

from gogo.clock import UTC
from gogo.ingest.openmeteo import OpenMeteoSource
from gogo.models import Spot

# Saturday 29 Aug 2026, 08:00 in Lisbon (WEST, UTC+1) is 07:00Z.
_SATURDAY_0800_LISBON = datetime(2026, 8, 29, 7, 0, tzinfo=UTC)
_UNIXTIME = int(_SATURDAY_0800_LISBON.timestamp())

_SPOT = Spot(
    id="ribeira",
    name="Ribeira d'Ilhas",
    lat=38.988,
    lon=-9.419,
    region="ericeira",
    swell_from_min=250,
    swell_from_max=330,
    size_min_m=0.8,
    size_max_m=3.2,
    period_min_s=8,
    offshore_from=80,
    max_onshore_kn=12,
    tides=["mid", "high"],
    skill_min="intermediate",
    skill_max="advanced",
    crowd="high",
)


def test_fetch_merges_marine_and_weather(monkeypatch):
    marine = {
        "latitude": 38.958,
        "longitude": -9.458,
        "hourly": {
            "time": [_UNIXTIME],
            "swell_wave_height": [1.3],
            "swell_wave_direction": [290],
            "swell_wave_period": [11.0],
            "wind_wave_height": [0.2],
            "sea_level_height_msl": [0.4],
            "sea_surface_temperature": [19.2],
        },
    }
    weather = {
        "latitude": 39.0,
        "longitude": -9.375,
        "hourly": {
            "time": [_UNIXTIME],
            "wind_speed_10m": [8.5],
            "wind_direction_10m": [70],
            "wind_gusts_10m": [14.0],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "timeformat=unixtime" in str(request.url)
        if "marine" in str(request.url):
            assert "cell_selection=sea" in str(request.url)
            return httpx.Response(200, json=marine)
        assert "wind_speed_unit=kn" in str(request.url)
        return httpx.Response(200, json=weather)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = OpenMeteoSource(client=client)
    hours = src.fetch([_SPOT], forecast_days=7)
    assert len(hours) == 1
    h = hours[0]
    assert h.grid_lat == 38.958
    assert h.swell_height_m == 1.3
    assert h.swell_period_s == 11.0
    assert h.wind_speed_kn == 8.5
    assert h.valid_at == _SATURDAY_0800_LISBON
    assert h.valid_at.tzinfo is not None
