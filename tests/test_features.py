"""S8 — what we knew at a chosen moment.

The failure this module exists to prevent leaves no trace in its output. A backtest that
leaks later knowledge does not crash or look odd; it simply returns better numbers than
the system ever earned, and the better they look the less anyone questions them. So the
tests here are mostly negative: they assert that things which exist are *not* visible.
"""

from datetime import date, timedelta

import pytest
from helpers import grid_day

from gogo.assemble import windows_for_day
from gogo.clock import from_local_input, now_utc, to_local
from gogo.features import (
    BEST_KNOWN,
    EVENING_BEFORE,
    LEAD_24H,
    POLICIES,
    as_of_for,
    features_for_day,
)
from gogo.spots import load_spots
from gogo.store import (
    Written,
    connect,
    persist_analysis_hours,
    persist_hours,
    record_fetch_cycle,
    seed_spots,
)

DAY = date(2026, 9, 5)  # a Saturday, safely in the past


def _conn():
    try:
        return connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")


def _ribeira():
    return [s for s in load_spots() if s.id == "ribeira"]


# --- the as-of bound itself ---------------------------------------------------------


def test_a_later_fetch_is_invisible_at_an_earlier_as_of():
    """The test the plan names, and the whole point of the module.

    Two beliefs about the same hour: one formed two days out, one formed at 05:00 on the
    morning itself. Asked as of the evening before, only the earlier one may come back.
    The later revision is better information and that is exactly why it must not be
    visible — using it would score the forecast with a correction that had not happened
    when anyone had to decide.
    """
    spots = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(
            conn, spots, grid_day(DAY, spots, 7, 9, swell_height_m=1.0),
            fetched_at=from_local_input(DAY - timedelta(days=2), "12:00"),
        )
        persist_hours(
            conn, spots, grid_day(DAY, spots, 7, 9, swell_height_m=3.0),
            fetched_at=from_local_input(DAY, "05:00"),
        )
        evening = features_for_day(conn, spots, DAY, EVENING_BEFORE)
        best = features_for_day(conn, spots, DAY, BEST_KNOWN)

    assert {h.swell_height_m for h in evening.hours} == {1.0}, "the revision leaked"
    assert {h.swell_height_m for h in best.hours} == {3.0}, "unbounded should see it"


def test_only_the_newest_belief_before_the_as_of_survives():
    """Many fetches, one row per hour: the last thing we believed in time."""
    spots = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        for days_before, height in ((3, 1.0), (2, 2.0), (1, 3.0)):
            persist_hours(
                conn, spots,
                grid_day(DAY, spots, 7, 9, swell_height_m=height),
                fetched_at=from_local_input(DAY - timedelta(days=days_before), "12:00"),
            )
        got = features_for_day(conn, spots, DAY, EVENING_BEFORE)

    # 18:00 the evening before is after all three fetches, so the newest wins, and
    # there is exactly one row per hour rather than three.
    assert {h.swell_height_m for h in got.hours} == {3.0}
    assert len(got.hours) == 2


def test_a_fetch_after_the_as_of_is_not_seen_at_all():
    spots = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        # Only belief: formed on the morning of the day itself, after the evening
        # decision would have been made.
        persist_hours(
            conn, spots, grid_day(DAY, spots, 7, 9),
            fetched_at=from_local_input(DAY, "05:00"),
        )
        evening = features_for_day(conn, spots, DAY, EVENING_BEFORE)
        best = features_for_day(conn, spots, DAY, BEST_KNOWN)

    assert evening.hours == [], "nothing was known the evening before"
    assert best.hours, "but it is the best estimate we hold"


# --- reanalysis, the leak that flatters ---------------------------------------------


def test_reanalysis_is_invisible_to_a_lead_policy_even_when_its_timestamp_would_pass():
    """The double guard, and why the timestamp alone is not enough.

    Analysis rows carry `fetched_at` = when *we pulled them*, not when they were valid.
    Here the pull is deliberately older than the as-of, so the timestamp comparison lets
    it through. Only the explicit `NOT is_analysis` filter keeps a perfect account of
    what actually happened out of a question about what was knowable beforehand.
    """
    spots = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        pulled_long_ago = from_local_input(DAY - timedelta(days=10), "12:00")
        persist_analysis_hours(
            conn, spots,
            grid_day(DAY, spots, 7, 9, swell_height_m=4.2, source="archive-era5"),
            fetched_at=pulled_long_ago,
        )
        assert pulled_long_ago < as_of_for(LEAD_24H, DAY), "setup: timestamp would pass"

        for policy in (LEAD_24H, EVENING_BEFORE):
            got = features_for_day(conn, spots, DAY, policy)
            assert got.hours == [], f"{policy} saw reanalysis"

        assert features_for_day(conn, spots, DAY, BEST_KNOWN).hours, (
            "Q1 is supposed to use it"
        )


def test_best_known_prefers_reanalysis_over_an_older_forecast():
    """What the ocean did beats what we guessed it would do — for Q1 only."""
    spots = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(
            conn, spots, grid_day(DAY, spots, 7, 9, swell_height_m=1.0),
            fetched_at=from_local_input(DAY - timedelta(days=1), "12:00"),
        )
        persist_analysis_hours(
            conn, spots,
            grid_day(DAY, spots, 7, 9, swell_height_m=2.5, source="archive-era5"),
            fetched_at=now_utc(),
        )
        got = features_for_day(conn, spots, DAY, BEST_KNOWN)

    assert {h.swell_height_m for h in got.hours} == {2.5}


# --- the policies themselves --------------------------------------------------------


def test_evening_before_is_six_in_the_evening_local_the_day_before():
    resolved = to_local(as_of_for(EVENING_BEFORE, DAY))
    assert resolved.date() == DAY - timedelta(days=1)
    assert (resolved.hour, resolved.minute) == (18, 0)


def test_lead_24h_gives_every_surfable_hour_at_least_a_day_of_warning():
    first_surfable = from_local_input(DAY, "06:00")
    assert first_surfable - as_of_for(LEAD_24H, DAY) == timedelta(hours=24)


def test_best_known_is_unbounded():
    assert as_of_for(BEST_KNOWN, DAY) is None


def test_an_unknown_policy_is_refused_rather_than_guessed_at():
    with pytest.raises(ValueError, match="unknown as-of policy"):
        as_of_for("yesterday_ish", DAY)
    conn = _conn()
    with conn, pytest.raises(ValueError, match="unknown as-of policy"):
        features_for_day(conn, _ribeira(), DAY, "yesterday_ish")


def test_every_named_policy_resolves():
    for policy in POLICIES:
        as_of_for(policy, DAY)  # must not raise


# --- coverage: how stale was "what we knew"? ----------------------------------------


def test_stale_by_shows_the_worker_was_down_before_the_decision():
    """A 24-hour lead is only 24 hours if we were awake 24 hours ago.

    Dedup means the newest snapshot before an as-of can be old simply because nothing
    changed, so the snapshot's own age cannot tell you this. `fetch_cycles` can.
    """
    spots = _ribeira()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(
            conn, spots, grid_day(DAY, spots, 7, 9),
            fetched_at=from_local_input(DAY - timedelta(days=2), "12:00"),
        )
        # Last successful cycle: twelve hours before the evening decision.
        as_of = as_of_for(EVENING_BEFORE, DAY)
        record_fetch_cycle(
            conn, as_of - timedelta(hours=12), Written(1176, 0), "open-meteo"
        )
        got = features_for_day(conn, spots, DAY, EVENING_BEFORE)

    assert got.stale_by == timedelta(hours=12)


def test_best_known_has_no_staleness_because_it_has_no_as_of():
    conn = _conn()
    with conn:
        got = features_for_day(conn, _ribeira(), DAY, BEST_KNOWN)
    assert got.as_of is None
    assert got.stale_by is None


# --- the point of all of it ---------------------------------------------------------


def test_features_score_through_the_same_path_as_serving():
    """A backtest must score past days with the product's own code, not a copy of it.

    `features_for_day` returns the same `GridHour` shape `load_current_hours` does, so
    `windows_for_day` takes it untouched. Two code paths would mean the harness measured
    the harness.
    """
    spots = load_spots()
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        persist_hours(
            conn, spots, grid_day(DAY, spots, 6, 20),
            fetched_at=from_local_input(DAY - timedelta(days=1), "12:00"),
        )
        got = features_for_day(conn, spots, DAY, EVENING_BEFORE)

    ranked = windows_for_day(spots, got.hours, DAY)
    assert len(ranked) == len(spots)
    assert all(w.starts_at < w.ends_at for w in ranked)
    # Every hour came from a belief that predated the decision.
    assert all(h.valid_at.date() in (DAY, DAY - timedelta(days=1)) for h in got.hours)


def test_a_day_we_hold_nothing_for_is_empty_rather_than_an_error():
    conn = _conn()
    with conn:
        seed_spots(conn, _ribeira())
        got = features_for_day(conn, _ribeira(), date(2020, 1, 1), BEST_KNOWN)
    assert got.hours == []
    assert got.day == date(2020, 1, 1)
