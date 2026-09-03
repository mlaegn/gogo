from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, timedelta

from gogo.ingest.archive import ArchiveSource
from gogo.ingest.openmeteo import OpenMeteoSource
from gogo.spots import load_spots
from gogo.store import (
    connection,
    persist_analysis_hours,
    persist_hours,
    seed_spots,
)


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


def date_chunks(start: date, end: date, days: int) -> Iterator[tuple[date, date]]:
    """Split an inclusive range into inclusive chunks of at most `days`."""
    if days < 1:
        raise ValueError(f"chunk must be at least one day: {days}")
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=days - 1), end)
        yield cursor, stop
        cursor = stop + timedelta(days=1)


def backfill(
    start: date,
    end: date,
    chunk_days: int = 31,
    on_chunk: Callable[[date, date, int], None] | None = None,
) -> int:
    """Pull reanalysis for `start`..`end` inclusive into snapshots.

    Chunked so a year-long range makes visible progress and commits as it goes: a run
    that dies halfway leaves the completed months stored, and re-running skips them.
    """
    spots = load_spots()
    src = ArchiveSource()
    total = 0
    try:
        with connection() as conn:
            seed_spots(conn, spots)
            for chunk_start, chunk_end in date_chunks(start, end, chunk_days):
                hours = src.fetch(spots, chunk_start, chunk_end)
                written = persist_analysis_hours(conn, spots, hours)
                total += written
                if on_chunk:
                    on_chunk(chunk_start, chunk_end, written)
    finally:
        src.close()
    return total
