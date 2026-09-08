"""S5c — the door, the JSON API, and the property that makes the labels trustworthy.

The headline test is `test_the_log_screens_only_payload_carries_no_score`. With a client
app, "we do not show our number" cannot be a promise a component makes: the number would
be sitting in devtools. So it is a fact about what the endpoint returns, and this asserts
that fact.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import grid_day

from gogo.api import app
from gogo.clock import now_utc, to_local
from gogo.spots import load_spots
from gogo.store import connect, persist_hours, seed_spots
from gogo.web import COOKIE, SECRET_ENV

KEY = "test-key-not-a-real-secret"


@pytest.fixture(autouse=True)
def forecast():
    """Two future days of flat conditions, so the API has something real to answer with.

    Deliberately tomorrow onwards: `available_days` drops hours that have already
    happened, so seeding "today" would behave differently depending on the clock.
    """
    try:
        conn = connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")
    spots = load_spots()
    first = to_local(now_utc()).date() + timedelta(days=1)
    with conn:
        seed_spots(conn, spots)
        persist_hours(
            conn,
            spots,
            grid_day(first, spots, 6, 20) + grid_day(first + timedelta(days=1), spots, 6, 20),
        )
    return first


@pytest.fixture
def locked(monkeypatch):
    monkeypatch.setenv(SECRET_ENV, KEY)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(locked):
    locked.cookies.set(COOKIE, KEY)
    return locked


# --- the door ---------------------------------------------------------------------


def test_without_a_secret_everything_is_off_not_open(monkeypatch):
    """An unset secret must never mean "let everyone in"."""
    monkeypatch.delenv(SECRET_ENV, raising=False)
    with TestClient(app) as anon:
        assert anon.get("/", follow_redirects=False).status_code == 503
        assert anon.get("/api/windows").status_code == 503


def test_the_api_needs_the_cookie(locked):
    """/api/windows writes an impression row, so an open endpoint would let anyone
    fabricate the record of what we supposedly recommended."""
    assert locked.get("/api/windows").status_code == 401
    assert locked.get("/api/days").status_code == 401
    assert locked.get("/api/spots").status_code == 401
    assert locked.post("/api/observations", json={}).status_code == 401


def test_the_app_shell_redirects_to_the_login_form(locked):
    for path in ("/", "/log", "/spot/ribeira"):
        response = locked.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/enter"


def test_the_right_key_gets_an_httponly_cookie(locked):
    assert locked.post("/enter", data={"key": "wrong"}).status_code == 403

    response = locked.post("/enter", data={"key": KEY}, follow_redirects=False)
    assert response.status_code == 303
    assert locked.cookies.get(COOKIE) == KEY
    assert "httponly" in response.headers["set-cookie"].lower()


def test_health_stays_open(locked):
    """A load balancer cannot log in."""
    assert locked.get("/health").json()["status"] == "ok"


# --- the anchoring guarantee ------------------------------------------------------


def test_the_log_screens_only_payload_carries_no_score(client):
    """/api/spots is everything the log screen fetches, and it has no number in it."""
    spots = client.get("/api/spots").json()

    assert len(spots) == len(load_spots())
    assert {key for spot in spots for key in spot} == {"id", "name", "region"}
    banned = {"score", "peak_score", "verdict", "reasons", "windows"}
    assert not banned & {key for spot in spots for key in spot}


def test_the_reveal_only_exists_in_the_response_to_saving(client, forecast):
    top = client.get("/api/windows").json()["windows"][0]
    body = {
        "spot_id": top["spot_id"],
        "day": forecast.isoformat(),
        "start": top["starts_at_local"][11:16],
        "end": top["ends_at_local"][11:16],
        "rating": 4,
    }

    saved = client.post("/api/observations", json=body)

    assert saved.status_code == 200
    result = saved.json()
    assert result["duplicate"] is False
    # The label is stored by the time this number is on the wire.
    assert result["shown"]["score"] == top["score"]
    assert result["shown"]["verdict"] == top["verdict"]


def test_a_session_we_never_advised_is_honest_about_it(client):
    """Unanchored labels still answer "was it any good", never "did we call it right"."""
    saved = client.post(
        "/api/observations",
        json={
            "spot_id": load_spots()[0].id,
            "day": (to_local(now_utc()).date() - timedelta(days=3)).isoformat(),
            "start": "07:00",
            "end": "09:00",
            "rating": 4,
        },
    ).json()
    assert saved["shown"] is None


# --- labels -----------------------------------------------------------------------


def test_logging_the_same_session_twice_stores_one_label(client):
    body = {
        "spot_id": load_spots()[0].id,
        "day": (to_local(now_utc()).date() - timedelta(days=4)).isoformat(),
        "start": "07:00",
        "end": "09:00",
        "rating": 4,
        "kind": "surfed",
        "would_return": True,
        "crowd": "ok",
        "faults": ["wind"],
        "note": "logged by a test",
    }

    assert client.post("/api/observations", json=body).json()["duplicate"] is False
    # 004 says one session is one label, and the API must not be a way around it.
    assert client.post("/api/observations", json=body).json()["duplicate"] is True


def test_a_backwards_session_is_refused_with_a_readable_message(client):
    response = client.post(
        "/api/observations",
        json={
            "spot_id": load_spots()[0].id,
            "day": to_local(now_utc()).date().isoformat(),
            "start": "10:00",
            "end": "08:00",
            "rating": 3,
        },
    )
    assert response.status_code == 422
    assert "ended_at must be after started_at" in response.json()["detail"]


def test_an_unknown_spot_is_refused(client):
    response = client.post(
        "/api/observations",
        json={
            "spot_id": "not_a_spot",
            "day": to_local(now_utc()).date().isoformat(),
            "start": "07:00",
            "end": "09:00",
            "rating": 3,
        },
    )
    assert response.status_code == 404


def test_a_rating_outside_one_to_five_is_refused(client):
    for rating in (0, 6):
        response = client.post(
            "/api/observations",
            json={
                "spot_id": load_spots()[0].id,
                "day": to_local(now_utc()).date().isoformat(),
                "start": "07:00",
                "end": "09:00",
                "rating": rating,
            },
        )
        assert response.status_code == 422, rating


# --- days and windows -------------------------------------------------------------


def test_the_day_picker_offers_every_day_we_hold(client, forecast):
    body = client.get("/api/days").json()

    assert body["days"] == sorted(body["days"])
    assert body["default"] == body["days"][0] == forecast.isoformat()
    assert body["timezone"] == "Europe/Lisbon"


def test_windows_answers_for_a_chosen_day_not_only_saturday(client, forecast):
    """The whole point of the day parameter: a Tuesday session can be a label too."""
    later = forecast + timedelta(days=1)
    body = client.get(f"/api/windows?day={later.isoformat()}").json()

    assert body["day"] == later.isoformat()
    assert body["windows"]
    assert [w["score"] for w in body["windows"]] == sorted(
        (w["score"] for w in body["windows"]), reverse=True
    )


def test_windows_are_ranges_with_a_peak_inside_them(client):
    for w in client.get("/api/windows").json()["windows"]:
        assert w["starts_at"] < w["ends_at"]
        assert w["starts_at"] <= w["peak_at"] < w["ends_at"]
        assert w["hours"] >= 1
        assert w["region"] in {"ericeira", "lisbon", "peniche"}


def test_a_day_we_have_no_forecast_for_is_a_404(client):
    far = (date.today() + timedelta(days=90)).isoformat()
    assert client.get(f"/api/windows?day={far}").status_code == 404


def test_a_spot_detail_records_no_impression(client):
    """The impression log answers "what did we put in front of him", not "what did he
    browse" — otherwise every tap inflates the record of what we recommended."""

    def impressions() -> int:
        # A fresh connection each time: `with conn` closes it on exit.
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM window_impressions")
            return cur.fetchone()["n"]

    before = impressions()
    detail = client.get(f"/api/spots/{load_spots()[0].id}")
    assert detail.status_code == 200
    assert detail.json()["hours"]
    assert impressions() == before

    # /api/windows, by contrast, is a recommendation and must be recorded.
    client.get("/api/windows")
    assert impressions() > before
