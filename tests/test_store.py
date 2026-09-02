from datetime import datetime

import pytest

from gogo.clock import UTC, to_local
from gogo.ingest.protocol import GridHour
from gogo.spots import load_spots
from gogo.store import connect, load_current_hours, persist_hours, seed_spots
from gogo.versioning import spec_version


def _hour(**kwargs) -> GridHour:
    base = dict(
        requested_lat=38.988,
        requested_lon=-9.419,
        grid_lat=38.958,
        grid_lon=-9.458,
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


def test_persist_then_load_roundtrip():
    spots = [s for s in load_spots() if s.id == "ribeira"]
    assert spots
    try:
        conn = connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")

    written = _hour()
    with conn:
        seed_spots(conn, spots)
        n = persist_hours(conn, spots, [written])
        assert n == 1
        loaded = load_current_hours(conn, spots)

    # The table holds every hour ever fetched, so find the one this test wrote
    # rather than trusting the order rows come back in.
    mine = [
        h
        for h in loaded
        if h.requested_lat == spots[0].lat and h.valid_at == written.valid_at
    ]
    assert len(mine) == 1
    assert mine[0].swell_height_m == 1.3
    assert to_local(mine[0].valid_at).hour == 8


def test_seed_spots_stores_the_spec_version():
    spots = [s for s in load_spots() if s.id == "ribeira"]
    try:
        conn = connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")

    with conn:
        seed_spots(conn, spots)
        with conn.cursor() as cur:
            cur.execute("SELECT spec_version FROM spots WHERE id = 'ribeira'")
            stored = cur.fetchone()["spec_version"]
    assert stored == spec_version(spots[0])


def test_dst_fold_keeps_two_distinct_hours():
    """Local 01:00 happens twice on 25 Oct 2026. Both hours must survive."""
    spots = [s for s in load_spots() if s.id == "ribeira"]
    try:
        conn = connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")

    first = _hour(valid_at=datetime(2026, 10, 25, 0, 0, tzinfo=UTC), swell_height_m=1.0)
    second = _hour(valid_at=datetime(2026, 10, 25, 1, 0, tzinfo=UTC), swell_height_m=2.0)
    assert to_local(first.valid_at).hour == to_local(second.valid_at).hour == 1

    with conn:
        seed_spots(conn, spots)
        assert persist_hours(conn, spots, [first, second]) == 2
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT valid_at, payload->>'swell_height_m' AS hs
                FROM forecast_current
                WHERE grid_lat = %s AND grid_lon = %s
                  AND valid_at IN (%s, %s)
                ORDER BY valid_at
                """,
                (first.grid_lat, first.grid_lon, first.valid_at, second.valid_at),
            )
            rows = cur.fetchall()
    assert [float(r["hs"]) for r in rows] == [1.0, 2.0]


def test_retry_does_not_duplicate_current():
    spots = [s for s in load_spots() if s.id == "ribeira"]
    try:
        conn = connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")

    hour = _hour(swell_height_m=1.1)
    with conn:
        seed_spots(conn, spots)
        persist_hours(conn, spots, [hour])
        persist_hours(conn, spots, [_hour(swell_height_m=1.4)])
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload->>'swell_height_m' AS hs, COUNT(*)
                FROM forecast_current
                WHERE grid_lat = %s AND grid_lon = %s
                  AND valid_at = %s
                GROUP BY 1
                """,
                (hour.grid_lat, hour.grid_lon, hour.valid_at),
            )
            rows = cur.fetchall()
    assert len(rows) == 1
    assert float(rows[0]["hs"]) == 1.4
