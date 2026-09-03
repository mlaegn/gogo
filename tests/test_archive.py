"""S6 — reanalysis in, serving untouched.

The load-bearing assertion here is a negative one: `persist_analysis_hours` must not
write `forecast_current`. If it ever does, `/windows` starts serving hours that have
already happened, the score looks clairvoyant, and nothing in the output says so.
"""

from datetime import date, datetime, timedelta

import httpx
import pytest
from helpers import grid_hour

from gogo.clock import UTC
from gogo.ingest.archive import SOURCE, ArchiveSource
from gogo.spots import load_spots
from gogo.store import (
    connect,
    load_current_hours,
    persist_analysis_hours,
    persist_hours,
    seed_spots,
)
from gogo.worker import date_chunks

# 08:00 Lisbon on 1 Sep 2025 (WEST, UTC+1) is 07:00Z.
_SEP_0800 = datetime(2025, 9, 1, 7, 0, tzinfo=UTC)
_UNIXTIME = int(_SEP_0800.timestamp())
_DAY = date(2025, 9, 1)

_MARINE_CELL = (38.958336, -9.458328)
_WIND_CELL = (38.980667, -9.369873)


def _source(sea_level: float | None = 0.4, seen: list[httpx.Request] | None = None):
    """An ArchiveSource wired to canned responses shaped like the real ones."""
    marine = {
        "latitude": _MARINE_CELL[0],
        "longitude": _MARINE_CELL[1],
        "hourly": {
            "time": [_UNIXTIME],
            "swell_wave_height": [1.8],
            "swell_wave_direction": [295],
            "swell_wave_period": [13.0],
            "wind_wave_height": [0.3],
            "sea_level_height_msl": [sea_level],
            "sea_surface_temperature": [18.0],
        },
    }
    weather = {
        "latitude": _WIND_CELL[0],
        "longitude": _WIND_CELL[1],
        "hourly": {
            "time": [_UNIXTIME],
            "wind_speed_10m": [6.0],
            "wind_direction_10m": [85],
            "wind_gusts_10m": [11.0],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        url = str(request.url)
        assert "start_date=2025-09-01" in url and "end_date=2025-09-01" in url
        assert "timeformat=unixtime" in url
        assert "forecast_days" not in url
        if "marine" in url:
            assert "cell_selection=sea" in url
            return httpx.Response(200, json=marine)
        assert "archive-api" in url
        assert "wind_speed_unit=kn" in url
        return httpx.Response(200, json=weather)

    return ArchiveSource(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _ribeira():
    spots = [s for s in load_spots() if s.id == "ribeira"]
    assert spots
    return spots


def _connect_or_skip():
    try:
        return connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")


def test_fetch_reads_both_archives_and_tags_the_source():
    seen: list[httpx.Request] = []
    hours = _source(seen=seen).fetch(_ribeira(), _DAY, _DAY)

    assert len(hours) == 1
    assert hours[0].source == SOURCE
    assert hours[0].source != "open-meteo", "an analysis must not look like a forecast"
    assert hours[0].swell_period_s == 13.0
    assert hours[0].valid_at == _SEP_0800
    assert {r.url.host for r in seen} == {
        "marine-api.open-meteo.com",
        "archive-api.open-meteo.com",
    }


def test_the_hour_is_keyed_to_the_marine_cell_not_the_wind_cell():
    """Analysis and forecast rows join on the grid, and the live marine archive returns
    the same sea cell as the marine forecast. Keying off the wind cell instead would
    make the two silently unjoinable."""
    hour = _source().fetch(_ribeira(), _DAY, _DAY)[0]
    assert (hour.grid_lat, hour.grid_lon) == _MARINE_CELL
    assert (hour.grid_lat, hour.grid_lon) != _WIND_CELL
    assert hour.wind_speed_kn == 6.0, "wind still came from the wind response"


def test_an_hour_without_tide_still_loads():
    """Before 2023 the archive has no sea level. Those hours are usable without it."""
    hours = _source(sea_level=None).fetch(_ribeira(), _DAY, _DAY)
    assert len(hours) == 1
    assert hours[0].sea_level_m is None


def test_fetch_rejects_a_backwards_range():
    with pytest.raises(ValueError, match="backwards"):
        _source().fetch(_ribeira(), date(2025, 9, 2), date(2025, 9, 1))


def test_date_chunks_are_contiguous_and_inclusive():
    assert list(date_chunks(_DAY, _DAY, 31)) == [(_DAY, _DAY)]

    chunks = list(date_chunks(date(2025, 1, 1), date(2025, 3, 1), 31))
    assert chunks == [
        (date(2025, 1, 1), date(2025, 1, 31)),
        (date(2025, 2, 1), date(2025, 3, 1)),
    ]
    # No gap and no overlap, which is what makes a resumed backfill trustworthy.
    assert chunks[1][0] - chunks[0][1] == timedelta(days=1)

    with pytest.raises(ValueError):
        list(date_chunks(date(2025, 1, 1), date(2025, 1, 2), 0))


def test_analysis_never_reaches_the_serving_table():
    spots = _ribeira()
    conn = _connect_or_skip()
    with conn:
        seed_spots(conn, spots)
        analysis = grid_hour(valid_at=_SEP_0800, source=SOURCE, swell_height_m=1.8)
        assert persist_analysis_hours(conn, spots, [analysis]) == 1

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM forecast_snapshots WHERE is_analysis")
            assert cur.fetchone()["n"] == 1
            cur.execute("SELECT count(*) AS n FROM forecast_current")
            assert cur.fetchone()["n"] == 0

        assert load_current_hours(conn, spots) == []


def test_a_repeated_backfill_inserts_nothing_twice():
    spots = _ribeira()
    hour = grid_hour(valid_at=_SEP_0800, source=SOURCE)
    conn = _connect_or_skip()
    with conn:
        seed_spots(conn, spots)
        assert persist_analysis_hours(conn, spots, [hour]) == 1
        assert persist_analysis_hours(conn, spots, [hour]) == 0
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM forecast_snapshots")
            assert cur.fetchone()["n"] == 1


def test_a_revised_hour_is_corrected_in_place():
    """The recent end of the archive is provisional. A later backfill must fix it, and
    fixing it must not leave two accounts of the same hour lying around."""
    spots = _ribeira()
    conn = _connect_or_skip()
    with conn:
        seed_spots(conn, spots)
        persist_analysis_hours(
            conn, spots, [grid_hour(valid_at=_SEP_0800, source=SOURCE, swell_height_m=1.8)]
        )
        revised = grid_hour(valid_at=_SEP_0800, source=SOURCE, swell_height_m=2.4)
        assert persist_analysis_hours(conn, spots, [revised]) == 1

        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM forecast_snapshots WHERE is_analysis"
            )
            rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["payload"]["swell_height_m"] == 2.4


def test_a_forecast_and_an_analysis_share_an_hour_without_colliding():
    """The uniqueness is partial: one analysis per hour, but still many forecasts."""
    spots = _ribeira()
    conn = _connect_or_skip()
    with conn:
        seed_spots(conn, spots)
        persist_hours(conn, spots, [grid_hour(valid_at=_SEP_0800)])
        persist_hours(conn, spots, [grid_hour(valid_at=_SEP_0800)])
        persist_analysis_hours(
            conn, spots, [grid_hour(valid_at=_SEP_0800, source=SOURCE)]
        )

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT is_analysis, count(*) AS n
                FROM forecast_snapshots GROUP BY is_analysis ORDER BY is_analysis
                """
            )
            counts = [(r["is_analysis"], r["n"]) for r in cur.fetchall()]
    assert counts == [(False, 2), (True, 1)]


def test_backfill_does_not_repoint_a_spot_already_serving():
    """A spot's serving cell is set by `gogo fetch`; a backfill must not move it."""
    spots = _ribeira()
    conn = _connect_or_skip()
    with conn:
        seed_spots(conn, spots)
        persist_hours(conn, spots, [grid_hour(valid_at=_SEP_0800)])
        persist_analysis_hours(
            conn,
            spots,
            [grid_hour(valid_at=_SEP_0800, grid_lat=1.0, grid_lon=2.0, source=SOURCE)],
        )
        with conn.cursor() as cur:
            cur.execute("SELECT grid_lat, grid_lon FROM spot_grid WHERE spot_id = 'ribeira'")
            row = cur.fetchone()
    assert (row["grid_lat"], row["grid_lon"]) == (38.958, -9.458)
