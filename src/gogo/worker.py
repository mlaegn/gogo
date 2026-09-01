from __future__ import annotations

from gogo.ingest.openmeteo import OpenMeteoSource
from gogo.spots import load_spots
from gogo.store import connection, persist_hours, seed_spots


def fetch_once(forecast_days: int = 7) -> int:
    spots = load_spots()
    src = OpenMeteoSource()
    try:
        hours = src.fetch(spots, forecast_days=forecast_days)
    finally:
        src.close()

    with connection() as conn:
        seed_spots(conn, spots)
        return persist_hours(conn, spots, hours)
