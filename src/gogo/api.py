from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from gogo import __version__
from gogo.clock import LISBON, to_local
from gogo.models import WindowScore
from gogo.score import SCORE_VERSION
from gogo.serving import NoForecast, Served, UnknownDay, serve
from gogo.versioning import spec_version
from gogo.web import STATIC_DIR, router as web_router

app = FastAPI(title="gogo", version=__version__)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "gogo"}


def _window_json(w: WindowScore, versions: dict[str, str]) -> dict:
    return {
        "spot_id": w.spot_id,
        "spot_name": w.spot_name,
        "spec_version": versions.get(w.spot_id),
        # Both, deliberately: UTC is what the client should compute with, local is what
        # it should display, and deriving one from the other in a browser is where
        # timezone bugs come from.
        "starts_at": w.starts_at.isoformat(),
        "ends_at": w.ends_at.isoformat(),
        "starts_at_local": to_local(w.starts_at).isoformat(),
        "ends_at_local": to_local(w.ends_at).isoformat(),
        "peak_at": w.peak_at.isoformat(),
        "peak_at_local": to_local(w.peak_at).isoformat(),
        "hours": w.hours,
        "score": w.score,
        "peak_score": w.peak_score,
        "verdict": w.verdict,
        "vetoed": w.vetoed,
        "reasons": [r.model_dump() for r in w.reasons],
    }


def _served(day: date | None, surface: str) -> Served:
    try:
        return serve(day=day, surface=surface)
    except NoForecast as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnknownDay as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/days")
def days() -> dict:
    """Local days we can answer for. The page's day picker reads this."""
    served = _served(None, surface="api")
    return {
        "timezone": LISBON.key,
        "default": served.day.isoformat(),
        "days": [d.isoformat() for d in served.days],
        "as_of": served.as_of.isoformat() if served.as_of else None,
    }


@app.get("/windows")
def windows(
    day: date | None = Query(
        default=None,
        description="Local (Europe/Lisbon) day. Defaults to the next day with surfable hours.",
    ),
) -> dict:
    served = _served(day, surface="api")
    versions = {spot.id: spec_version(spot) for spot in served.spots}
    return {
        "day": served.day.isoformat(),
        "days": [d.isoformat() for d in served.days],
        "timezone": LISBON.key,
        "score_version": SCORE_VERSION,
        "as_of": served.as_of.isoformat() if served.as_of else None,
        "windows": [_window_json(w, versions) for w in served.windows],
    }
