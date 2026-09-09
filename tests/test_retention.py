"""Keeping the tables bounded, and noticing when the loop has stopped.

Three properties, all of which fail silently if they break. A snapshot table that
records the same belief every hour still looks like it is working. A serving table that
grows forever still returns the right answer, just slower every week. And a worker that
died still leaves a page that renders.
"""

from datetime import timedelta

import pytest
from helpers import grid_hour

from gogo.clock import now_utc
from gogo.spots import load_spots
from gogo.store import (
    connect,
    load_current_hours,
    persist_hours,
    prune_current,
    seed_spots,
)
from gogo.worker import health


def _conn():
    try:
        return connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")


def _ribeira():
    return [s for s in load_spots() if s.id == "ribeira"]


def _snapshot_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM forecast_snapshots WHERE NOT is_analysis")
        return cur.fetchone()["n"]


# --- snapshots: record beliefs, not poll attempts -----------------------------------


def test_an_unchanged_forecast_is_not_appended_twice():
    """The measured case. Two cycles 23 minutes apart wrote 1176 identical payloads.

    Hourly polling of a model that updates a few times a day spends most of its writes
    restating what it already said, and every one of those rows costs disk forever.
    """
    spots = _ribeira()
    hour = grid_hour(valid_at=now_utc() + timedelta(hours=5))
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        assert persist_hours(conn, spots, [hour]).appended == 1
        assert persist_hours(conn, spots, [hour]).appended == 0
        assert _snapshot_count(conn) == 1


def test_a_changed_forecast_is_appended():
    """The whole point of skipping repeats is that a real revision still lands."""
    spots = _ribeira()
    at = now_utc() + timedelta(hours=5)
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(conn, spots, [grid_hour(valid_at=at, swell_height_m=1.3)])
        written = persist_hours(conn, spots, [grid_hour(valid_at=at, swell_height_m=2.1)])
        assert written.appended == 1
        assert _snapshot_count(conn) == 2


def test_a_repeat_still_moves_the_freshness_stamp():
    """Skipping a snapshot must not make the system look like it stopped fetching.

    `current.fetched_at` means "we checked", not "it changed" — it is what the page
    shows and what `gogo health` reads. If an unchanged cycle left it alone, a calm
    week of stable forecasts would read as a dead worker.
    """
    spots = _ribeira()
    hour = grid_hour(valid_at=now_utc() + timedelta(hours=5))
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(conn, spots, [hour])
        with conn.cursor() as cur:
            cur.execute("SELECT max(fetched_at) AS t FROM forecast_current")
            first = cur.fetchone()["t"]

        written = persist_hours(conn, spots, [hour])
        assert written.appended == 0
        assert written.current == 1

        with conn.cursor() as cur:
            cur.execute("SELECT max(fetched_at) AS t FROM forecast_current")
            assert cur.fetchone()["t"] > first


# --- current: bounded read, bounded table -------------------------------------------


def test_the_serving_read_skips_hours_that_have_already_happened():
    spots = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(
            conn,
            spots,
            [
                grid_hour(valid_at=now_utc() - timedelta(days=3), swell_height_m=1.1),
                grid_hour(valid_at=now_utc() + timedelta(hours=5), swell_height_m=1.9),
            ],
        )
        loaded = load_current_hours(conn, spots)

    assert [h.swell_height_m for h in loaded] == [1.9]


def test_pruning_removes_past_hours_and_leaves_future_ones():
    spots = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(
            conn,
            spots,
            [
                grid_hour(valid_at=now_utc() - timedelta(days=5), swell_height_m=1.1),
                grid_hour(valid_at=now_utc() + timedelta(hours=5), swell_height_m=1.9),
            ],
        )
        assert prune_current(conn) == 1
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM forecast_current")
            assert cur.fetchone()["n"] == 1


def test_pruning_never_touches_a_stale_forecast_of_future_hours():
    """Keyed on `valid_at`, never `fetched_at`.

    If Open-Meteo is unreachable for a day, the stored forecast goes out of date but
    still describes hours ahead of us. Deleting it because it is old would turn a
    degraded service into no service at all.
    """
    spots = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(
            conn,
            spots,
            [grid_hour(valid_at=now_utc() + timedelta(hours=30))],
            fetched_at=now_utc() - timedelta(days=4),
        )
        assert prune_current(conn) == 0


def test_snapshots_survive_a_prune():
    """`forecast_current` is a cache of what is still ahead. History lives elsewhere."""
    spots = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(conn, spots, [grid_hour(valid_at=now_utc() - timedelta(days=5))])
        prune_current(conn)
        assert _snapshot_count(conn) == 1


# --- health: the thing that asks ----------------------------------------------------


def test_health_fails_when_nothing_has_ever_been_fetched():
    _conn().close()
    report = health()
    assert not report.ok
    assert report.stale
    assert report.as_of is None
    assert "no cycle has ever completed" in report.lines(spot_count=16)[0]


def test_health_fails_on_a_forecast_that_stopped_moving():
    """The exact shape of the failure this repo already had: nothing errors, the
    timestamp just stops."""
    spots = load_spots()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(
            conn,
            spots,
            [
                grid_hour(
                    requested_lat=s.lat,
                    requested_lon=s.lon,
                    grid_lat=s.lat,
                    grid_lon=s.lon,
                    valid_at=now_utc() + timedelta(hours=5),
                )
                for s in spots
            ],
            fetched_at=now_utc() - timedelta(hours=9),
        )

    report = health()
    assert report.stale
    assert not report.ok
    assert report.missing == []
    assert "STALE" in report.lines(spot_count=len(spots))[0]


def test_health_fails_on_a_spot_that_quietly_left_the_ranking():
    """Ribeira's grid row once pointed at rounded test coordinates and the spot vanished
    from every ranking for four days. Nothing complained, because one fewer row in a
    list looks entirely normal."""
    spots = load_spots()
    one = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(conn, one, [grid_hour(valid_at=now_utc() + timedelta(hours=5))])

    report = health()
    assert report.missing, "fifteen spots have no hours and health said nothing"
    assert "ribeira" not in report.missing
    assert not report.ok
    assert "NO HOURS" in report.lines(spot_count=len(spots))[1]


def test_health_passes_when_the_forecast_is_fresh_and_every_spot_is_covered():
    spots = load_spots()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(
            conn,
            spots,
            [
                grid_hour(
                    requested_lat=s.lat,
                    requested_lon=s.lon,
                    grid_lat=s.lat,
                    grid_lon=s.lon,
                    valid_at=now_utc() + timedelta(hours=5),
                )
                for s in spots
            ],
        )

    report = health()
    assert report.ok
    assert not report.stale
    assert report.missing == []
