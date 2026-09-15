"""What we knew about an hour, at a chosen moment. The floor the harness stands on.

Everything in Stage 2 reduces to one question asked twice, and the plan keeps them
apart on purpose:

- **Q1, is the score right?** Judge the score against the best estimate of what the
  ocean actually did. Reanalysis is exactly that, and using it here is correct.
- **Q2, was the recommendation right?** Judge it against what was knowable when someone
  had to decide. Reanalysis here is hindsight wearing a lab coat, and it would make
  every number look better than the system ever was.

A *policy* is the difference between those, made explicit and named so a stored
evaluation says which question it answered.

**Why `fetched_at <= as_of` is not on its own enough.** `persist_analysis_hours` stamps
reanalysis with when *we pulled it*, not when it was valid. Backfilling a year in one
afternoon gives a whole year of hours a recent `fetched_at`, so a Q2 query with a recent
as-of would happily return a perfect nowcast of last March. The lead policies therefore
exclude analysis rows outright rather than relying on a timestamp comparison to do it.
Two independent guards, because this is the one mistake that cannot be seen in the
output: the numbers simply come back good.

**Why the last cycle matters as much as the as-of.** Since the payload dedup (007), an
unchanged forecast writes no snapshot, so the newest row before an as-of may be hours
older than the last time we actually looked. Those are different facts. If the worker
was down for twelve hours, a "24 hour lead" was really a 36 hour lead, and a metric that
does not know this credits the score with a freshness it did not have. `fetch_cycles`
(008) is what makes the difference visible, so every feature set carries it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import psycopg

from gogo.assemble import SURFABLE_FROM_HOUR
from gogo.clock import from_local_input
from gogo.ingest.protocol import GridHour
from gogo.models import Spot
from gogo.store import _grid_cells, _hours_from_rows

#: The best estimate of what the hour actually was: the newest thing we hold, reanalysis
#: included. Answers Q1 — is the score's judgement of real conditions any good.
BEST_KNOWN = "best_known"

#: What we knew at 18:00 the evening before. The real decision moment for a dawn
#: session, and the lead time the evening go/no-go message would run at.
EVENING_BEFORE = "evening_before"

#: What we knew a full day before the first surfable hour, so every hour of the day has
#: at least 24 hours of lead. The "should I plan around this" horizon.
LEAD_24H = "lead_24h"

POLICIES = (BEST_KNOWN, EVENING_BEFORE, LEAD_24H)

#: When the evening decision gets made, in local time.
EVENING_HOUR = 18


def as_of_for(policy: str, day: date) -> datetime | None:
    """The instant a policy is allowed to know things up to, for a local day.

    `None` means unbounded, which only `best_known` gets. Everything else resolves to a
    real moment before the day being judged, so the bound is a property of the policy
    rather than something a caller can forget to pass.
    """
    if policy == BEST_KNOWN:
        return None
    if policy == EVENING_BEFORE:
        return from_local_input(day - timedelta(days=1), f"{EVENING_HOUR:02d}:00")
    if policy == LEAD_24H:
        first_hour = from_local_input(day, f"{SURFABLE_FROM_HOUR:02d}:00")
        return first_hour - timedelta(hours=24)
    raise ValueError(f"unknown as-of policy {policy!r}; expected one of {POLICIES}")


@dataclass(frozen=True)
class Features:
    """One day of hours for some spots, as they looked at `as_of`."""

    day: date
    policy: str
    as_of: datetime | None
    hours: list[GridHour]
    #: The last completed fetch at or before `as_of`, from `fetch_cycles`. `None` for
    #: `best_known` (unbounded) or when no cycle is on record for that period.
    last_cycle_at: datetime | None

    @property
    def stale_by(self) -> timedelta | None:
        """How long before the as-of we had last actually looked.

        Zero-ish is healthy. Hours mean the worker was down, and the real lead time was
        longer than the policy's name claims — which is the thing a Q2 metric must not
        silently absorb.
        """
        if self.as_of is None or self.last_cycle_at is None:
            return None
        return self.as_of - self.last_cycle_at


def _last_cycle_at(conn: psycopg.Connection, as_of: datetime | None) -> datetime | None:
    if as_of is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(fetched_at) AS t FROM fetch_cycles WHERE fetched_at <= %s",
            (as_of,),
        )
        row = cur.fetchone()
    return row["t"] if row else None


# One row per (cell, hour): the newest belief that existed at the as-of. DISTINCT ON
# with this ORDER BY is served straight from the 007 index, no sort.
_AS_OF_SQL = """
    SELECT DISTINCT ON (grid_lat, grid_lon, valid_at)
           grid_lat, grid_lon, valid_at, source, payload
    FROM forecast_snapshots
    WHERE valid_at >= %(day_start)s
      AND valid_at < %(day_end)s
      AND (%(allow_analysis)s OR NOT is_analysis)
      AND (%(as_of)s::timestamptz IS NULL OR fetched_at <= %(as_of)s)
    ORDER BY grid_lat, grid_lon, valid_at, fetched_at DESC
"""


def features_for_day(
    conn: psycopg.Connection, spots: list[Spot], day: date, policy: str = BEST_KNOWN
) -> Features:
    """Every hour of one local day for these spots, as known at the policy's as-of.

    Returns the same `GridHour` shape that `load_current_hours` serves, tagged with each
    spot's own coordinates, so the scoring path is identical to the live one. A backtest
    that scored past days through a separate code path would be measuring that path
    rather than the product.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown as-of policy {policy!r}; expected one of {POLICIES}")

    as_of = as_of_for(policy, day)
    cells = _grid_cells(conn)
    with conn.cursor() as cur:
        cur.execute(
            _AS_OF_SQL,
            {
                "day_start": from_local_input(day, "00:00"),
                "day_end": from_local_input(day + timedelta(days=1), "00:00"),
                # Only Q1 may see reanalysis. See the module docstring for why the
                # timestamp bound alone does not cover this.
                "allow_analysis": policy == BEST_KNOWN,
                "as_of": as_of,
            },
        )
        rows = cur.fetchall()

    return Features(
        day=day,
        policy=policy,
        as_of=as_of,
        hours=_hours_from_rows(spots, cells, rows),
        last_cycle_at=_last_cycle_at(conn, as_of),
    )
