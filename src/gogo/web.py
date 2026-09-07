"""The phone page — the surface people check, and the only practical way labels arrive.

Server-rendered, no build step, no framework: a plain form posts and the page reloads.
For something this size that is fewer moving parts than a client app, and it works on a
cold phone in a car park with one bar of signal.

The one rule that matters here: **the log card never shows our score.** If it told you
what we predicted before asking how it was, the answer would drift toward our number and
the label would be worth nothing. You rate it blind; we reveal afterwards.
"""

from __future__ import annotations

import os
import secrets
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from gogo.clock import from_local_input, now_utc, to_local
from gogo.models import Fault, Observation, WindowScore
from gogo.serving import NoForecast, UnknownDay, serve, serve_spot
from gogo.spots import load_spots
from gogo.store import (
    connection,
    ensure_user,
    impression_for,
    record_observation,
    seed_spots,
)

# Package data, found relative to __file__: an installed wheel has no repo root.
PACKAGE = Path(__file__).parent
TEMPLATE_DIR = PACKAGE / "templates"
STATIC_DIR = PACKAGE / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
router = APIRouter()

COOKIE = "gogo_key"
SECRET_ENV = "GOGO_WEB_SECRET"
# One account until S5b brings real ones. Everything logged here belongs to it.
HANDLE_ENV = "GOGO_WEB_HANDLE"

CROWD_CHOICES = [("", "not saying"), ("empty", "empty"), ("ok", "ok"), ("busy", "busy"), ("zoo", "zoo")]
RATINGS = [(1, "awful"), (2, "poor"), (3, "ok"), (4, "good"), (5, "epic")]
# A fault says which gate we got wrong. The card only offers "worse than you said",
# because that is the direction a person volunteers; `gogo log` can record either.
FAULT_CHOICES = [
    ("size", "smaller / bigger than you said"),
    ("wind", "wind was worse"),
    ("period", "no power"),
    ("swell_dir", "wrong direction"),
    ("tide", "wrong tide"),
]


def _page(request: Request, name: str, context: dict, status: int = 200) -> Response:
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status
    )


def _gate(request: Request) -> Response | None:
    """Whoever holds the link is the user, until S5b.

    No secret configured means the page is **off**, not open. There is deliberately no
    "localhost is exempt" shortcut: behind a reverse proxy the client address is the
    proxy's, so that exemption would hand the page to the whole internet.
    """
    secret = os.environ.get(SECRET_ENV)
    if not secret:
        return _page(request, "off.html", {"env": SECRET_ENV}, status=503)
    if not secrets.compare_digest(request.cookies.get(COOKIE, ""), secret):
        return RedirectResponse("/enter", status_code=303)
    return None


def _day_label(day: date, today: date) -> str:
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    return day.strftime("%a %d")


def _view(w: WindowScore) -> dict:
    """A window as the template wants it: no timezone arithmetic in a template."""
    return {
        "spot_id": w.spot_id,
        "spot_name": w.spot_name,
        "score": w.score,
        "verdict": w.verdict,
        "hours": w.hours,
        "start": f"{to_local(w.starts_at):%H:%M}",
        "end": f"{to_local(w.ends_at):%H:%M}",
        "peak": f"{to_local(w.peak_at):%H:%M}",
        "peak_score": w.peak_score,
        "why": "; ".join(
            r.detail
            for r in w.reasons
            if r.points == 0 or r.code in {"wind", "size", "period"}
        ),
    }


@router.get("/enter", response_class=HTMLResponse)
def enter(request: Request) -> Response:
    if not os.environ.get(SECRET_ENV):
        return _page(request, "off.html", {"env": SECRET_ENV}, status=503)
    return _page(request, "enter.html", {})


@router.post("/enter")
def submit_key(request: Request, key: str = Form(...)) -> Response:
    secret = os.environ.get(SECRET_ENV)
    if not secret:
        return _page(request, "off.html", {"env": SECRET_ENV}, status=503)
    if not secrets.compare_digest(key.strip(), secret):
        return _page(request, "enter.html", {"error": "Not that one."}, status=403)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE,
        secret,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/", response_class=HTMLResponse)
def index(request: Request, day: date | None = None) -> Response:
    if (blocked := _gate(request)) is not None:
        return blocked
    try:
        # The main screen is a recommendation, so it is recorded.
        served = serve(day=day, surface="web")
    except (NoForecast, UnknownDay) as exc:
        return _page(request, "empty.html", {"message": str(exc)}, status=503)

    today = to_local(now_utc()).date()
    windows = [_view(w) for w in served.windows]
    best = served.best
    return _page(
        request,
        "index.html",
        {
            "day": served.day,
            "day_label": _day_label(served.day, today),
            "tabs": [
                {
                    "iso": d.isoformat(),
                    "label": _day_label(d, today),
                    "current": d == served.day,
                }
                for d in served.days
            ],
            "best": _view(best) if best else None,
            "rest": [w for w in windows if not best or w["spot_id"] != best.spot_id],
            "as_of": f"{to_local(served.as_of):%a %H:%M}" if served.as_of else None,
        },
    )


@router.get("/spot/{spot_id}", response_class=HTMLResponse)
def spot_detail(request: Request, spot_id: str, day: date | None = None) -> Response:
    if (blocked := _gate(request)) is not None:
        return blocked
    spot = next((s for s in load_spots() if s.id == spot_id), None)
    if spot is None:
        return _page(request, "empty.html", {"message": f"No spot {spot_id!r}."}, status=404)
    try:
        # A detail view is not a recommendation, so nothing is recorded: the impression
        # log answers "what did we put in front of him", not "what did he browse".
        served = serve(day=day, surface=None)
    except (NoForecast, UnknownDay) as exc:
        return _page(request, "empty.html", {"message": str(exc)}, status=503)

    hours = serve_spot(spot, served.day)
    return _page(
        request,
        "spot.html",
        {
            "spot": spot,
            "day": served.day,
            "day_label": _day_label(served.day, to_local(now_utc()).date()),
            "hours": [
                {
                    "at": f"{to_local(h.valid_at):%H:%M}",
                    "score": h.score,
                    "verdict": h.verdict,
                    "why": "; ".join(r.detail for r in h.reasons),
                }
                for h in hours
            ],
        },
    )


def _log_form(request: Request, prefill: dict, error: str | None = None, status: int = 200) -> Response:
    return _page(
        request,
        "log.html",
        {
            "spots": load_spots(),
            "ratings": RATINGS,
            "crowds": CROWD_CHOICES,
            "faults": FAULT_CHOICES,
            "prefill": prefill,
            "error": error,
        },
        status=status,
    )


@router.get("/log", response_class=HTMLResponse)
def log_form(
    request: Request,
    spot: str | None = None,
    day: date | None = None,
    start: str | None = None,
    end: str | None = None,
) -> Response:
    if (blocked := _gate(request)) is not None:
        return blocked
    local_now = to_local(now_utc())
    return _log_form(
        request,
        {
            "spot": spot or "",
            "day": (day or local_now.date()).isoformat(),
            # Defaults that suit the car park: a session that just ended.
            "start": start or f"{(local_now - timedelta(hours=2)):%H:%M}",
            "end": end or f"{local_now:%H:%M}",
            "kind": "surfed",
        },
    )


@router.post("/log", response_class=HTMLResponse)
def save_log(
    request: Request,
    spot: str = Form(...),
    day: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    rating: int = Form(...),
    kind: str = Form("surfed"),
    would_return: str = Form(""),
    crowd: str = Form(""),
    note: str = Form(""),
    faults: list[str] = Form(default=[]),
) -> Response:
    if (blocked := _gate(request)) is not None:
        return blocked

    prefill = {
        "spot": spot, "day": day, "start": start, "end": end,
        "kind": kind, "rating": rating, "note": note,
    }
    try:
        session_day = date.fromisoformat(day)
        started_at = from_local_input(session_day, start)
        ended_at = from_local_input(session_day, end)
    except ValueError as exc:
        return _log_form(request, prefill, f"Check the date and times: {exc}", status=400)

    if not any(s.id == spot for s in load_spots()):
        return _log_form(request, prefill, f"Unknown spot {spot!r}.", status=400)

    with connection() as conn:
        seed_spots(conn, load_spots())
        # Anchored means our recommendation was on screen for this session. Only an
        # impression proves that, and only anchored rows can answer "would we have told
        # you the right thing"; the rest still answer "was it any good".
        shown = impression_for(conn, spot, started_at, ended_at)
        try:
            observation = Observation(
                spot_id=spot,
                kind=kind,
                started_at=started_at,
                ended_at=ended_at,
                rating=rating,
                would_return={"yes": True, "no": False}.get(would_return),
                crowd=crowd or None,
                note=note.strip() or None,
                faults=[Fault(code=code, direction=-1) for code in faults],
                anchored=shown is not None,
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            return _log_form(request, prefill, first.get("msg", "That does not add up."), status=400)

        user_id = ensure_user(conn, os.environ.get(HANDLE_ENV, "max"))
        observation_id = record_observation(conn, user_id, observation)

    return _page(
        request,
        "logged.html",
        {
            "spot_name": next(s.name for s in load_spots() if s.id == spot),
            "duplicate": observation_id is None,
            "rating": rating,
            "rating_word": dict(RATINGS)[rating],
            "span": f"{start}–{end}",
            "day": session_day,
            # Safe to reveal now: the label is already stored.
            "shown": shown,
        },
    )


@router.get("/manifest.webmanifest")
def manifest() -> JSONResponse:
    """Enough for "Add to Home Screen" to behave like an app."""
    return JSONResponse(
        {
            "name": "Gogo",
            "short_name": "Gogo",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#0b1622",
            "theme_color": "#0b1622",
            "icons": [],
        },
        media_type="application/manifest+json",
    )
