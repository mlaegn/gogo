from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable, Iterator
from datetime import date, timedelta

from gogo.ingest.archive import ArchiveSource
from gogo.ingest.openmeteo import OpenMeteoSource
from gogo.spots import load_spots
from gogo.store import (
    connection,
    load_current_hours,
    persist_analysis_hours,
    persist_hours,
    seed_spots,
)

log = logging.getLogger("gogo.worker")


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


def spots_without_hours() -> list[str]:
    """Spots holding no servable hours. Empty is the only healthy answer.

    This exists because it already happened: Ribeira's `spot_grid` row was overwritten
    with rounded test coordinates, the spot silently left every ranking for four days,
    and nothing complained. A ranking that is quietly missing a spot looks completely
    normal — there is no error to see, just one fewer row.
    """
    spots = load_spots()
    with connection() as conn:
        hours = load_current_hours(conn, spots)
    have = {(h.requested_lat, h.requested_lon) for h in hours}
    return [s.id for s in spots if (s.lat, s.lon) not in have]


def run_forever(
    interval_s: int = 3600,
    forecast_days: int = 7,
    stop: threading.Event | None = None,
    fetch: Callable[[int], int] = fetch_once,
    check: Callable[[], list[str]] = spots_without_hours,
) -> int:
    """Fetch on a loop until asked to stop. Returns the number of successful cycles.

    Why a loop is worth more than it looks: every run writes a `forecast_snapshots` row
    stamped with `fetched_at`, and that history is **as unrecoverable as a label**. The
    archive can tell us what the ocean did last Tuesday, but nothing can reconstruct
    what the forecast *said* on the Monday. Q2 — "would we have called it right at the
    time" — is answered entirely from those stamps, so a day the worker did not run is a
    permanent hole in the only dataset that can answer it. That is true even with zero
    users, which is why this comes before hosting rather than after.

    A failed cycle must never end the loop. Open-Meteo has bad minutes, and a worker
    that exits on the first timeout is indistinguishable from one that was never
    started — except that it looks like it worked for a while.
    """
    stop = stop or threading.Event()
    cycles = 0
    backoff = 0

    while not stop.is_set():
        try:
            written = fetch(forecast_days)
            cycles += 1
            backoff = 0
            missing = check()
            if missing:
                # Loud, because the failure mode is silence.
                log.error("spots with no hours after fetch: %s", ", ".join(missing))
            log.info("cycle %d: %d grid-hours stored", cycles, written)
        except Exception:
            # Exponential up to the normal interval, then just keep trying at that rate.
            backoff = min(interval_s, 60 if backoff == 0 else backoff * 2)
            log.exception("fetch failed; retrying in %ds", backoff)
            stop.wait(backoff)
            continue

        stop.wait(interval_s)

    log.info("stopped after %d successful cycles", cycles)
    return cycles


def install_signal_handlers(stop: threading.Event) -> None:
    """SIGINT/SIGTERM finish the current sleep instead of killing mid-write.

    A container gets SIGTERM on every deploy, and a fetch interrupted between the
    snapshot insert and the `forecast_current` upsert is the kind of half-write that is
    tedious to reason about later.
    """

    def handle(signum, _frame):
        log.info("signal %s: finishing this cycle", signal.Signals(signum).name)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle)


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
