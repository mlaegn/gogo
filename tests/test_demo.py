"""Fixture labels, and the wall between them and real ones.

The headline test here is `test_synthetic_labels_are_invisible_by_default`. Synthetic
data is legitimate — an evaluation pipeline cannot be built against an empty table — but
it is generated *from our own score*, so any metric that counts it measures our
assumptions and returns a flattering number with nothing to show the mistake.

`AGENTS.md` says a fabricated observation is indistinguishable from a real label once
the harness exists. Migration 006 is what makes that sentence false, and these tests are
what keep it false.
"""

from collections import Counter
from datetime import date, timedelta

import pytest
from helpers import grid_day

from gogo.clock import now_utc
from gogo.demo import BIAS_PIVOT_M, HANDLES, generate, pick_days
from gogo.models import Observation
from gogo.spots import load_spots
from gogo.store import (
    connect,
    count_observations,
    ensure_user,
    load_observations,
    record_observation,
    seed_spots,
)

DAYS = [date(2026, 3, 7), date(2026, 3, 14), date(2026, 3, 21)]


@pytest.fixture
def conn():
    try:
        c = connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")
    with c:
        seed_spots(c, load_spots())
        yield c


@pytest.fixture
def sessions():
    spots = load_spots()
    hours = [h for day in DAYS for h in grid_day(day, spots, 6, 20)]
    return generate(spots, hours, DAYS)


# --- the wall ---------------------------------------------------------------------


def test_synthetic_labels_are_invisible_by_default(conn, sessions):
    """The one property that makes generating this data safe at all."""
    for session in sessions:
        user_id = ensure_user(conn, session.handle)
        record_observation(conn, user_id, session.observation, is_synthetic=True)

    assert count_observations(conn, include_synthetic=True) == len(sessions)
    # Every read an evaluation would go through has to come back empty.
    assert count_observations(conn) == 0
    assert load_observations(conn) == []
    assert len(load_observations(conn, only_synthetic=True)) == len(sessions)


def test_a_label_is_real_unless_someone_says_otherwise(conn):
    """`is_synthetic` defaults to false, so only `gogo demo` can write a fixture row."""
    started = now_utc()
    user_id = ensure_user(conn, "test-harness")
    record_observation(
        conn,
        user_id,
        Observation(
            spot_id=load_spots()[0].id,
            kind="surfed",
            started_at=started,
            ended_at=started + timedelta(hours=1),
            rating=3,
            anchored=False,
        ),
    )

    rows = load_observations(conn)
    assert len(rows) == 1
    assert rows[0]["is_synthetic"] is False


def test_asking_for_both_slices_at_once_is_refused(conn):
    with pytest.raises(ValueError):
        load_observations(conn, include_synthetic=True, only_synthetic=True)


# --- is the fixture actually useful ------------------------------------------------


def test_it_produces_same_day_pairs(sessions):
    """Pairs are the sample size of the headline metric, so a fixture without them
    cannot exercise the thing it exists to test."""
    by_day: dict[date, set[str]] = {}
    for s in sessions:
        by_day.setdefault(s.day, set()).add(s.observation.spot_id)

    assert by_day, "generated nothing"
    assert any(len(spots) >= 2 for spots in by_day.values())


def test_one_rater_is_never_in_two_places_at_once(sessions):
    """A day belongs to one person driving up the coast. Overlapping sessions for the
    same rater is a physically impossible day that per-rater reasoning would trip on."""
    by_rater: dict[tuple[str, date], list] = {}
    for s in sessions:
        by_rater.setdefault((s.handle, s.day), []).append(s.observation)

    for (handle, day), obs in by_rater.items():
        obs.sort(key=lambda o: o.started_at)
        for earlier, later in zip(obs, obs[1:]):
            assert later.started_at >= earlier.ended_at, f"{handle} overlaps on {day}"


def test_ratings_span_the_scale(sessions):
    """A fixture of 1s and 5s would make any score look good, including a bad one. The
    middle is where ranking is hard, so the middle has to exist."""
    spread = Counter(s.observation.rating for s in sessions)

    assert len(spread) >= 3, f"too few distinct ratings: {sorted(spread.items())}"
    assert any(r in spread for r in (2, 3, 4)), "no middling sessions at all"


def test_the_rater_disagrees_with_us_in_the_direction_we_injected(sessions):
    """The declared bias is what makes this fixture a test of a measuring device.

    A synthetic rater who simply agreed with the score would make every metric read
    100% and prove nothing. This one likes size more than we do, so on the bigger days
    it should rate above what our score implies — and a harness that cannot see that
    has a bug in it, not in the surfer.
    """
    big = [s for s in sessions if s.felt > s.our_score]
    assert big, "the bias never showed up; the fixture cannot validate a harness"
    # And it is not merely noise: the gap should track swell, which is what pushed it.
    assert max(s.felt - s.our_score for s in sessions) > BIAS_PIVOT_M


def test_it_is_the_same_fixture_every_time(sessions):
    """Seeded, so a second run is a no-op against the unique constraint rather than a
    second copy of every row."""
    spots = load_spots()
    hours = [h for day in DAYS for h in grid_day(day, spots, 6, 20)]
    again = generate(spots, hours, DAYS)

    assert [s.observation.model_dump() for s in again] == [
        s.observation.model_dump() for s in sessions
    ]


def test_every_rater_is_marked_as_one(sessions):
    """The handle prefix is a second, human-visible signal on top of the column."""
    assert {s.handle for s in sessions} <= set(HANDLES)
    assert all(h.startswith("demo-") for h in HANDLES)


def test_sessions_are_unanchored(sessions):
    """Nothing was on screen on a day in the past, so nothing was anchored to it — the
    same reasoning the CSV importer uses."""
    assert all(s.observation.anchored is False for s in sessions)


# --- day picking ------------------------------------------------------------------


def test_picked_days_are_spread_not_consecutive():
    """Adjacent days sit inside one swell and are not independent samples, so a block of
    them inflates every number the harness reports."""
    covered = {date(2026, 1, 1) + timedelta(days=i) for i in range(90)}

    picked = pick_days(covered, 10)

    assert len(picked) == 10
    assert picked == sorted(picked)
    gaps = [(b - a).days for a, b in zip(picked, picked[1:])]
    assert min(gaps) > 1, f"consecutive days picked: {picked}"


def test_asking_for_more_days_than_we_hold_gives_what_there_is():
    covered = {date(2026, 1, 1), date(2026, 1, 5)}
    assert pick_days(covered, 10) == [date(2026, 1, 1), date(2026, 1, 5)]


def test_no_reanalysis_means_no_days():
    assert pick_days(set(), 5) == []
