from __future__ import annotations

from fastapi import FastAPI, HTTPException

from gogo import __version__
from gogo.assemble import plan_day, windows_for_day
from gogo.clock import LISBON, now_utc, to_local
from gogo.score import SCORE_VERSION
from gogo.spots import load_spots
from gogo.store import (
    connection,
    current_as_of,
    load_current_hours,
    record_impressions,
)
from gogo.versioning import spec_version

app = FastAPI(title="gogo", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "gogo"}


@app.get("/windows")
def windows() -> dict:
    spots = load_spots()
    with connection() as conn:
        hours = load_current_hours(conn, spots)
        if not hours:
            raise HTTPException(
                status_code=503, detail="No stored forecasts. Run gogo fetch."
            )
        day = plan_day(hours, not_before=now_utc())
        if day is None:
            raise HTTPException(status_code=503, detail="No upcoming hours to score.")
        ranked = windows_for_day(spots, hours, day)
        as_of = current_as_of(conn)
        if as_of is not None:
            record_impressions(conn, ranked, spots, as_of, surface="api")
    versions = {spot.id: spec_version(spot) for spot in spots}
    return {
        "day": day.isoformat(),
        "timezone": LISBON.key,
        "score_version": SCORE_VERSION,
        "windows": [
            {
                "spot_id": w.spot_id,
                "spot_name": w.spot_name,
                "spec_version": versions.get(w.spot_id),
                # Both, deliberately: UTC is what the client should compute with, local
                # is what it should display, and deriving one from the other in a
                # browser is where timezone bugs come from.
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
            for w in ranked
        ],
    }
