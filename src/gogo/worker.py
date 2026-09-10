from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from gogo.clock import now_utc, to_local
from gogo.ingest.archive import ArchiveSource
from gogo.ingest.openmeteo import OpenMeteoSource
from gogo.ingest.protocol import GridHour
from gogo.models import Spot
from gogo.spots import load_spots
from gogo.store import (
    Written,
    connection,
    current_as_of,
    load_current_hours,
    persist_analysis_hours,
    persist_hours,
    prune_current,
    seed_spots,
)

log = logging.getLogger("gogo.worker")


def fetch_once(forecast_days: int = 7) -> Written:
    spots = load_spots()
    src = OpenMeteoSource()
    try:
        hours = src.fetch(spots, forecast_days=forecast_days)
    finally:
        src.close()

    with connection() as conn:
        seed_spots(conn, spots)
        written = persist_hours(conn, spots, hours)
        # Serving keeps only hours still ahead of us. Pruning here rather than on a
        # separate schedule means the table is bounded by the same process that fills
        # it, with no second thing to remember to run.
        prune_current(conn)
    return written


def _missing(spots: list[Spot], hours: list[GridHour]) -> list[str]:
    have = {(h.requested_lat, h.requested_lon) for h in hours}
    return [s.id for s in spots if (s.lat, s.lon) not in have]


def spots_without_hours() -> list[str]:
    """Spots holding no servable hours. Empty is the only healthy answer.

    This exists because it already happened: Ribeira's `spot_grid` row was overwritten
    with rounded test coordinates, the spot silently left every ranking for four days,
    and nothing complained. A ranking that is quietly missing a spot looks completely
    normal — there is no error to see, just one fewer row.
    """
    spots = load_spots()
    with connection() as conn:
        return _missing(spots, load_current_hours(conn, spots))


#: How stale the served forecast may get before something is wrong. Two hours tolerates
#: one missed hourly cycle and catches two, which is the point where a transient bad
#: minute at Open-Meteo has become a worker that is not running.
MAX_AGE_S = 7200


@dataclass(frozen=True)
class Health:
    """Both ways this system fails quietly, in one answer.

    A worker that has stopped and a ranking that is missing a spot produce no error and
    no empty page. The first shows up only as a timestamp that stopped moving, and the
    second only as one fewer row in a list nobody counts. Neither is visible from the
    outside unless something asks, so this is the thing that asks.
    """

    as_of: datetime | None
    age_s: float | None
    max_age_s: int
    missing: list[str]

    @property
    def stale(self) -> bool:
        return self.age_s is None or self.age_s > self.max_age_s

    @property
    def ok(self) -> bool:
        return not self.stale and not self.missing

    def lines(self, spot_count: int) -> list[str]:
        if self.as_of is None or self.age_s is None:
            freshness = "forecast: nothing stored — no cycle has ever completed"
        else:
            freshness = (
                f"forecast: {'STALE' if self.stale else 'fresh'}, "
                f"{self.age_s / 60:.0f} min old "
                f"(as of {to_local(self.as_of):%Y-%m-%d %H:%M} Lisbon, "
                f"limit {self.max_age_s // 60} min)"
            )
        coverage = (
            f"spots: NO HOURS for {', '.join(self.missing)}"
            if self.missing
            else f"spots: all {spot_count} have servable hours"
        )
        return [freshness, coverage]


HEARTBEAT_ENV = "GOGO_HEARTBEAT_URL"


def heartbeat(url: str | None = None) -> bool:
    """Tell a dead-man's switch we are fine. Only when we actually are.

    Hung off `gogo health` rather than off the fetch loop, and that is the whole design.
    The container healthcheck already runs `gogo health` every few minutes, so making it
    the sender means the alarm covers every way this can stop, not only the ones the
    worker survives long enough to notice. A wedged loop, a killed container, a full
    disk, a dead box: all of them stop the pings identically, and not one of them could
    have sent a message about itself.

    Never changes the exit code. A monitoring service being unreachable is not the surf
    forecast being wrong, and a healthcheck that fails for that reason would restart the
    wrong thing.
    """
    url = url or os.environ.get(HEARTBEAT_ENV) or None
    if not url:
        return False
    try:
        httpx.get(url, timeout=10).raise_for_status()
        return True
    except Exception:
        log.warning("heartbeat to %s failed; health itself is unaffected", url)
        return False


def health(max_age_s: int = MAX_AGE_S) -> Health:
    """Is the forecast fresh, and is every spot still in the ranking?

    Read-only and cheap, so it is safe as a container healthcheck, a cron line, or
    something to type over SSH when you want to know whether the box is doing its job.
    """
    spots = load_spots()
    with connection() as conn:
        as_of = current_as_of(conn)
        hours = load_current_hours(conn, spots)
    age = (now_utc() - as_of).total_seconds() if as_of else None
    return Health(
        as_of=as_of, age_s=age, max_age_s=max_age_s, missing=_missing(spots, hours)
    )


def run_forever(
    interval_s: int = 3600,
    forecast_days: int = 7,
    stop: threading.Event | None = None,
    fetch: Callable[[int], Written] = fetch_once,
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
            # Both numbers, because "stored a lot" and "learned anything" differ. An
            # appended of 0 between model runs is the loop working, not the loop idle.
            log.info("cycle %d: %s", cycles, written)
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
