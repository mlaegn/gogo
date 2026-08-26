from __future__ import annotations

from fastapi import FastAPI

from gogo import __version__

app = FastAPI(title="gogo", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "gogo"}
