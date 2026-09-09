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


def _one_hour_response(wind: dict, marine_extra: dict | None = None):
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
    marine["hourly"].update(marine_extra or {})
    weather = {
        "latitude": 39.0,
        "longitude": -9.375,
        "hourly": {"time": [_UNIXTIME], **wind},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = marine if "marine" in str(request.url) else weather
        return httpx.Response(200, json=payload)

    return OpenMeteoSource(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_a_missing_wind_reading_drops_the_hour_rather_than_calling_it_calm():
    """0 kn is not the same claim as "we do not know".

    A null coerced to zero lands on the spot's offshore bearing and scores the wind
    term full marks, so a hole in the feed would come back as the glassiest hour of
    the day. Dropping the hour ends the run instead, which is what a gap already means.
    """
    src = _one_hour_response(
        {"wind_speed_10m": [None], "wind_direction_10m": [70], "wind_gusts_10m": [14.0]}
    )
    assert src.fetch([_SPOT], forecast_days=7) == []


def test_a_missing_swell_period_drops_the_hour_rather_than_vetoing_the_spot():
    """The mirror image: a coerced 0 s period is short enough to veto every spot."""
    src = _one_hour_response(
        {"wind_speed_10m": [8.5], "wind_direction_10m": [70], "wind_gusts_10m": [14.0]},
        marine_extra={"swell_wave_period": [None]},
    )
    assert src.fetch([_SPOT], forecast_days=7) == []


def test_a_real_zero_is_kept():
    """Only None is a hole. Calm wind out of due north is a reading, not a gap."""
    src = _one_hour_response(
        {"wind_speed_10m": [0.0], "wind_direction_10m": [0], "wind_gusts_10m": [None]}
    )
    hours = src.fetch([_SPOT], forecast_days=7)
    assert len(hours) == 1
    assert hours[0].wind_speed_kn == 0.0
    assert hours[0].wind_from_deg == 0.0
    # Gusts are not gated on, so a null there stays a harmless default.
    assert hours[0].wind_gusts_kn == 0.0


# --- stored, not scored -------------------------------------------------------------


def test_the_extra_fields_are_captured():
    """Six fields a forecast can give us and the archive never can, after the fact.

    What the ocean did is recoverable from ERA5 whenever we ask. What we believed it
    would do exists only if the worker wrote it down at the time, so these are stored
    now and left out of the score until the harness can say whether they read the water
    better than what it uses today.
    """
    src = _one_hour_response(
        {"wind_speed_10m": [8.5], "wind_direction_10m": [70], "wind_gusts_10m": [14.0]},
        marine_extra={
            "swell_wave_peak_period": [13.4],
            "wave_height": [1.9],
            "wave_period": [7.2],
            "secondary_swell_wave_height": [0.6],
            "secondary_swell_wave_direction": [200],
            "secondary_swell_wave_period": [8.1],
        },
    )
    h = src.fetch([_SPOT], forecast_days=7)[0]

    # Peak period is a separate number from the mean the score gates on, not a rename.
    assert h.swell_period_s == 11.0
    assert h.swell_peak_period_s == 13.4
    assert (h.combined_height_m, h.combined_period_s) == (1.9, 7.2)
    assert (h.swell2_height_m, h.swell2_from_deg, h.swell2_period_s) == (0.6, 200, 8.1)


def test_a_missing_extra_field_does_not_drop_the_hour():
    """Nothing gates on these, so an absent reading is absent, not fatal and not zero.

    The mirror of the wind rule above, and the difference is the point: a null in a
    field the score reads is a hole that must not be guessed at, while a null in one it
    ignores is simply nothing to record.
    """
    src = _one_hour_response(
        {"wind_speed_10m": [8.5], "wind_direction_10m": [70], "wind_gusts_10m": [14.0]},
        marine_extra={"swell_wave_peak_period": [None], "wave_height": [None]},
    )
    hours = src.fetch([_SPOT], forecast_days=7)
    assert len(hours) == 1
    assert hours[0].swell_peak_period_s is None
    assert hours[0].combined_height_m is None
    assert hours[0].swell_height_m == 1.3


def test_a_payload_written_before_these_fields_existed_still_loads():
    """Why adding a field needs no migration.

    The stored form is JSONB, so an hour written last week has no key for any of these.
    It has to re-hydrate as None rather than fail, or every historical snapshot becomes
    unreadable the moment the model grows.
    """
    from gogo.ingest.protocol import GridHour

    old = {
        "requested_lat": 38.988, "requested_lon": -9.419,
        "grid_lat": 38.958, "grid_lon": -9.458,
        "valid_at": "2026-08-29T07:00:00Z",
        "swell_height_m": 1.3, "swell_from_deg": 290, "swell_period_s": 11.0,
        "wind_wave_height_m": 0.2, "wind_speed_kn": 8.0, "wind_from_deg": 70,
        "wind_gusts_kn": 12.0, "sea_level_m": 0.4, "source": "open-meteo",
    }
    hour = GridHour.model_validate(old)
    assert hour.swell_height_m == 1.3
    assert hour.swell_peak_period_s is None
    assert hour.combined_height_m is None
    assert hour.swell2_height_m is None
