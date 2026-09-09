from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from gogo.clock import LISBON, UTC, to_utc
from gogo.ingest.protocol import GridHour
from gogo.models import Observation, Spot, WindowScore
from gogo.score import SCORE_VERSION
from gogo.settings import Settings
from gogo.versioning import spec_version


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or Settings().database_url, row_factory=dict_row)


@contextmanager
def connection(url: str | None = None) -> Iterator[psycopg.Connection]:
    with connect(url) as conn:
        yield conn


def seed_spots(conn: psycopg.Connection, spots: list[Spot]) -> None:
    sql = """
        INSERT INTO spots (id, name, lat, lon, region, spec, spec_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            lat = EXCLUDED.lat,
            lon = EXCLUDED.lon,
            region = EXCLUDED.region,
            spec = EXCLUDED.spec,
            spec_version = EXCLUDED.spec_version
    """
    with conn.cursor() as cur:
        for spot in spots:
            cur.execute(
                sql,
                (
                    spot.id,
                    spot.name,
                    spot.lat,
                    spot.lon,
                    spot.region,
                    Jsonb(spot.model_dump()),
                    spec_version(spot),
                ),
            )
    conn.commit()


@dataclass(frozen=True)
class Written:
    """What one fetch actually changed.

    Two numbers because they answer different questions and usually disagree.
    `current` is how many servable hours we now hold, which is what freshness means.
    `appended` is how many of them were news — a belief we had not recorded before.
    A cycle with a healthy `current` and an `appended` of zero is working perfectly and
    storing nothing, which is the normal case between model runs.
    """

    current: int
    appended: int


def persist_hours(
    conn: psycopg.Connection,
    spots: list[Spot],
    hours: list[GridHour],
    fetched_at: datetime | None = None,
) -> Written:
    """Append changed snapshots, upsert current by grid, remember each spot's cell."""
    fetched_at = to_utc(fetched_at or datetime.now(UTC))
    if not hours:
        return Written(current=0, appended=0)

    # Append only when this is a different belief from the last one we recorded for the
    # same cell-hour. Polling hourly a model that updates a few times a day writes the
    # identical payload over and over: two cycles 23 minutes apart in September produced
    # 1176 rows and 1176 unchanged payloads.
    #
    # This is lossless for the question snapshots exist to answer. An as-of query takes
    # the newest row with `fetched_at <= T`, and skipping an unchanged repeat leaves that
    # answer identical — the retained row's `fetched_at` simply becomes when the belief
    # started rather than when it was last restated, which is the more useful stamp.
    #
    # `IS DISTINCT FROM` rather than `<>` so the first snapshot for an hour, where the
    # subquery returns NULL, is an insert rather than a silent skip.
    snap = """
        INSERT INTO forecast_snapshots
            (grid_lat, grid_lon, valid_at, fetched_at, source, payload)
        SELECT %(lat)s, %(lon)s, %(valid_at)s, %(fetched_at)s, %(source)s, %(payload)s
        WHERE (
            SELECT s.payload
            FROM forecast_snapshots s
            WHERE s.grid_lat = %(lat)s
              AND s.grid_lon = %(lon)s
              AND s.valid_at = %(valid_at)s
              AND NOT s.is_analysis
            ORDER BY s.fetched_at DESC
            LIMIT 1
        ) IS DISTINCT FROM %(payload)s
    """
    current = """
        INSERT INTO forecast_current
            (grid_lat, grid_lon, valid_at, fetched_at, source, payload)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (grid_lat, grid_lon, valid_at) DO UPDATE SET
            fetched_at = EXCLUDED.fetched_at,
            source = EXCLUDED.source,
            payload = EXCLUDED.payload
    """
    cell = """
        INSERT INTO spot_grid (spot_id, grid_lat, grid_lon, updated_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (spot_id) DO UPDATE SET
            grid_lat = EXCLUDED.grid_lat,
            grid_lon = EXCLUDED.grid_lon,
            updated_at = EXCLUDED.updated_at
    """

    by_request: dict[tuple[float, float], GridHour] = {}
    stored = 0
    appended = 0
    with conn.cursor() as cur:
        seen_grid_hour: set[tuple[float, float, datetime]] = set()
        for hour in hours:
            valid_at = hour.valid_at
            payload = Jsonb(hour.model_dump(mode="json"))
            key = (hour.grid_lat, hour.grid_lon, valid_at)
            if key not in seen_grid_hour:
                cur.execute(
                    snap,
                    {
                        "lat": hour.grid_lat,
                        "lon": hour.grid_lon,
                        "valid_at": valid_at,
                        "fetched_at": fetched_at,
                        "source": hour.source,
                        "payload": payload,
                    },
                )
                appended += cur.rowcount
                # Current is upserted every cycle whether the payload moved or not.
                # Its `fetched_at` is what the page shows as "Updated 14:36" and what
                # `gogo health` reads, so it has to mean "we checked", not "it changed".
                cur.execute(
                    current,
                    (
                        hour.grid_lat,
                        hour.grid_lon,
                        valid_at,
                        fetched_at,
                        hour.source,
                        payload,
                    ),
                )
                seen_grid_hour.add(key)
                stored += 1
            by_request[(hour.requested_lat, hour.requested_lon)] = hour

        for spot in spots:
            hour = by_request.get((spot.lat, spot.lon))
            if hour is None:
                continue
            cur.execute(cell, (spot.id, hour.grid_lat, hour.grid_lon, fetched_at))
    conn.commit()
    return Written(current=stored, appended=appended)


def persist_analysis_hours(
    conn: psycopg.Connection,
    spots: list[Spot],
    hours: list[GridHour],
    fetched_at: datetime | None = None,
) -> int:
    """Append reanalysis to snapshots. Never writes `forecast_current`.

    That omission is the whole point. `forecast_current` is what `/windows` serves, and
    upserting ERA5 into it would hand the live path knowledge of hours that have already
    happened — the score would look clairvoyant and the mistake would be invisible.

    `fetched_at` is when *we* pulled the row, not when it was valid. The row genuinely
    did not exist before then, so an as-of policy filtering on `fetched_at` is right to
    hide it from an earlier as-of.

    Returns rows written — inserted, or updated because the archive revised them. A
    re-run over an unchanged range writes nothing and says so.
    """
    fetched_at = to_utc(fetched_at or datetime.now(UTC))
    if not hours:
        return 0

    # One analysis row per hour, but not a frozen one: the most recent days come back
    # complete and are revised afterwards, so a later backfill of the same range has to
    # be able to correct them. Updating only on a real difference keeps a repeat run
    # honest about having changed nothing.
    snap = """
        INSERT INTO forecast_snapshots
            (grid_lat, grid_lon, valid_at, fetched_at, source, payload, is_analysis)
        VALUES (%s, %s, %s, %s, %s, %s, true)
        ON CONFLICT (grid_lat, grid_lon, valid_at) WHERE is_analysis DO UPDATE SET
            fetched_at = EXCLUDED.fetched_at,
            source = EXCLUDED.source,
            payload = EXCLUDED.payload
        WHERE forecast_snapshots.payload IS DISTINCT FROM EXCLUDED.payload
    """
    # Insert-only: a backfill must not repoint a spot whose serving cell is already
    # known, but on a database that has never fetched it is the only thing that can
    # record the mapping features will later need.
    cell = """
        INSERT INTO spot_grid (spot_id, grid_lat, grid_lon, updated_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (spot_id) DO NOTHING
    """

    by_request: dict[tuple[float, float], GridHour] = {}
    written = 0
    with conn.cursor() as cur:
        seen: set[tuple[float, float, datetime]] = set()
        for hour in hours:
            key = (hour.grid_lat, hour.grid_lon, hour.valid_at)
            if key not in seen:
                cur.execute(
                    snap,
                    (
                        hour.grid_lat,
                        hour.grid_lon,
                        hour.valid_at,
                        fetched_at,
                        hour.source,
                        Jsonb(hour.model_dump(mode="json")),
                    ),
                )
                written += cur.rowcount
                seen.add(key)
            by_request[(hour.requested_lat, hour.requested_lon)] = hour

        for spot in spots:
            hour = by_request.get((spot.lat, spot.lon))
            if hour is not None:
                cur.execute(cell, (spot.id, hour.grid_lat, hour.grid_lon, fetched_at))
    conn.commit()
    return written


def current_as_of(conn: psycopg.Connection) -> datetime | None:
    """Freshest fetch behind `forecast_current` — the as-of of anything scored from it."""
    with conn.cursor() as cur:
        cur.execute("SELECT max(fetched_at) AS as_of FROM forecast_current")
        row = cur.fetchone()
    return to_utc(row["as_of"]) if row and row["as_of"] else None


def analysis_days(conn: psycopg.Connection) -> set[date]:
    """Local days with reanalysis stored — the days a recalled session can be judged on.

    A label whose day was never backfilled has nothing to be compared against, so the
    importer warns instead of letting it look imported-and-fine.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT (valid_at AT TIME ZONE %s)::date AS day
            FROM forecast_snapshots
            WHERE is_analysis
            """,
            (LISBON.key,),
        )
        return {row["day"] for row in cur.fetchall()}


def ensure_user(conn: psycopg.Connection, handle: str, skill: str = "advanced") -> int:
    """Get or create a user by handle. Real accounts arrive with S5b."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (handle, skill) VALUES (%s, %s)
            ON CONFLICT (handle) DO UPDATE SET handle = EXCLUDED.handle
            RETURNING id
            """,
            (handle, skill),
        )
        user_id = cur.fetchone()["id"]
    conn.commit()
    return user_id


def record_observation(
    conn: psycopg.Connection,
    user_id: int,
    obs: Observation,
    is_synthetic: bool = False,
) -> int | None:
    """Store one label. Returns its id, or None if that session was already recorded.

    None rather than an error because re-running an edited CSV is the normal way to
    import, and a duplicate is a no-op rather than a problem. See 004 for why the
    duplicate must not be allowed through.

    `is_synthetic` defaults to false so that only `gogo demo`, which passes it
    explicitly, can ever write a fixture row. See 006.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO observations
                (user_id, spot_id, kind, scope, started_at, ended_at, residual,
                 anchored, would_return, rating, crowd, note, is_synthetic)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, spot_id, started_at) DO NOTHING
            RETURNING id
            """,
            (
                user_id,
                obs.spot_id,
                obs.kind,
                obs.scope,
                obs.started_at,
                obs.ended_at,
                obs.residual,
                obs.anchored,
                obs.would_return,
                obs.rating,
                obs.crowd,
                obs.note,
                is_synthetic,
            ),
        )
        row = cur.fetchone()
        if row is None:
            conn.commit()
            return None
        observation_id = row["id"]
        for fault in obs.faults:
            cur.execute(
                """
                INSERT INTO observation_faults (observation_id, code, direction)
                VALUES (%s, %s, %s)
                ON CONFLICT (observation_id, code) DO UPDATE
                    SET direction = EXCLUDED.direction
                """,
                (observation_id, fault.code, fault.direction),
            )
    conn.commit()
    return observation_id


def record_impressions(
    conn: psycopg.Connection,
    ranked: list[WindowScore],
    spots: list[Spot],
    as_of: datetime,
    surface: str,
    user_id: int | None = None,
) -> int:
    """Write down what we showed. Append-only; a re-request writes a new row.

    The stored range is the window's own range, so an observation logged 07:15–09:00
    overlaps whatever we actually recommended rather than a single hour standing in
    for it.
    """
    versions = {spot.id: spec_version(spot) for spot in spots}
    sql = """
        INSERT INTO window_impressions
            (user_id, spot_id, window_start, window_end, as_of, surface,
             rank, score, verdict, reasons, score_version, spec_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    with conn.cursor() as cur:
        for position, window in enumerate(ranked, start=1):
            cur.execute(
                sql,
                (
                    user_id,
                    window.spot_id,
                    window.starts_at,
                    window.ends_at,
                    to_utc(as_of),
                    surface,
                    position,
                    window.score,
                    window.verdict,
                    Jsonb([r.model_dump() for r in window.reasons]),
                    SCORE_VERSION,
                    versions.get(window.spot_id, "unknown"),
                ),
            )
    conn.commit()
    return len(ranked)


def impression_for(
    conn: psycopg.Connection, spot_id: str, started_at: datetime, ended_at: datetime
) -> dict | None:
    """The most recent thing we told anyone about this spot over this interval."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT score, verdict, score_version, spec_version, shown_at
            FROM window_impressions
            WHERE spot_id = %s AND window_start < %s AND window_end > %s
            ORDER BY shown_at DESC
            LIMIT 1
            """,
            (spot_id, to_utc(ended_at), to_utc(started_at)),
        )
        return cur.fetchone()


def _hours_from_rows(
    spots: list[Spot], cells: dict[str, tuple[float, float]], rows: list[dict]
) -> list[GridHour]:
    """Re-hydrate stored payloads into `GridHour`, tagged with each spot's own lat/lon.

    Two spots can share a marine cell, so the same row legitimately becomes an hour for
    each of them — the tagging is what lets `assemble` split them apart again.
    """
    by_grid: dict[tuple[float, float], list[dict]] = {}
    for row in rows:
        by_grid.setdefault((row["grid_lat"], row["grid_lon"]), []).append(row)

    hours: list[GridHour] = []
    for spot in spots:
        cell = cells.get(spot.id)
        if cell is None:
            continue
        for row in by_grid.get(cell, []):
            payload = dict(row["payload"])
            payload["requested_lat"] = spot.lat
            payload["requested_lon"] = spot.lon
            payload["grid_lat"] = row["grid_lat"]
            payload["grid_lon"] = row["grid_lon"]
            payload["source"] = row["source"]
            payload["valid_at"] = to_utc(row["valid_at"])
            hours.append(GridHour.model_validate(payload))
    return hours


def _grid_cells(conn: psycopg.Connection) -> dict[str, tuple[float, float]]:
    with conn.cursor() as cur:
        cur.execute("SELECT spot_id, grid_lat, grid_lon FROM spot_grid")
        return {row["spot_id"]: (row["grid_lat"], row["grid_lon"]) for row in cur.fetchall()}


#: How far back `load_current_hours` looks by default. The serving paths discard
#: anything before `now` anyway, so this only has to cover the hour in progress plus
#: clock skew. Its job is to stop an unbounded read growing with the table.
CURRENT_LOOKBACK = timedelta(hours=2)

#: How long an unservable hour is kept before `prune_current` removes it. Generous,
#: because deleting is cheap and the only cost of keeping a row is size.
CURRENT_RETENTION = timedelta(days=2)


def load_current_hours(
    conn: psycopg.Connection, spots: list[Spot], since: datetime | None = None
) -> list[GridHour]:
    """Servable hours for each spot from its last grid, tagged with that spot's lat/lon.

    Bounded on purpose. `forecast_current` accumulates every hour ever fetched, so an
    unbounded read grows without limit while the answer does not: an hour that has
    already happened cannot be served, and every row here is re-validated into a
    `GridHour` once per spot sharing its cell. A year of hourly fetching would make
    this the slowest thing in the request.

    `since` overrides the default bound. Backtests and round-trip tests pass an explicit
    instant because they deal in historical hours on purpose.
    """
    cutoff = since if since is not None else datetime.now(UTC) - CURRENT_LOOKBACK
    cells = _grid_cells(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT grid_lat, grid_lon, valid_at, source, payload
            FROM forecast_current
            WHERE valid_at >= %s
            """,
            (to_utc(cutoff),),
        )
        rows = cur.fetchall()
    return _hours_from_rows(spots, cells, rows)


def prune_current(
    conn: psycopg.Connection, retention: timedelta = CURRENT_RETENTION
) -> int:
    """Drop hours from the serving table that can no longer be served. Returns rows cut.

    Keyed on `valid_at`, never on `fetched_at`, and that distinction is load-bearing: it
    means this can only ever remove hours that are already in the past. A forecast that
    is stale but still points at future hours is left completely alone, so a spell where
    the worker cannot reach Open-Meteo degrades by going out of date rather than by
    having its data deleted underneath it.

    History is untouched. `forecast_snapshots` is the record; this table is a cache of
    the newest belief about hours still ahead of us.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM forecast_current WHERE valid_at < %s",
            (datetime.now(UTC) - retention,),
        )
        cut = cur.rowcount
    conn.commit()
    return cut


def load_analysis_hours(
    conn: psycopg.Connection, spots: list[Spot], start: date, end: date
) -> list[GridHour]:
    """Reanalysis hours for the inclusive local day range `start`..`end`.

    The best known estimate of what the ocean actually did, which is what Q1 — "is the
    score right about real conditions" — has to be judged against. Never mixed with
    `forecast_current`: that is a serving table and a past hour in it holds a nowcast.
    """
    cells = _grid_cells(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT grid_lat, grid_lon, valid_at, source, payload
            FROM forecast_snapshots
            WHERE is_analysis
              AND (valid_at AT TIME ZONE %s)::date BETWEEN %s AND %s
            """,
            (LISBON.key, start, end),
        )
        rows = cur.fetchall()
    return _hours_from_rows(spots, cells, rows)


def count_observations(conn: psycopg.Connection, include_synthetic: bool = False) -> int:
    """How many labels we hold. The Stage 1 gate is a number, so make it readable."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM observations"
            + ("" if include_synthetic else " WHERE NOT is_synthetic")
        )
        return cur.fetchone()["n"]


def load_observations(
    conn: psycopg.Connection,
    include_synthetic: bool = False,
    only_synthetic: bool = False,
) -> list[dict]:
    """Labels, joined to their faults, real ones only unless asked otherwise.

    `include_synthetic` defaults to false because this is the function an evaluation
    will read through, and a metric that quietly counts `gogo demo` output is worse than
    no metric — it looks authoritative and measures our own score against itself.

    `only_synthetic` exists for the opposite reason: validating the harness on fixture
    data with a known injected bias, where recovering that bias is the pass condition.
    """
    if only_synthetic and include_synthetic:
        raise ValueError("only_synthetic and include_synthetic are contradictory")

    where = (
        "WHERE o.is_synthetic" if only_synthetic
        else "" if include_synthetic
        else "WHERE NOT o.is_synthetic"
    )
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT o.id, o.user_id, u.handle, o.spot_id, o.kind, o.scope,
                   o.started_at, o.ended_at, o.reported_at, o.residual, o.rating,
                   o.would_return, o.crowd, o.note, o.anchored, o.is_synthetic,
                   coalesce(
                       array_agg(f.code || ':' || f.direction)
                           FILTER (WHERE f.code IS NOT NULL),
                       '{{}}'
                   ) AS faults
            FROM observations o
            JOIN users u ON u.id = o.user_id
            LEFT JOIN observation_faults f ON f.observation_id = o.id
            {where}
            GROUP BY o.id, u.handle
            ORDER BY o.started_at, o.spot_id
            """  # noqa: S608 - `where` is one of three literals above, never input
        )
        return [dict(row) for row in cur.fetchall()]
