from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from gogo.clock import UTC, to_utc
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


def persist_hours(
    conn: psycopg.Connection,
    spots: list[Spot],
    hours: list[GridHour],
    fetched_at: datetime | None = None,
) -> int:
    """Append snapshots, upsert current by grid, remember each spot's cell."""
    fetched_at = to_utc(fetched_at or datetime.now(UTC))
    if not hours:
        return 0

    snap = """
        INSERT INTO forecast_snapshots
            (grid_lat, grid_lon, valid_at, fetched_at, source, payload)
        VALUES (%s, %s, %s, %s, %s, %s)
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
    written = 0
    with conn.cursor() as cur:
        seen_grid_hour: set[tuple[float, float, datetime]] = set()
        for hour in hours:
            valid_at = hour.valid_at
            payload = Jsonb(hour.model_dump(mode="json"))
            key = (hour.grid_lat, hour.grid_lon, valid_at)
            if key not in seen_grid_hour:
                cur.execute(
                    snap,
                    (
                        hour.grid_lat,
                        hour.grid_lon,
                        valid_at,
                        fetched_at,
                        hour.source,
                        payload,
                    ),
                )
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
                written += 1
            by_request[(hour.requested_lat, hour.requested_lon)] = hour

        for spot in spots:
            hour = by_request.get((spot.lat, spot.lon))
            if hour is None:
                continue
            cur.execute(cell, (spot.id, hour.grid_lat, hour.grid_lon, fetched_at))
    conn.commit()
    return written


def current_as_of(conn: psycopg.Connection) -> datetime | None:
    """Freshest fetch behind `forecast_current` — the as-of of anything scored from it."""
    with conn.cursor() as cur:
        cur.execute("SELECT max(fetched_at) AS as_of FROM forecast_current")
        row = cur.fetchone()
    return to_utc(row["as_of"]) if row and row["as_of"] else None


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
    conn: psycopg.Connection, user_id: int, obs: Observation
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO observations
                (user_id, spot_id, kind, scope, started_at, ended_at, residual,
                 anchored, would_return, rating, crowd, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            ),
        )
        observation_id = cur.fetchone()["id"]
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
    window_hours: int = 1,
    user_id: int | None = None,
) -> int:
    """Write down what we showed. Append-only; a re-request writes a new row.

    Windows are still single hours, so window_end is window_start + one hour. S5 makes
    them real ranges and this signature stops lying.
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
                    window.valid_at,
                    window.valid_at + timedelta(hours=window_hours),
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


def load_current_hours(conn: psycopg.Connection, spots: list[Spot]) -> list[GridHour]:
    """Hours for each spot from its last grid, tagged with that spot's lat/lon."""
    with conn.cursor() as cur:
        cur.execute("SELECT spot_id, grid_lat, grid_lon FROM spot_grid")
        cells = {row["spot_id"]: (row["grid_lat"], row["grid_lon"]) for row in cur.fetchall()}
        cur.execute(
            """
            SELECT grid_lat, grid_lon, valid_at, source, payload
            FROM forecast_current
            """
        )
        by_grid: dict[tuple[float, float], list[dict]] = {}
        for row in cur.fetchall():
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
