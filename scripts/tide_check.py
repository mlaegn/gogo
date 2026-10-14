"""Check Open-Meteo's tide against the official one, as AGENTS.md asks before trusting it.

The score's tide term comes from Open-Meteo's `sea_level_height_msl`, the output of a
global ocean model, turned into low / mid / high. The reference is the Instituto
Hidrográfico's prediction of high and low water at its tide stations — the official
Portuguese tide tables. Cascais and Peniche sit beside the Lisbon and Peniche spots.

    uv run python scripts/tide_check.py --from 2026-09-16 --to 2026-10-20

First run, 2026-10-14, 16 Sep – 20 Oct: the model turned 32 minutes early at both
stations (spread 3–4 min), heights ±4 cm, range 0.98–1.01×; phase differed on 24% of
surfable hours, 9% after a 32-minute shift. That shift is `tide.SEA_LEVEL_LEAD`; rerun
with `--lead-min 32` to see whether it still holds.

Reports, per station: how much earlier or later the model's high and low water come,
how its range compares, and — the number the score cares about — on what share of
surfable hours the low / mid / high phase comes out differently from the official
curve. Plain Python, network only.

**Times.** The Hidrográfico's API returns UTC: its page labels them so, and its new
moon of 10 October 2026 at 15:50 matches the astronomical new moon, computed, to the
minute. Open-Meteo is requested with `timeformat=unixtime`, as the worker does.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta

from gogo.clock import to_local
from gogo.ingest.openmeteo import MARINE_URL
from gogo.tide import attach_tide

IH_URL = "https://www.hidrografico.pt/hmapi/tidestation/"
HEADERS = {"User-Agent": "gogo-surf-planner/0.1 (tide check)", "Accept": "application/json"}

#: Official station -> the spots whose forecast cell it is checked against.
STATIONS = {
    "Cascais": (15, {"carcavelos": (38.676, -9.339), "guincho": (38.732, -9.472)}),
    "Peniche": (29, {"supertubos": (39.341, -9.361), "baleal": (39.374, -9.340)}),
}


def _get(url: str, params: dict) -> object:
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def official(port: int, start: date, end: date) -> list[tuple[datetime, str, float]]:
    """(time UTC, 'high' | 'low', height above chart datum) for every turning point."""
    events: dict[datetime, tuple[str, float]] = {}
    day = start
    while day <= end:
        for row in _get(IH_URL, {"portID": port, "startDate": day.isoformat(), "period": 7}):
            if row.get("tide") not in ("PM", "BM") or row.get("height") is None:
                continue
            at = datetime.fromisoformat(row["date"]).replace(tzinfo=UTC)
            if start <= at.date() <= end:
                events[at] = ("high" if row["tide"] == "PM" else "low", float(row["height"]))
        day += timedelta(days=8)  # period 7 covers the day and seven more
    return [(t, kind, h) for t, (kind, h) in sorted(events.items())]


def modelled(lat: float, lon: float, past_days: int) -> tuple[list[datetime], list[float]]:
    """Hourly sea level as the worker requests it: sea cell, unix time, Lisbon days."""
    raw = _get(
        MARINE_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "sea_level_height_msl",
            "cell_selection": "sea",
            "timeformat": "unixtime",
            "timezone": "Europe/Lisbon",
            "past_days": past_days,
            "forecast_days": 7,
        },
    )
    times = [datetime.fromtimestamp(t, UTC) for t in raw["hourly"]["time"]]
    return times, raw["hourly"]["sea_level_height_msl"]


def turning_points(times, levels) -> list[tuple[datetime, str, float]]:
    """Highs and lows of an hourly series, placed between the hours by fitting a
    parabola through each extreme and its neighbours: a few minutes, not an hour."""
    out = []
    for i in range(1, len(levels) - 1):
        a, b, c = levels[i - 1], levels[i], levels[i + 1]
        if None in (a, b, c):
            continue
        if (b > a and b >= c) or (b < a and b <= c):
            curvature = a - 2 * b + c
            shift = 0.5 * (a - c) / curvature if curvature else 0.0
            height = b - 0.25 * (a - c) * shift
            out.append((times[i] + timedelta(hours=shift), "high" if b > a else "low", height))
    return out


def _at(series, t):
    for (t0, z0), (t1, z1) in zip(series, series[1:], strict=False):
        if t0 <= t <= t1:
            return z0 + (z1 - z0) * (t - t0) / (t1 - t0)
    return None


def official_curve(events, times) -> list[float | None]:
    """The official tide at each hour: half a cosine between consecutive turning points,
    the shape the tables' own interpolation rule assumes."""
    out: list[float | None] = []
    for t in times:
        before = [e for e in events if e[0] <= t]
        after = [e for e in events if e[0] > t]
        if not before or not after:
            out.append(None)
            continue
        (t0, _, h0), (t1, _, h1) = before[-1], after[0]
        phase = (t - t0) / (t1 - t0)
        out.append(h0 + (h1 - h0) * (1 - math.cos(math.pi * phase)) / 2)
    return out


def compare(
    name: str, port: int, cells: dict, start: date, end: date, lead: timedelta = timedelta()
) -> None:
    events = official(port, start, end)
    past = (date.today() - start).days + 1
    print(f"\n{name} (IH station {port}): {len(events)} official turning points, {start} to {end}")
    for spot, (lat, lon) in cells.items():
        times, levels = modelled(lat, lon, past)
        times = [t + lead for t in times]  # undo a known lead before comparing
        model_events = turning_points(times, levels)
        offsets, ranges = [], []
        for t, kind, h in events:
            same = [m for m in model_events if m[1] == kind and abs(m[0] - t) < timedelta(hours=3)]
            if same:
                m = min(same, key=lambda m: abs(m[0] - t))
                offsets.append((m[0] - t).total_seconds() / 60)
        for (t0, k0, h0), (t1, k1, h1) in zip(events, events[1:], strict=False):
            m0 = [m for m in model_events if m[1] == k0 and abs(m[0] - t0) < timedelta(hours=3)]
            m1 = [m for m in model_events if m[1] == k1 and abs(m[0] - t1) < timedelta(hours=3)]
            if m0 and m1 and k0 != k1:
                ranges.append(abs(m1[0][2] - m0[0][2]) / abs(h1 - h0))

        # What the score sees: the phase, on the hours anyone surfs.
        kept = [(t, z) for t, z in zip(times, levels, strict=True) if z is not None]
        if lead:
            # Back on the hour grid, as the score sees it: interpolate the shifted series.
            hours = [t - lead for t, _ in kept][1:]
            kept = [(h, _at(kept, h)) for h in hours if _at(kept, h) is not None]
        truth = official_curve(events, [t for t, _ in kept])
        pairs = [(t, z, o) for (t, z), o in zip(kept, truth, strict=True) if o is not None]
        # Both raw: this compares the series, so neither gets the score's correction.
        mod = attach_tide([t for t, _, _ in pairs], [z for _, z, _ in pairs], lead=timedelta())
        off = attach_tide([t for t, _, _ in pairs], [o for _, _, o in pairs], lead=timedelta())
        surfable = [t for t, _, _ in pairs if 6 <= to_local(t).hour < 20 and t in mod and t in off]
        phase_wrong = sum(mod[t][0] != off[t][0] for t in surfable) / len(surfable)

        mean = statistics.mean(offsets)
        sd = statistics.stdev(offsets)
        print(
            f"  {spot:11s} cell {lat:.3f},{lon:.3f}: model turns {mean:+.0f} min "
            f"(sd {sd:.0f}, worst {max(offsets, key=abs):+.0f}, n={len(offsets)}); "
            f"range {statistics.mean(ranges):.2f}× official; "
            f"phase differs on {phase_wrong:.1%} of {len(surfable)} surfable hours"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--lead-min", type=float, default=0.0,
        help="Shift the model this many minutes later before comparing.",
    )
    args = parser.parse_args()
    for name, (port, cells) in STATIONS.items():
        compare(name, port, cells, args.start, args.end, timedelta(minutes=args.lead_min))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
