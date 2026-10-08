"""Measure each spot's facing direction and exposure from the OpenStreetMap coastline.

Where `faces_deg`, `exposure` and `shadow_sectors` in coast.yml come from, so they can be re-derived
rather than argued about. Prints a table and the YAML lines to paste; it never edits
coast.yml, which stays hand-curated.

    uv run python scripts/spot_geometry.py            # needs the archive backfilled
    uv run python scripts/spot_geometry.py --no-db    # facing only

**Facing.** OSM coastline ways run with land on the left and water on the right, so a
segment of bearing b has its seaward normal at b + 90°: the swell direction that meets
it square-on. A spot's facing is the length-weighted vector mean of those normals over
the shoreline within `RADIUS_KM` of the nearest coastline point. *Coherence* is the
length of that mean vector (1 = a straight beach, near 0 = the shore turns through the
radius, so the facing is ill-defined and wants a look at a map).

**Exposure.** From a point `OFFSHORE_KM` out to sea along the facing, a ray is cast
towards every 5° bearing the swell actually came from at the spot's marine cell over
the backfilled year. A bearing is *blocked* when the ray meets coastline between
`MIN_HIT_KM` (a headland, not the rocks at your feet) and `REACH_KM`. The blocked share
is weighted by swell energy, H²T. Over `SHELTERED_SHARE` the spot is `sheltered`: the
offshore height at its cell overstates the wave at the beach whenever the swell comes
from behind the land.

**Shadow sectors.** The same rays at every whole degree within 90° of the facing,
merged into arcs: the bearings from which land stands between the spot and the open
sea. Arcs narrower than `MIN_SECTOR_DEG` are dropped as coastline noise. The score's
`shadow` candidate reads these to cut a swell's height by how far into the lee it comes.

Pure Python on purpose: numpy is not a dependency of this project, and a few seconds is
fast enough for something run when a spot is added or moved.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from gogo.spots import load_spots

OVERPASS = "https://overpass-api.de/api/interpreter"
#: Lisbon to Peniche plus the headlands and islands that can shadow it. Fetched in
#: strips because the public server times out on the whole box.
STRIPS = (
    (38.40, 38.80), (38.80, 39.20), (39.20, 39.40), (39.40, 39.60),
)
LON_W, LON_E = -9.75, -8.90

RADIUS_KM = 0.5
OFFSHORE_KM = 0.3
MIN_HIT_KM = 1.0
REACH_KM = 60.0
SHELTERED_SHARE = 0.20
MIN_SECTOR_DEG = 3

LAT0, LON0 = 39.0, -9.4
KX, KY = 111.32 * math.cos(math.radians(LAT0)), 110.57


def xy(lat: float, lon: float) -> tuple[float, float]:
    return (lon - LON0) * KX, (lat - LAT0) * KY


def fetch(cache: Path) -> list[list[tuple[float, float]]]:
    cache.mkdir(parents=True, exist_ok=True)
    ways: dict[int, list[tuple[float, float]]] = {}
    for south, north in STRIPS:
        path = cache / f"coast_{south}_{north}.json"
        if not path.exists():
            query = (
                f'[out:json][timeout:110];way["natural"="coastline"]'
                f"({south},{LON_W},{north},{LON_E});out geom;"
            )
            body = urllib.parse.urlencode({"data": query}).encode()
            for attempt in range(5):
                request = urllib.request.Request(
                    OVERPASS,
                    data=body,
                    headers={
                        "User-Agent": "gogo-surf-planner/0.1 (spot geometry)",
                        "Accept": "application/json",
                    },
                )
                try:
                    with urllib.request.urlopen(request, timeout=130) as response:
                        text = response.read().decode()
                    json.loads(text)
                    path.write_text(text)
                    break
                except (OSError, ValueError):
                    time.sleep(30 * (attempt + 1))
            else:
                raise SystemExit(f"Overpass would not serve {south}–{north}; try later")
        for element in json.loads(path.read_text())["elements"]:
            ways[element["id"]] = [(p["lat"], p["lon"]) for p in element["geometry"]]
    return list(ways.values())


def segments(ways) -> list[tuple[float, float, float, float]]:
    out = []
    for way in ways:
        points = [xy(*p) for p in way]
        out += [(*a, *b) for a, b in zip(points, points[1:], strict=False)]
    return out


def _closest_on(seg, px, py) -> tuple[float, float, float]:
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length2))
    cx, cy = x1 + t * dx, y1 + t * dy
    return math.hypot(cx - px, cy - py), cx, cy


def facing(segs, lat, lon, radius_km=RADIUS_KM):
    px, py = xy(lat, lon)
    distance, qx, qy = min((_closest_on(s, px, py) for s in segs), key=lambda c: c[0])
    sx = sy = total = 0.0
    for seg in segs:
        if _closest_on(seg, qx, qy)[0] > radius_km:
            continue
        x1, y1, x2, y2 = seg
        length = math.hypot(x2 - x1, y2 - y1)
        normal = math.radians((math.degrees(math.atan2(x2 - x1, y2 - y1)) + 90) % 360)
        sx += length * math.sin(normal)
        sy += length * math.cos(normal)
        total += length
    face = math.degrees(math.atan2(sx, sy)) % 360
    return face, math.hypot(sx, sy) / total, (qx, qy), distance


def first_hit(segs, ox, oy, bearing) -> float | None:
    rx, ry = math.sin(math.radians(bearing)), math.cos(math.radians(bearing))
    best = None
    for x1, y1, x2, y2 in segs:
        dx, dy = x2 - x1, y2 - y1
        den = rx * dy - ry * dx
        if abs(den) < 1e-12:
            continue
        ax, ay = x1 - ox, y1 - oy
        s = (ax * dy - ay * dx) / den
        u = (ax * ry - ay * rx) / den
        if 0 <= u <= 1 and 0 < s < REACH_KM and (best is None or s < best):
            best = s
    return best


def blocked_bearings(segs, ox, oy, bearings) -> set[int]:
    """Whole-degree bearings whose ray meets coastline between MIN_HIT_KM and REACH_KM.

    Segments are bucketed by the degrees they subtend from the origin first, so each ray
    tests only what lies in its direction — a few hundred segments, not tens of
    thousands.
    """
    buckets: dict[int, list] = defaultdict(list)
    for seg in segs:
        x1, y1, x2, y2 = seg
        if min(math.hypot(x1 - ox, y1 - oy), math.hypot(x2 - ox, y2 - oy)) > REACH_KM:
            continue
        a1 = math.degrees(math.atan2(x1 - ox, y1 - oy)) % 360
        a2 = math.degrees(math.atan2(x2 - ox, y2 - oy)) % 360
        lo, span = (a1, (a2 - a1) % 360) if (a2 - a1) % 360 <= 180 else (a2, (a1 - a2) % 360)
        for k in range(int(span) + 2):
            buckets[int(lo + k) % 360].append(seg)
            buckets[int(lo + k - 1) % 360].append(seg)
    out = set()
    for bearing in bearings:
        hit = first_hit(buckets.get(bearing % 360, []), ox, oy, bearing)
        if hit is not None and hit >= MIN_HIT_KM:
            out.add(bearing % 360)
    return out


def sectors(blocked: set[int], face: float) -> list[list[int]]:
    """Merge blocked whole degrees within 90° of the facing into [from, to] arcs, read
    clockwise like a swell window. Narrow arcs are dropped as noise."""
    start = int(round(face)) - 89
    run: list[int] = []
    arcs: list[list[int]] = []
    for k in range(179):
        bearing = (start + k) % 360
        if bearing in blocked:
            run.append(bearing)
            continue
        if len(run) >= MIN_SECTOR_DEG:
            arcs.append([run[0], run[-1]])
        run = []
    if len(run) >= MIN_SECTOR_DEG:
        arcs.append([run[0], run[-1]])
    return arcs


def swell_by_cell():
    """{cell: {bearing bin: (hours, energy)}} from the backfilled reanalysis."""
    from gogo.store import connection

    out: dict[tuple[float, float], dict[int, tuple[int, float]]] = defaultdict(dict)
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT spot_id, grid_lat, grid_lon FROM spot_grid")
        cells = {r["spot_id"]: (r["grid_lat"], r["grid_lon"]) for r in cur.fetchall()}
        cur.execute(
            """
            SELECT grid_lat, grid_lon,
                   (round((payload->>'swell_from_deg')::numeric / 5) * 5)::int % 360 AS b,
                   count(*) AS n,
                   sum(power((payload->>'swell_height_m')::float, 2)
                       * (payload->>'swell_period_s')::float) AS e
            FROM forecast_snapshots WHERE is_analysis GROUP BY 1, 2, 3
            """
        )
        for r in cur.fetchall():
            out[(r["grid_lat"], r["grid_lon"])][r["b"]] = (r["n"], r["e"])
    return cells, out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "gogo-osm")
    parser.add_argument("--no-db", action="store_true", help="Facing only, no exposure.")
    args = parser.parse_args()

    segs = segments(fetch(args.cache))
    cells, swell = ({}, {}) if args.no_db else swell_by_cell()
    rows = []
    for spot in load_spots():
        face, coherence, (qx, qy), distance = facing(segs, spot.lat, spot.lon)
        ox = qx + OFFSHORE_KM * math.sin(math.radians(face))
        oy = qy + OFFSHORE_KM * math.cos(math.radians(face))
        share = None
        if spot.id in cells:
            bins = swell[cells[spot.id]]
            energy = sum(e for _, e in bins.values())
            blocked = 0.0
            for bearing, (_, e) in bins.items():
                hit = first_hit(segs, ox, oy, bearing)
                if hit is not None and hit >= MIN_HIT_KM:
                    blocked += e
            share = blocked / energy if energy else None
        exposure = None if share is None else (
            "sheltered" if share > SHELTERED_SHARE else "open"
        )
        half_plane = range(int(round(face)) - 89, int(round(face)) + 90)
        arcs = sectors(blocked_bearings(segs, ox, oy, half_plane), face)
        rows.append(
            (spot, round(face / 5) * 5 % 360, coherence, distance, share, exposure, arcs)
        )

    print(f"{'spot':14s} {'faces':>5s} {'coh':>5s} {'to coast':>8s} {'blocked':>8s}  exposure")
    for spot, face, coherence, distance, share, exposure, _ in rows:
        flag = "  <- check on a map" if coherence < 0.6 or distance > 0.5 else ""
        blocked = "n/a" if share is None else f"{share:.0%}"
        print(
            f"{spot.id:14s} {face:5d} {coherence:5.2f} {distance:6.2f}km {blocked:>8s}  "
            f"{exposure or 'n/a'}{flag}"
        )
    print("\n# coast.yml")
    for spot, face, _, _, _, exposure, arcs in rows:
        print(
            f"{spot.id}: faces_deg: {face}"
            + (f", exposure: {exposure}" if exposure else "")
            + f", shadow_sectors: {arcs}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
