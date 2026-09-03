from datetime import datetime, timedelta
from typing import get_args

import psycopg
import pytest
from pydantic import ValidationError

from gogo.clock import UTC
from gogo.models import FaultCode, HourForecast, Observation
from gogo.score import rank_hour
from gogo.spots import load_spots
from gogo.store import connect, ensure_user, record_observation, seed_spots

WHEN = datetime(2026, 9, 5, 7, 0, tzinfo=UTC)


def _observation(**kwargs) -> Observation:
    base = dict(
        spot_id="ribeira",
        kind="surfed",
        started_at=WHEN,
        ended_at=WHEN + timedelta(hours=2),
        residual=-1,
        anchored=True,
    )
    base.update(kwargs)
    return Observation.model_validate(base)


def test_fault_codes_are_exactly_the_scores_reason_codes():
    """A fault must name a gate the score actually has, or it cannot be attributed.

    If this fails, either the score grew a term with no way to report it wrong, or
    FaultCode drifted from reality.
    """
    hours = [
        HourForecast(
            valid_at=WHEN,
            swell_height_m=hs,
            swell_from_deg=deg,
            swell_period_s=period,
            wind_speed_kn=wind,
            wind_from_deg=wind_deg,
            tide=tide,
        )
        for hs, deg, period, wind, wind_deg, tide in [
            (1.2, 280, 8.0, 8.0, 70, "mid"),
            (0.2, 180, 4.0, 30.0, 260, "low"),
            (4.5, 310, 16.0, 2.0, 80, "high"),
        ]
    ]
    emitted = {
        reason.code
        for hour in hours
        for window in rank_hour(load_spots(), hour)
        for reason in window.reasons
    }
    assert emitted == set(get_args(FaultCode))


def test_interval_must_run_forwards():
    with pytest.raises(ValidationError):
        _observation(ended_at=WHEN - timedelta(hours=1))
    with pytest.raises(ValidationError):
        _observation(ended_at=WHEN)


def test_residual_is_bounded():
    with pytest.raises(ValidationError):
        _observation(residual=-3)
    assert _observation(residual=2).residual == 2


def test_checked_needs_no_rating():
    """Looked at it, did not paddle out. The most valuable cheap observation."""
    obs = _observation(kind="checked", residual=-2, rating=None)
    assert obs.kind == "checked"
    assert obs.rating is None


def test_scope_defaults_to_global():
    """Ratings pool; only interpretation is ever group-scoped (docs/plan.md, Stage 4)."""
    assert _observation().scope == "global"


def _conn():
    try:
        return connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")


def test_record_observation_roundtrip():
    spots = [s for s in load_spots() if s.id == "ribeira"]
    conn = _conn()
    obs = _observation(
        residual=-1,
        crowd="busy",
        rating=2,
        note="tide drained faster than we said",
        faults=[{"code": "tide", "direction": -1}, {"code": "size", "direction": -1}],
    )
    with conn:
        seed_spots(conn, spots)
        user_id = ensure_user(conn, "test-harness")
        observation_id = record_observation(conn, user_id, obs)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT residual, crowd, anchored, scope FROM observations WHERE id = %s",
                (observation_id,),
            )
            row = cur.fetchone()
            cur.execute(
                "SELECT code, direction FROM observation_faults "
                "WHERE observation_id = %s ORDER BY code",
                (observation_id,),
            )
            faults = cur.fetchall()

    assert row["residual"] == -1
    assert row["crowd"] == "busy"
    assert row["anchored"] is True
    assert row["scope"] == "global"
    assert [(f["code"], f["direction"]) for f in faults] == [("size", -1), ("tide", -1)]


def test_ensure_user_is_idempotent():
    conn = _conn()
    with conn:
        first = ensure_user(conn, "test-harness")
        second = ensure_user(conn, "test-harness")
    assert first == second


def test_database_rejects_a_backwards_interval():
    """The model guards it; so does the table, because the model is not the only writer."""
    spots = [s for s in load_spots() if s.id == "ribeira"]
    conn = _conn()
    with conn:
        seed_spots(conn, spots)
        user_id = ensure_user(conn, "test-harness")
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observations
                        (user_id, spot_id, kind, started_at, ended_at, anchored)
                    VALUES (%s, 'ribeira', 'surfed', %s, %s, true)
                    """,
                    (user_id, WHEN, WHEN - timedelta(hours=1)),
                )
        conn.rollback()
