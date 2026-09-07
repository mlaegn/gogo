"""S5c — the page, and the properties that make its labels trustworthy.

The headline test here is `test_the_log_card_never_shows_our_score`. The rest is
plumbing; that one is the reason the labels are worth collecting at all.
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
    """Two future days of flat conditions, so the page has something real to render.

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


def test_without_a_secret_the_page_is_off_not_open(monkeypatch):
    """An unset secret must never mean "let everyone in"."""
    monkeypatch.delenv(SECRET_ENV, raising=False)
    with TestClient(app) as anon:
        response = anon.get("/", follow_redirects=False)
    assert response.status_code == 503
    assert "page is off" in response.text


def test_the_page_is_private(locked):
    assert locked.get("/", follow_redirects=False).status_code == 303
    assert locked.get("/log", follow_redirects=False).status_code == 303
    assert locked.post("/enter", data={"key": "wrong"}).status_code == 403


def test_the_right_key_gets_a_cookie_and_the_page(locked):
    response = locked.post("/enter", data={"key": KEY}, follow_redirects=False)
    assert response.status_code == 303
    assert locked.cookies.get(COOKIE) == KEY
    assert locked.get("/").status_code == 200


def test_the_main_screen_leads_with_one_window(client, forecast):
    page = client.get("/")
    assert page.status_code == 200
    assert "Tomorrow" in page.text
    # The headline is a range, not an instant: an hour on its own would not have a dash.
    assert "–" in page.text


def test_the_log_card_never_shows_our_score(client):
    """Ask before revealing, or the answer drifts toward our number.

    Asserted against the rendered HTML rather than trusting the template to stay honest.
    """
    top = client.get("/windows").json()["windows"][0]
    card = client.get(f"/log?spot={top['spot_id']}")

    assert card.status_code == 200
    assert "How were the waves?" in card.text
    assert top["reasons"][0]["detail"] not in card.text
    if top["score"] >= 10:  # a single digit could collide with a rating button
        assert f">{top['score']}<" not in card.text


def test_the_reveal_comes_after_the_label_is_stored(client, forecast):
    """Showing our number on the confirmation page is safe and is the reward for logging."""
    top = client.get("/windows").json()["windows"][0]
    saved = client.post(
        "/log",
        data={
            "spot": top["spot_id"],
            "day": forecast.isoformat(),
            "start": top["starts_at_local"][11:16],
            "end": top["ends_at_local"][11:16],
            "rating": "4",
        },
    )
    assert saved.status_code == 200
    assert "Saved" in saved.text
    assert "We had said" in saved.text
    assert str(top["score"]) in saved.text


def test_a_session_logged_from_the_page_lands_and_is_idempotent(client):
    spot = load_spots()[0]
    form = {
        "spot": spot.id,
        "day": (to_local(now_utc()).date() - timedelta(days=3)).isoformat(),
        "start": "07:00",
        "end": "09:00",
        "rating": "4",
        "kind": "surfed",
        "would_return": "yes",
        "crowd": "ok",
        "note": "logged by a test",
        "faults": ["wind"],
    }

    first = client.post("/log", data=form)
    assert first.status_code == 200
    assert "Saved" in first.text
    # Nothing was on screen three days ago, so the label is honest about not being paired.
    assert "nothing on screen" in first.text

    # 004 says one session is one label, and the page must not be a way around it.
    again = client.post("/log", data=form)
    assert again.status_code == 200
    assert "Already logged" in again.text


def test_a_backwards_session_is_refused_with_a_readable_message(client):
    response = client.post(
        "/log",
        data={
            "spot": load_spots()[0].id,
            "day": to_local(now_utc()).date().isoformat(),
            "start": "10:00",
            "end": "08:00",
            "rating": "3",
        },
    )
    assert response.status_code == 400
    assert "ended_at must be after started_at" in response.text


def test_an_unknown_spot_is_refused(client):
    response = client.post(
        "/log",
        data={
            "spot": "not_a_spot",
            "day": to_local(now_utc()).date().isoformat(),
            "start": "07:00",
            "end": "09:00",
            "rating": "3",
        },
    )
    assert response.status_code == 400
    assert "Unknown spot" in response.text


def test_the_day_picker_offers_every_day_we_hold(client, forecast):
    body = client.get("/days").json()

    assert body["days"] == sorted(body["days"])
    assert body["default"] == body["days"][0] == forecast.isoformat()
    assert body["timezone"] == "Europe/Lisbon"
    assert client.get(f"/?day={body['days'][-1]}").status_code == 200


def test_windows_answers_for_a_chosen_day_not_only_saturday(client, forecast):
    """The whole point of the day parameter: a Tuesday session can be a label too."""
    later = forecast + timedelta(days=1)
    body = client.get(f"/windows?day={later.isoformat()}").json()
    assert body["day"] == later.isoformat()
    assert body["windows"]


def test_a_day_we_have_no_forecast_for_is_a_404(client):
    far = (date.today() + timedelta(days=90)).isoformat()
    assert client.get(f"/windows?day={far}").status_code == 404


def test_a_detail_view_records_no_impression(client):
    """The impression log answers "what did we put in front of him", not "what did he
    browse" — otherwise every tap inflates the record of what we recommended."""
    def impressions() -> int:
        # A fresh connection each time: `with conn` closes it on exit.
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM window_impressions")
            return cur.fetchone()["n"]

    before = impressions()
    assert client.get(f"/spot/{load_spots()[0].id}").status_code == 200
    assert impressions() == before

    # The main screen, by contrast, is a recommendation and must be recorded.
    client.get("/")
    assert impressions() > before
