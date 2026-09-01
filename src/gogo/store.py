from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from gogo.ingest.protocol import GridHour
from gogo.models import Spot
from gogo.settings import Settings

LISBON = ZoneInfo("Europe/Lisbon")


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or Settings().database_url, row_factory=dict_row)


@contextmanager
def connection(url: str | None = None) -> Iterator[psycopg.Connection]:
    with connect(url) as conn:
        yield conn


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LISBON)
    return dt


def seed_spots(conn: psycopg.Connection, spots: list[Spot]) -> None:
    sql = """
        INSERT INTO spots (id, name, lat, lon, region, spec)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            lat = EXCLUDED.lat,
            lon = EXCLUDED.lon,
            region = EXCLUDED.region,
            spec = EXCLUDED.spec
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
    fetched_at = _aware(fetched_at or datetime.now(timezone.utc))
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
            valid_at = _aware(hour.valid_at)
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
            payload["valid_at"] = row["valid_at"].astimezone(LISBON)
            hours.append(GridHour.model_validate(payload))
    return hours
