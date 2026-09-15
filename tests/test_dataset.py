"""S9 — labels joined to what was knowable, grouped into events.

Two properties here are worth more than the rest. Synthetic labels must stay out unless
asked for by name, because they are derived from our own score and a metric that counts
them flatters us with no way to tell. And observations that cannot be scored must be
counted as dropped rather than skipped, because a shrinking denominator improves every
ratio computed from it and looks like nothing at all.
"""

from datetime import date, datetime, timedelta

import pytest
from helpers import grid_day

from gogo.clock import UTC, from_local_input, to_local
from gogo.eval.dataset import EVENT_GAP, assign_events, build_dataset
from gogo.features import BEST_KNOWN, EVENING_BEFORE
from gogo.models import Observation
from gogo.spots import load_spots
from gogo.store import (
    connect,
    ensure_user,
    persist_hours,
    record_impressions,
    record_observation,
    seed_spots,
)

DAY = date(2026, 9, 5)  # a Saturday, in the past


def _conn():
    try:
        return connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")


def _spots(*ids):
    return [s for s in load_spots() if s.id in ids]


def _observe(conn, spot_id, day, start, end, synthetic=False, **kwargs):
    user = ensure_user(conn, "demo-rater" if synthetic else "max")
    return record_observation(
        conn,
        user,
        Observation(
            spot_id=spot_id,
            started_at=from_local_input(day, start),
            ended_at=from_local_input(day, end),
            anchored=False,
            **kwargs,
        ),
        is_synthetic=synthetic,
    )


def _seed_a_day(conn, spots, day=DAY, **overrides):
    seed_spots(conn, load_spots())
    persist_hours(
        conn, spots, grid_day(day, spots, 6, 20, **overrides),
        fetched_at=from_local_input(day - timedelta(days=1), "12:00"),
    )


# --- events -------------------------------------------------------------------------


def test_a_gap_longer_than_the_rule_starts_a_new_event():
    base = datetime(2026, 9, 5, 7, tzinfo=UTC)
    starts = [base, base + timedelta(hours=24), base + timedelta(hours=24) + EVENT_GAP
              + timedelta(minutes=1)]
    assert assign_events(starts) == [0, 0, 1]


def test_a_gap_inside_the_rule_stays_one_event():
    base = datetime(2026, 9, 5, 7, tzinfo=UTC)
    assert assign_events([base, base + EVENT_GAP]) == [0, 0]


def test_two_spots_on_one_morning_are_one_event_not_two():
    """A swell hits the whole coast. If a same-day pair were two events, a bootstrap
    over events would resample it as two independent draws and report a confidence
    interval narrower than the data supports."""
    base = datetime(2026, 9, 5, 7, tzinfo=UTC)
    assert assign_events([base, base + timedelta(minutes=90)]) == [0, 0]


def test_no_observations_is_no_events():
    assert assign_events([]) == []


# --- the join -----------------------------------------------------------------------


def test_a_label_is_joined_to_the_hours_the_person_was_in_the_water():
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        _seed_a_day(conn, spots)
        _observe(conn, "ribeira", DAY, "07:15", "09:00", rating=4)
        data = build_dataset(conn, BEST_KNOWN, spots=spots)

    assert len(data) == 1
    sample = data.samples[0]
    # 07:15-09:00 overlaps the 07:00 hour partly and the 08:00 hour fully, and stops
    # exactly where the 09:00 hour begins. Same exclusive end as a window's `ends_at`.
    assert [to_local(h.valid_at).hour for h in sample.hours] == [7, 8]
    assert sample.predicted_score is not None
    assert sample.rating == 4
    assert sample.spot_id == "ribeira"
    assert sample.day == DAY


def test_a_session_running_past_the_hour_picks_that_hour_up():
    """The other side of the exclusive end: 09:30 does reach into the 09:00 hour."""
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        _seed_a_day(conn, spots)
        _observe(conn, "ribeira", DAY, "07:15", "09:30", rating=4)
        sample = build_dataset(conn, BEST_KNOWN, spots=spots).samples[0]

    assert [to_local(h.valid_at).hour for h in sample.hours] == [7, 8, 9]


def test_the_prediction_is_the_mean_over_those_hours():
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        _seed_a_day(conn, spots)
        _observe(conn, "ribeira", DAY, "07:00", "08:00", rating=3)
        sample = build_dataset(conn, BEST_KNOWN, spots=spots).samples[0]

    assert sample.predicted_score == round(
        sum(h.score for h in sample.hours) / len(sample.hours)
    )


def test_an_observation_with_no_stored_features_is_dropped_and_counted():
    """Silently skipping it would shrink the denominator of every metric."""
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        seed_spots(conn, load_spots())
        _observe(conn, "ribeira", DAY, "07:00", "09:00", rating=4)
        data = build_dataset(conn, BEST_KNOWN, spots=spots)

    assert len(data) == 0
    assert sum(data.dropped.values()) == 1
    assert any("no stored features" in reason for reason in data.dropped)
    assert any("dropped 1" in line for line in data.summary())


def test_the_as_of_policy_reaches_the_join():
    """A label on a day whose only forecast arrived after the decision moment can be
    scored by `best_known` and not by `evening_before`."""
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        seed_spots(conn, load_spots())
        persist_hours(
            conn, spots, grid_day(DAY, spots, 6, 20),
            fetched_at=from_local_input(DAY, "05:00"),  # after 18:00 the night before
        )
        _observe(conn, "ribeira", DAY, "07:00", "09:00", rating=4)

        assert len(build_dataset(conn, BEST_KNOWN, spots=spots)) == 1
        late = build_dataset(conn, EVENING_BEFORE, spots=spots)

    assert len(late) == 0
    assert late.policy == EVENING_BEFORE


def test_what_we_showed_is_carried_when_there_was_an_impression():
    """Only anchored rows can answer "would we have sent you to the right place"."""
    from gogo.assemble import windows_for_day

    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        _seed_a_day(conn, spots)
        hours = grid_day(DAY, spots, 6, 20)
        ranked = windows_for_day(spots, hours, DAY)
        record_impressions(
            conn, ranked, spots, from_local_input(DAY - timedelta(days=1), "12:00"),
            surface="api",
        )
        _observe(conn, "ribeira", DAY, "07:00", "09:00", rating=4)
        sample = build_dataset(conn, BEST_KNOWN, spots=spots).samples[0]

    assert sample.shown_score == ranked[0].score
    assert sample.shown_verdict == ranked[0].verdict


def test_a_session_with_no_impression_carries_none_rather_than_a_guess():
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        _seed_a_day(conn, spots)
        _observe(conn, "ribeira", DAY, "07:00", "09:00", rating=4)
        sample = build_dataset(conn, BEST_KNOWN, spots=spots).samples[0]

    assert sample.shown_score is None
    assert sample.shown_verdict is None


# --- the quarantine -----------------------------------------------------------------


def test_synthetic_labels_are_excluded_by_default():
    """The default that matters most in this file. `gogo demo` ratings come from our own
    score, so counting them measures our assumptions and returns a flattering number."""
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        _seed_a_day(conn, spots)
        _observe(conn, "ribeira", DAY, "07:00", "09:00", rating=4, synthetic=True)
        data = build_dataset(conn, BEST_KNOWN, spots=spots)

    assert len(data) == 0
    assert not any("SYNTHETIC" in line for line in data.summary())


def test_synthetic_labels_can_be_asked_for_by_name_and_say_so_loudly():
    """The harness has to be validated against a known injected bias before it is
    pointed at real labels. That is the only legitimate use, so it announces itself."""
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        _seed_a_day(conn, spots)
        _observe(conn, "ribeira", DAY, "07:00", "09:00", rating=4, synthetic=True)
        data = build_dataset(conn, BEST_KNOWN, only_synthetic=True, spots=spots)

    assert len(data) == 1
    assert data.samples[0].is_synthetic
    assert any("SYNTHETIC" in line for line in data.summary())


def test_real_and_synthetic_are_never_mixed_silently():
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        _seed_a_day(conn, spots)
        _observe(conn, "ribeira", DAY, "07:00", "09:00", rating=4)
        _observe(conn, "ribeira", DAY, "10:00", "11:00", rating=2, synthetic=True)

        real = build_dataset(conn, BEST_KNOWN, spots=spots)
        both = build_dataset(conn, BEST_KNOWN, include_synthetic=True, spots=spots)

    assert len(real) == 1 and not real.samples[0].is_synthetic
    assert len(both) == 2
    assert any("SYNTHETIC" in line for line in both.summary())


# --- what the headline metric will actually have to work with ------------------------


def test_pairs_count_same_day_different_spot_combinations():
    spots = _spots("ribeira", "coxos", "carcavelos")
    conn = _conn()
    with conn:
        _seed_a_day(conn, spots)
        for spot in ("ribeira", "coxos", "carcavelos"):
            _observe(conn, spot, DAY, "07:00", "09:00", rating=3, kind="checked")
        data = build_dataset(conn, BEST_KNOWN, spots=spots)

    assert len(data) == 3
    assert data.pairs == 3  # three spots on one day is three pairs
    assert data.events == 1
    assert data.days == 1


def test_one_spot_a_day_yields_labels_and_no_pairs():
    """Fifty one-spot days are fifty labels and zero pairs. This is the number that
    makes `kind='checked'` rows the point rather than padding."""
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        for offset in (0, 7):
            day = DAY - timedelta(days=offset)
            _seed_a_day(conn, spots, day=day)
            _observe(conn, "ribeira", day, "07:00", "09:00", rating=3)
        data = build_dataset(conn, BEST_KNOWN, spots=spots)

    assert len(data) == 2
    assert data.pairs == 0
    assert data.events == 2, "a week apart is two swells"


def test_an_empty_table_produces_an_empty_dataset_rather_than_an_error():
    conn = _conn()
    with conn:
        seed_spots(conn, load_spots())
        data = build_dataset(conn, BEST_KNOWN)
    assert len(data) == 0
    assert data.pairs == 0 and data.events == 0
    assert "0 samples" in data.summary()[0]
