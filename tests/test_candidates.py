"""Stage 3's measuring device: a candidate score, compared on the incumbent's own rows.

Two things must hold before any number it prints means anything. A candidate must be
measured exactly the way the incumbent is — same hours, same rounding — or a "win" is
a difference in bookkeeping. And the label-free account of what it moves must count
nothing when nothing changed, or every candidate looks like a big bet.
"""

from datetime import date, timedelta

import pytest
from helpers import grid_day

from gogo.clock import from_local_input
from gogo.eval import backtest as bt
from gogo.eval import candidates as cand
from gogo.eval.dataset import build_dataset
from gogo.features import BEST_KNOWN
from gogo.models import Observation
from gogo.score import INCUMBENT, ScoreOptions, typical_period_s
from gogo.spots import load_spots
from gogo.store import connect, ensure_user, persist_hours, record_observation, seed_spots

DAY = date(2026, 9, 5)


def _conn():
    try:
        return connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")


def _spots(*ids):
    return [s for s in load_spots() if s.id in ids]


def _hours(conn, spots, day=DAY, **overrides):
    seed_spots(conn, load_spots())
    persist_hours(
        conn, spots, grid_day(day, spots, 6, 20, **overrides),
        fetched_at=from_local_input(day - timedelta(days=1), "12:00"),
    )


# --- names ---------------------------------------------------------------------------


def test_a_name_parses_to_options_and_a_canonical_name():
    assert cand.parse("size_period:8.30") == (
        "size_period:8.3", ScoreOptions(size_ref_period_s=8.3)
    )
    assert cand.parse("size_period:10")[0] == "size_period:10"
    assert cand.parse("size_period:typical") == (
        "size_period:typical", ScoreOptions(size_period_typical=True)
    )


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("tide_magic:1", "unknown candidate"),
        ("size_period", "needs an argument"),
        ("size_period:", "needs an argument"),
        ("size_period:long", "wants seconds"),
        ("size_period:0.8", "outside 3–20 s"),
    ],
)
def test_a_bad_name_is_refused_with_the_reason(name, message):
    with pytest.raises(ValueError, match=message):
        cand.parse(name)


def test_the_same_candidate_twice_is_refused_after_canonicalising():
    with pytest.raises(ValueError, match="given twice"):
        cand.options_for(["size_period:8.3", "size_period:8.30"])


def test_the_suggested_sweep_parses():
    assert len(cand.options_for(list(cand.SUGGESTED))) == len(cand.SUGGESTED)


# --- with labels: measured exactly like the incumbent --------------------------------


def test_with_incumbent_options_a_candidate_reproduces_the_incumbent_exactly():
    """If this fails, every Δ the backtest prints is partly a bookkeeping difference."""
    spots = _spots("ribeira", "coxos", "foz_lizandro")
    conn = _conn()
    with conn:
        _hours(conn, spots, swell_period_s=7.0)
        user = ensure_user(conn, "max")
        for spot, rating in zip(spots, (5, 2, 3), strict=True):
            record_observation(
                conn, user,
                Observation(
                    spot_id=spot.id,
                    started_at=from_local_input(DAY, "07:15"),
                    ended_at=from_local_input(DAY, "09:40"),
                    rating=rating, anchored=False,
                ),
            )
        data = build_dataset(conn, BEST_KNOWN, spots=load_spots())

    predict = cand.predictor(INCUMBENT, load_spots())
    assert len(data) == 3
    assert [predict(s) for s in data.samples] == [s.predicted_score for s in data.samples]


def test_a_candidate_that_changes_nothing_gets_a_delta_of_exactly_zero():
    """Every hour is at 11 s, so an 11 s reference is the incumbent in effect."""
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _hours(conn, spots)
        user = ensure_user(conn, "max")
        for spot, rating in zip(spots, (5, 2), strict=True):
            record_observation(
                conn, user,
                Observation(
                    spot_id=spot.id,
                    started_at=from_local_input(DAY, "07:00"),
                    ended_at=from_local_input(DAY, "09:00"),
                    rating=rating, anchored=False,
                ),
            )
        result = bt.run(conn, policies=(BEST_KNOWN,), candidates=["size_period:11"])

    got = result.results[0].candidates["size_period:11"]
    assert got.versus_incumbent.value == 0.0
    assert got.accuracy.value == result.results[0].accuracy["incumbent"].value
    assert result.movement["size_period:11"].headline_changed == 0


# --- without labels: what it moves ---------------------------------------------------


def test_movement_counts_nothing_when_nothing_changes():
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _hours(conn, spots)
        moved = cand.movement(conn, spots, cand.options_for(["size_period:11"]))

    m = moved["size_period:11"]
    assert m.days == 1
    assert m.headline_changed == 0
    assert sum(m.opened.values()) == sum(m.closed.values()) == 0
    assert m.hours["ribeira"] == 14, "06:00–20:00 local, every surfable hour counted"


def test_re_anchored_movement_is_nothing_on_a_typical_day():
    spots = _spots("ribeira", "coxos", "foz_lizandro")
    conn = _conn()
    with conn:
        _hours(conn, spots, swell_height_m=1.3, swell_period_s=typical_period_s(1.3))
        moved = cand.movement(conn, spots, cand.options_for(["size_period:typical"]))
    m = moved["size_period:typical"]
    assert m.headline_changed == 0
    assert sum(m.opened.values()) == sum(m.closed.values()) == 0


def test_movement_sees_a_candidate_close_a_spot():
    """0.85 m at 6 s passes Ribeira's 0.8 m minimum today and breaks like 0.7 m under an
    8.3 s reference, so the candidate closes the whole day and the headline goes with
    it. That day is exactly where one label would settle the argument."""
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        _hours(conn, spots, swell_height_m=0.85, swell_period_s=6.0)
        moved = cand.movement(conn, spots, cand.options_for(["size_period:8.3"]))

    m = moved["size_period:8.3"]
    assert m.closed["ribeira"] == 14 and m.opened["ribeira"] == 0
    assert m.headline_changed == 1
    assert m.disagreements == [(DAY, "ribeira", None)]
    assert m.incumbent_verdicts["no"] == 0 and m.candidate_verdicts["no"] == 1


def test_movement_respects_the_date_range():
    spots = _spots("ribeira")
    conn = _conn()
    with conn:
        _hours(conn, spots, day=DAY)
        _hours(conn, spots, day=DAY - timedelta(days=10))
        everything = cand.movement(conn, spots, cand.options_for(["size_period:8.3"]))
        narrowed = cand.movement(
            conn, spots, cand.options_for(["size_period:8.3"]), from_day=DAY, to_day=DAY
        )
    assert narrowed["size_period:8.3"].days == 1
    assert everything["size_period:8.3"].days == 2, "the empty days between are skipped"


# --- in the report and the stored row ------------------------------------------------


def test_candidates_reach_the_report_and_the_row():
    spots = _spots("ribeira", "coxos")
    conn = _conn()
    with conn:
        _hours(conn, spots)
        result = bt.run(conn, policies=(BEST_KNOWN,), candidates=["size_period:8.3"])
        text = bt.report(result)
        again = bt.report(bt.run(conn, policies=(BEST_KNOWN,), candidates=["size_period:8.3"]))
        ids = bt.store(conn, result)
        with conn.cursor() as cur:
            cur.execute("SELECT metrics FROM eval_runs WHERE id = %s", (ids[0],))
            metrics = cur.fetchone()["metrics"]

    assert text == again, "still byte-identical with candidates in it"
    assert "### Candidates against the incumbent" in text
    assert "## What each candidate moves" in text
    assert "`size_period:8.3`" in text
    assert set(metrics["candidates"]) == {"size_period:8.3"}
    assert metrics["movement"]["size_period:8.3"]["days"] == 1


def test_no_candidates_means_no_candidate_sections():
    conn = _conn()
    with conn:
        text = bt.report(bt.run(conn, policies=(BEST_KNOWN,)))
    assert "Candidates against" not in text and "What each candidate moves" not in text


def test_a_bad_candidate_fails_before_any_work():
    conn = _conn()
    with conn, pytest.raises(ValueError, match="unknown candidate"):
        bt.run(conn, candidates=["nope:1"])
