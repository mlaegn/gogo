from datetime import datetime, timedelta

import pytest

from gogo.clock import UTC
from gogo.migrate import migrate, migration_files
from gogo.models import HourForecast
from gogo.score import SCORE_VERSION, rank_hour
from gogo.spots import load_spots
from gogo.store import connect, impression_for, record_impressions, seed_spots
from gogo.versioning import spec_version

WHEN = datetime(2026, 9, 5, 7, 0, tzinfo=UTC)


def _conn():
    try:
        return connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")


def _ranked():
    hour = HourForecast(
        valid_at=WHEN,
        swell_height_m=1.4,
        swell_from_deg=295,
        swell_period_s=11.0,
        wind_speed_kn=7.0,
        wind_from_deg=80,
        tide="mid",
    )
    return rank_hour(load_spots(), hour)


def test_impressions_are_stamped_and_findable():
    spots = load_spots()
    ranked = _ranked()
    as_of = datetime(2026, 9, 3, 6, 0, tzinfo=UTC)
    conn = _conn()

    with conn:
        seed_spots(conn, spots)
        written = record_impressions(conn, ranked, spots, as_of, surface="cli")
        assert written == len(ranked)

        top = ranked[0]
        found = impression_for(conn, top.spot_id, WHEN, WHEN + timedelta(hours=1))

    assert found is not None
    assert found["score"] == top.score
    assert found["verdict"] == top.verdict
    assert found["score_version"] == SCORE_VERSION
    assert found["spec_version"] == spec_version(
        next(s for s in spots if s.id == top.spot_id)
    )


def test_impression_lookup_ignores_a_non_overlapping_window():
    spots = load_spots()
    ranked = _ranked()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        record_impressions(
            conn, ranked, spots, datetime(2026, 9, 3, 6, 0, tzinfo=UTC), surface="cli"
        )
        # A session two days later must not pair with today's recommendation.
        away = WHEN + timedelta(days=2)
        assert impression_for(conn, ranked[0].spot_id, away, away + timedelta(hours=1)) is None


def test_impressions_are_append_only():
    """Showing the same window twice is two rows: the log is what we said, when."""
    spots = load_spots()
    ranked = _ranked()[:1]
    as_of = datetime(2026, 9, 3, 6, 0, tzinfo=UTC)
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM window_impressions WHERE spot_id = %s",
                (ranked[0].spot_id,),
            )
            before = cur.fetchone()["n"]
        record_impressions(conn, ranked, spots, as_of, surface="cli")
        record_impressions(conn, ranked, spots, as_of, surface="cli")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM window_impressions WHERE spot_id = %s",
                (ranked[0].spot_id,),
            )
            after = cur.fetchone()["n"]
    assert after == before + 2


def test_migrations_sort_and_are_idempotent():
    names = [p.name for p in migration_files()]
    assert names == sorted(names)
    assert names[0] == "001_init.sql"

    conn = _conn()
    with conn:
        assert migrate(conn) == []
