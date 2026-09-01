from __future__ import annotations

from fastapi import FastAPI, HTTPException

from gogo import __version__
from gogo.assemble import saturday_morning, score_spots_at
from gogo.spots import load_spots
from gogo.store import connection, load_current_hours

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
    return {
        "when": when.isoformat(),
        "windows": [
            {
                "spot_id": w.spot_id,
                "spot_name": w.spot_name,
                "score": w.score,
                "verdict": w.verdict,
                "vetoed": w.vetoed,
                "reasons": [r.model_dump() for r in w.reasons],
            }
            for w in ranked
        ],
    }
