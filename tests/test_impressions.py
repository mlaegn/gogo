from datetime import date, datetime, timedelta

import pytest
from helpers import grid_day

from gogo.assemble import windows_for_day
from gogo.clock import UTC, from_local_input
from gogo.migrate import migrate, migration_files
from gogo.score import SCORE_VERSION
from gogo.spots import load_spots
from gogo.store import connect, impression_for, record_impressions, seed_spots
from gogo.versioning import spec_version

DAY = date(2026, 9, 5)  # a Saturday


def _conn():
    try:
        return connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")


def _ranked():
    spots = load_spots()
    return windows_for_day(spots, grid_day(DAY, spots), DAY)


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
        found = impression_for(conn, top.spot_id, top.starts_at, top.ends_at)

    assert found is not None
    assert found["score"] == top.score
    assert found["verdict"] == top.verdict
    assert found["score_version"] == SCORE_VERSION
    assert found["spec_version"] == spec_version(
        next(s for s in spots if s.id == top.spot_id)
    )


def test_an_observation_inside_the_window_pairs_with_it():
    """The reason ranges matter: a 07:15–09:00 session overlaps a 07:00–11:00 window
    but would have missed a single stored 08:00 hour by 45 minutes."""
    spots = load_spots()
    ranked = _ranked()
    top = ranked[0]
    assert top.hours > 1, "fixture should produce a multi-hour window"
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        record_impressions(
            conn, ranked, spots, datetime(2026, 9, 3, 6, 0, tzinfo=UTC), surface="cli"
        )
        session_start = from_local_input(DAY, "07:15")
        session_end = from_local_input(DAY, "09:00")
        assert impression_for(conn, top.spot_id, session_start, session_end) is not None


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
        away = ranked[0].ends_at + timedelta(days=2)
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
