from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from gogo import __version__
from gogo.almanac import (
    SunTimes,
    TurningPoint,
    day_class,
    day_tides,
    station_for,
    sun_times,
    tide_nudges,
)
from gogo.clock import LISBON, from_local_input, to_local
from gogo.models import Fault, Observation, Spot, WindowScore
from gogo.schemas import (
    DaylightOut,
    DaysOut,
    HourOut,
    ObservationIn,
    ObservationOut,
    ShownOut,
    SpotAlmanacOut,
    SpotDayOut,
    SpotDetailOut,
    SpotOut,
    SwellOut,
    TideNudgeOut,
    TideOut,
    WindowOut,
    WindowsOut,
)
from gogo.score import SCORE_VERSION
from gogo.serving import NoForecast, Served, UnknownDay, serve, serve_spot
from gogo.spots import load_spots
from gogo.store import (
    connection,
    default_handle,
    ensure_user,
    impression_for,
    record_observation,
    seed_spots,
)
from gogo.versioning import spec_version
from gogo.web import STATIC_DIR, require_key
from gogo.web import router as web_router

app = FastAPI(title="gogo", version=__version__)

# Everything under /api needs the cookie. Not for secrecy of surf forecasts, but because
# a request to /api/windows writes a row to window_impressions — an open endpoint would
# let anyone fill the record of what we supposedly recommended.
api = APIRouter(prefix="/api", dependencies=[Depends(require_key)])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "gogo"}


def _served(day: date | None, surface: str | None) -> Served:
    try:
        return serve(day=day, surface=surface)
    except NoForecast as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnknownDay as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _window_out(
    w: WindowScore, versions: dict[str, str], regions: dict[str, str]
) -> WindowOut:
    return WindowOut(
        spot_id=w.spot_id,
        spot_name=w.spot_name,
        region=regions[w.spot_id],
        spec_version=versions.get(w.spot_id),
        starts_at=w.starts_at,
        ends_at=w.ends_at,
        starts_at_local=to_local(w.starts_at),
        ends_at_local=to_local(w.ends_at),
        peak_at=w.peak_at,
        peak_at_local=to_local(w.peak_at),
        hours=w.hours,
        score=w.score,
        peak_score=w.peak_score,
        verdict=w.verdict,
        vetoed=w.vetoed,
        reasons=[r.model_dump() for r in w.reasons],
    )


def _daylight_out(sun: SunTimes) -> DaylightOut:
    return DaylightOut(
        civil_dawn=sun.civil_dawn,
        civil_dawn_local=to_local(sun.civil_dawn),
        sunrise_local=to_local(sun.sunrise),
        sunset_local=to_local(sun.sunset),
        civil_dusk=sun.civil_dusk,
        civil_dusk_local=to_local(sun.civil_dusk),
    )


def _tide_out(p: TurningPoint) -> TideOut:
    return TideOut(
        at=p.at, at_local=to_local(p.at), kind=p.kind, height_m=p.height_cd_m, king=p.king
    )


def _coast_daylight(spots: list[Spot], day: date) -> DaylightOut | None:
    """The widest light along the coast, so every spot's window fits the strip."""
    if not spots:
        return None
    suns = [sun_times(day, s.lat, s.lon) for s in spots]
    return _daylight_out(
        SunTimes(
            civil_dawn=min(x.civil_dawn for x in suns),
            sunrise=min(x.sunrise for x in suns),
            sunset=max(x.sunset for x in suns),
            civil_dusk=max(x.civil_dusk for x in suns),
        )
    )


def _nudges_out(spots: list[Spot], day: date) -> list[TideNudgeOut]:
    names = {s.id: s for s in spots}
    return [
        TideNudgeOut(
            kind=n.kind,
            tides=[_tide_out(p) for _, p in n.points],
            stations=[st.name for st, _ in n.points],
            spots=[
                SpotOut(id=i, name=names[i].name, region=names[i].region) for i in n.spot_ids
            ],
        )
        for n in tide_nudges(spots, day)
    ]


def _spot_almanac(spot: Spot, day: date) -> SpotAlmanacOut:
    station = station_for(spot.lat, spot.lon)
    return SpotAlmanacOut(
        station=station.name,
        daylight=_daylight_out(sun_times(day, spot.lat, spot.lon)),
        tides=[_tide_out(p) for p in day_tides(station, day)],
        tide_class=day_class(station, day),
    )


def _windows_out(served: Served) -> WindowsOut:
    versions = {spot.id: spec_version(spot) for spot in served.spots}
    regions = {spot.id: spot.region for spot in served.spots}
    return WindowsOut(
        day=served.day,
        days=served.days,
        timezone=LISBON.key,
        score_version=SCORE_VERSION,
        as_of=served.as_of,
        windows=[_window_out(w, versions, regions) for w in served.windows],
        daylight=_coast_daylight(served.spots, served.day),
        nudges=_nudges_out(served.spots, served.day),
    )


@api.get("/days")
def days() -> DaysOut:
    # No impression: this answers "which days do you hold", not "here is where to go".
    # Recording one would log a ranking for a day nobody looked at, and — because
    # `anchored` is set from any overlapping impression — would let a day-list request
    # mark later sessions as having been recommended to. That empties the unanchored
    # control slice of its meaning.
    served = _served(None, surface=None)
    return DaysOut(
        timezone=LISBON.key,
        default=served.day,
        days=served.days,
        as_of=served.as_of,
    )


@api.get("/windows")
def windows(
    day: date | None = Query(
        default=None,
        description="Local (Europe/Lisbon) day. Defaults to the next day with surfable hours.",
    ),
) -> WindowsOut:
    return _windows_out(_served(day, surface="api"))


@api.get("/spots")
def spots() -> list[SpotOut]:
    """The log screen's only data source, and it carries no scores.

    This is the architectural half of "ask before revealing": there is no number in this
    response to leak, so hiding it is not left to a component.
    """
    return [SpotOut(id=s.id, name=s.name, region=s.region) for s in load_spots()]


def _find_spot(spot_id: str) -> Spot:
    spot = next((s for s in load_spots() if s.id == spot_id), None)
    if spot is None:
        raise HTTPException(status_code=404, detail=f"No spot {spot_id!r}")
    return spot


@api.get("/spots/{spot_id}")
def spot_day(spot_id: str, day: date | None = None) -> SpotDayOut:
    spot = _find_spot(spot_id)
    # A detail view is not a recommendation, so nothing is recorded: the impression log
    # answers "what did we put in front of him", not "what did he browse".
    served = _served(day, surface=None)
    return SpotDayOut(
        spot=SpotDetailOut(**spot.model_dump()),
        day=served.day,
        hours=[
            HourOut(
                valid_at=h.valid_at,
                at_local=f"{to_local(h.valid_at):%H:%M}",
                score=h.score,
                verdict=h.verdict,
                reasons=[r.model_dump() for r in h.reasons],
                swell=SwellOut(
                    height_m=f.swell_height_m,
                    period_s=f.swell_period_s,
                    peak_period_s=f.swell_peak_period_s,
                    from_deg=f.swell_from_deg,
                ),
                tide=f.tide,
            )
            for f, h in serve_spot(spot, served.day)
        ],
        almanac=_spot_almanac(spot, served.day),
    )


@api.post("/observations")
def create_observation(body: ObservationIn) -> ObservationOut:
    """Store one label, then reveal what we had said.

    The order is the point. Nothing in the request tells the client our score, and the
    reveal only exists in this response — by which time the rating is already stored and
    cannot be influenced by it.
    """
    spot = _find_spot(body.spot_id)
    try:
        started_at = from_local_input(body.day, body.start)
        ended_at = from_local_input(body.day, body.end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Check the times: {exc}") from exc

    with connection() as conn:
        seed_spots(conn, load_spots())
        # Anchored means our recommendation really was on screen for this interval. Only
        # an impression proves that, and only anchored rows can answer "would we have
        # told you the right thing"; the rest still answer "was it any good".
        shown = impression_for(conn, spot.id, started_at, ended_at)
        try:
            observation = Observation(
                spot_id=spot.id,
                kind=body.kind,
                started_at=started_at,
                ended_at=ended_at,
                rating=body.rating,
                would_return=body.would_return,
                crowd=body.crowd,
                note=(body.note or "").strip() or None,
                faults=[Fault(code=code, direction=-1) for code in body.faults],
                anchored=shown is not None,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail=exc.errors()[0].get("msg", "That does not add up.")
            ) from exc

        user_id = ensure_user(conn, default_handle())
        observation_id = record_observation(conn, user_id, observation)

    return ObservationOut(
        spot_name=spot.name,
        day=body.day,
        span=f"{body.start}–{body.end}",
        rating=body.rating,
        duplicate=observation_id is None,
        shown=(
            ShownOut(
                score=shown["score"],
                verdict=shown["verdict"],
                score_version=shown["score_version"],
                spec_version=shown["spec_version"],
            )
            if shown
            else None
        ),
    )


app.include_router(api)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Last: web_router ends in a catch-all that hands unknown paths to the client router.
app.include_router(web_router)
