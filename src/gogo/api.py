from __future__ import annotations

from fastapi import FastAPI, HTTPException

from gogo import __version__
from gogo.assemble import saturday_morning, score_spots_at
from gogo.clock import to_local
from gogo.score import SCORE_VERSION
from gogo.spots import load_spots
from gogo.store import connection, load_current_hours
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
        raise HTTPException(status_code=503, detail="No stored forecasts. Run gogo fetch.")
    when = saturday_morning(hours)
    if when is None:
        raise HTTPException(status_code=503, detail="No hours to score.")
    ranked = score_spots_at(spots, hours, when)
    versions = {spot.id: spec_version(spot) for spot in spots}
    return {
        "when": when.isoformat(),
        "when_local": to_local(when).isoformat(),
        "score_version": SCORE_VERSION,
        "windows": [
            {
                "spot_id": w.spot_id,
                "spot_name": w.spot_name,
                "spec_version": versions.get(w.spot_id),
                "score": w.score,
                "verdict": w.verdict,
                "vetoed": w.vetoed,
                "reasons": [r.model_dump() for r in w.reasons],
            }
            for w in ranked
        ],
    }
