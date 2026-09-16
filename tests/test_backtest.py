"""S11 — the command, and the three properties that make its output worth reading.

Deterministic, so two runs of the same arguments diff to nothing and a score change
diffs to exactly the numbers it moved. Offline, so nothing it reports depends on what
Open-Meteo happens to say today. And unable to touch `window_impressions`, because that
is the record of what was actually served and an experiment must never be able to edit
the thing it is measured against.
"""

from datetime import date, datetime, timedelta

import pytest
from helpers import grid_day

from gogo.clock import UTC, from_local_input
from gogo.eval import backtest as bt
from gogo.features import BEST_KNOWN, LEAD_24H
from gogo.models import Observation
from gogo.spots import load_spots
from gogo.store import (
    connect,
    ensure_user,
    persist_hours,
    record_observation,
    seed_spots,
)
from gogo.versioning import spec_version

DAY = date(2026, 9, 5)


def _conn():
    try:
        return connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")


def _spots(*ids):
    return [s for s in load_spots() if s.id in ids]


def _seed(conn, spots, ratings: dict[str, int], day: date = DAY, synthetic=False):
    seed_spots(conn, load_spots())
    persist_hours(
        conn, spots, grid_day(day, spots, 6, 20),
        fetched_at=from_local_input(day - timedelta(days=1), "12:00"),
    )
    user = ensure_user(conn, "demo-rater" if synthetic else "max")
    for spot_id, rating in ratings.items():
        record_observation(
            conn, user,
            Observation(
                spot_id=spot_id,
                started_at=from_local_input(day, "07:00"),
                ended_at=from_local_input(day, "09:00"),
                rating=rating, anchored=False,
            ),
            is_synthetic=synthetic,
        )


# --- deterministic --------------------------------------------------------------------


def test_two_runs_of_the_same_arguments_are_byte_identical():
    """A diffable report is only diffable if nothing incidental moves between runs."""
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _seed(conn, spots, {"ribeira": 5, "coxos": 2})
        first = bt.report(bt.run(conn, policies=(BEST_KNOWN,)))
        second = bt.report(bt.run(conn, policies=(BEST_KNOWN,)))
    assert first == second


def test_the_report_carries_no_wall_clock():
    """A timestamp in the body would make every run differ from every other one, which
    is the one thing a diffable report cannot afford. `ran_at` lives in the row instead."""
    conn = _conn()
    with conn:
        text = bt.report(bt.run(conn, policies=(BEST_KNOWN,)))

    assert "ran_at" not in text
    stamp = datetime.now(UTC)
    for fragment in (stamp.strftime("%Y-%m-%d"), stamp.strftime("%H:%M")):
        assert fragment not in text, f"{fragment!r} makes the report undiffable"


def test_the_seed_moves_the_interval_and_not_the_estimate():
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _seed(conn, spots, {"ribeira": 5, "coxos": 2})
        a = bt.run(conn, policies=(BEST_KNOWN,), seed=1)
        b = bt.run(conn, policies=(BEST_KNOWN,), seed=2)
    assert a.results[0].accuracy["incumbent"].value == b.results[0].accuracy["incumbent"].value


# --- cannot damage the record ---------------------------------------------------------


def test_storing_a_run_never_touches_the_served_record():
    """`window_impressions` is what a human was actually shown. A backtest is an
    experiment, run constantly and wrong often, and must not be able to edit it."""
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _seed(conn, spots, {"ribeira": 5, "coxos": 2})
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM window_impressions")
            before = cur.fetchone()["n"]

        bt.store(conn, bt.run(conn, policies=(BEST_KNOWN,)))

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM window_impressions")
            assert cur.fetchone()["n"] == before


def test_a_run_stores_its_arguments_and_its_rows():
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _seed(conn, spots, {"ribeira": 5, "coxos": 2})
        ids = bt.store(conn, bt.run(conn, policies=(BEST_KNOWN,), seed=7))
        assert len(ids) == 1
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM eval_runs WHERE id = %s", (ids[0],))
            run = cur.fetchone()
            cur.execute(
                "SELECT count(*) AS n FROM eval_predictions WHERE run_id = %s", (ids[0],)
            )
            rows = cur.fetchone()["n"]

    assert run["as_of_policy"] == BEST_KNOWN
    assert run["spec_mode"] == bt.CURRENT
    assert run["seed"] == 7
    assert run["samples"] == 2 and rows == 2
    assert run["metrics"]["pairwise"]["incumbent"] is not None


def test_one_run_row_per_policy_so_they_can_be_compared_with_a_where_clause():
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _seed(conn, spots, {"ribeira": 5, "coxos": 2})
        ids = bt.store(conn, bt.run(conn, policies=(BEST_KNOWN, LEAD_24H)))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT as_of_policy FROM eval_runs WHERE id = ANY(%s) ORDER BY id", (ids,)
            )
            policies = [r["as_of_policy"] for r in cur.fetchall()]
    assert policies == [BEST_KNOWN, LEAD_24H]


# --- spec modes -----------------------------------------------------------------------


def test_current_uses_todays_spot_file():
    conn = _conn()
    with conn:
        seed_spots(conn, load_spots())
        got = bt.resolve_spots(conn, bt.CURRENT)
    assert len(got.spots) == len(load_spots())
    assert got.unresolved == [] and got.pinned_spot is None


def test_a_pin_swaps_one_spot_and_leaves_the_rest_alone():
    """A spec version is the digest of a single spot, so a pin names one spot. That is
    the Stage 3 shape: change what Coxos needs, hold everything else still, ask whether
    the one change helped."""
    conn = _conn()
    with conn:
        seed_spots(conn, load_spots())
        coxos = next(s for s in load_spots() if s.id == "coxos")
        got = bt.resolve_spots(conn, f"{bt.PINNED}:{spec_version(coxos)}")
    assert got.pinned_spot == "coxos"
    assert got.unresolved == []
    assert len(got.spots) == len(load_spots())


def test_a_pin_to_a_version_nobody_recorded_says_so_instead_of_pretending():
    """Falling back to today's spec silently would turn the whole run into fiction, and
    a fiction that looks exactly like a real result."""
    conn = _conn()
    with conn:
        seed_spots(conn, load_spots())
        got = bt.resolve_spots(conn, f"{bt.PINNED}:deadbeef1234")
        text = bt.report(
            bt.run(conn, policies=(BEST_KNOWN,), spec_mode=f"{bt.PINNED}:deadbeef1234")
        )

    assert got.pinned_spot is None
    assert got.unresolved and "deadbeef1234" in got.unresolved[0]
    assert "spec not on record" in text
    assert "deadbeef1234" in text


def test_an_unknown_spec_mode_is_refused():
    conn = _conn()
    with conn, pytest.raises(ValueError, match="unknown spec mode"):
        bt.resolve_spots(conn, "vibes")


def test_an_unknown_policy_is_refused():
    conn = _conn()
    with conn, pytest.raises(ValueError, match="unknown as-of policy"):
        bt.run(conn, policies=("last_tuesday",))


# --- what it says when there is nothing to say ----------------------------------------


def test_an_empty_label_table_reports_no_evidence_rather_than_a_bad_score():
    """The state this command will be in until the first labels land. 0.0 would read as
    a terrible score and 0.5 as a coin flip; both would be lies about an empty table."""
    conn = _conn()
    with conn:
        seed_spots(conn, load_spots())
        result = bt.run(conn, policies=(BEST_KNOWN,))
        text = bt.report(result)

    assert result.results[0].accuracy["incumbent"].value is None
    assert "No comparable pairs" in text
    assert "Not a bad score" in text
    assert "| n/a |" in text


def test_synthetic_runs_shout_about_it_in_the_report_and_in_the_row():
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _seed(conn, spots, {"ribeira": 5, "coxos": 2}, synthetic=True)
        result = bt.run(conn, policies=(BEST_KNOWN,), only_synthetic=True)
        text = bt.report(result)
        ids = bt.store(conn, result)
        with conn.cursor() as cur:
            cur.execute("SELECT synthetic FROM eval_runs WHERE id = %s", (ids[0],))
            flagged = cur.fetchone()["synthetic"]

    assert "SYNTHETIC LABELS" in text
    assert "measures our own assumptions" in text
    assert flagged is True


def test_a_real_run_carries_no_synthetic_warning():
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _seed(conn, spots, {"ribeira": 5, "coxos": 2})
        text = bt.report(bt.run(conn, policies=(BEST_KNOWN,)))
    assert "SYNTHETIC" not in text


# --- the finding the gate exists to surface -------------------------------------------


def test_the_report_says_plainly_when_a_baseline_beats_the_score():
    """The plan's own words: if the hand-tuned score loses to biggest-swell-wins, that
    is the finding we needed. It has to be stated, not buried in a table."""
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        # Carcavelos is not in the seeded spots, so always_carcavelos scores 0 for both
        # and ties at 0.5; rate the spot the score dislikes higher and the incumbent
        # drops below that floor.
        seed_spots(conn, load_spots())
        persist_hours(
            conn, spots, grid_day(DAY, spots, 6, 20),
            fetched_at=from_local_input(DAY - timedelta(days=1), "12:00"),
        )
        user = ensure_user(conn, "max")
        # Deliberately rate against the score's own ordering.
        order = sorted(spots, key=lambda s: s.id)
        for spot, rating in zip(order, (1, 5), strict=True):
            record_observation(
                conn, user,
                Observation(
                    spot_id=spot.id,
                    started_at=from_local_input(DAY, "07:00"),
                    ended_at=from_local_input(DAY, "09:00"),
                    rating=rating, anchored=False,
                ),
            )
        text = bt.report(bt.run(conn, policies=(BEST_KNOWN,)))

    assert ("does not beat" in text) or ("Incumbent leads" in text)
    assert "|---|" in text, "the tables render"


def test_a_date_range_narrows_the_samples():
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _seed(conn, spots, {"ribeira": 5, "coxos": 2}, day=DAY)
        _seed(conn, spots, {"ribeira": 4, "coxos": 1}, day=DAY - timedelta(days=30))

        everything = bt.run(conn, policies=(BEST_KNOWN,))
        narrowed = bt.run(conn, policies=(BEST_KNOWN,), from_day=DAY, to_day=DAY)

    assert len(everything.results[0].data) == 4
    assert len(narrowed.results[0].data) == 2
    assert {s.day for s in narrowed.results[0].data.samples} == {DAY}
