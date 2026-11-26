"""Check the almanac's official tide against Open-Meteo's sea level, spot by spot.

    uv run python scripts/almanac_check.py --from 2025-09-01 --to 2026-09-01

Two questions, both answered over a year of every spot's marine cell:

1. Does the half-cosine curve between the Instituto Hidrográfico's turning points,
   shifted to mean sea level, agree with the hourly series the score already reads?
   Open-Meteo is moved 32 minutes later first (`tide.SEA_LEVEL_LEAD`), since that is a
   known timestamp convention, not a disagreement.
2. Which station fits each spot. The almanac uses the nearest; the Ericeira spots sit
   between Cascais and Peniche, so both are scored against every cell.

Network only: Open-Meteo's marine API, the same cells the worker uses
(`cell_selection=sea`).

First run, 2026-11-26, 1 Sep 2025 – 1 Sep 2026, 8784 hours per spot:

- **The shapes agree.** Around a constant offset the two curves differ by 8–9 cm (one
  standard deviation) at every spot against its own station, and that error is flat
  across the cycle (6–8 cm at every tenth of a half-cycle at Ribeira), so the cosine
  between turning points is not the weak point. Some of it is weather: Open-Meteo is a
  model of the sea, the table is astronomy.
- **Open-Meteo's zero is 38 cm low.** Its `sea_level_height_msl` sits 37.5–37.9 cm
  below the table's mean sea level at every cell. It is a level, not a tide error, so
  the phase the score reads is untouched; but an absolute height has to come from the
  almanac, never from Open-Meteo.
- **The nearest station is right.** It is the better fit, or equal within 0.2 cm, for
  all 16 spots: Lisbon spots 8 cm against Cascais vs 11 against Peniche, Peniche spots
  9 vs 12. The Ericeira spots are a tie (9.0–9.4 vs 9.2–9.6), so nearest — Cascais —
  stands.
"""

from __future__ import annotations

import argparse
import statistics
from datetime import UTC, date, datetime

import httpx

from gogo.almanac import station_for, stations
from gogo.ingest.openmeteo import MARINE_URL
from gogo.spots import load_spots
from gogo.tide import SEA_LEVEL_LEAD


def modelled(spots, start: date, end: date) -> list[dict[datetime, float]]:
    raw = httpx.get(
        MARINE_URL,
        params={
            "latitude": ",".join(str(s.lat) for s in spots),
            "longitude": ",".join(str(s.lon) for s in spots),
            "hourly": "sea_level_height_msl",
            "cell_selection": "sea",
            "timeformat": "unixtime",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        timeout=120,
    ).raise_for_status().json()
    out = []
    for cell in raw if isinstance(raw, list) else [raw]:
        h = cell["hourly"]
        out.append(
            {
                datetime.fromtimestamp(t, UTC) + SEA_LEVEL_LEAD: z
                for t, z in zip(h["time"], h["sea_level_height_msl"], strict=True)
                if z is not None
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    args = parser.parse_args()

    spots = load_spots()
    series = modelled(spots, args.start, args.end)
    table = stations()
    print(f"{len(spots)} spots, {args.start} to {args.end}; Open-Meteo moved "
          f"{SEA_LEVEL_LEAD.total_seconds() / 60:.0f} min later")
    print(f"{'spot':22s} {'hours':>6s}  " + "  ".join(
        f"{s.name + ' bias':>13s} {'sd':>5s}" for s in table.values()
    ) + "   nearest")
    worst = 0.0
    offsets: list[float] = []
    for spot, levels in zip(spots, series, strict=True):
        cols = []
        for st in table.values():
            diffs = [z - t.height_m for at, z in levels.items() if (t := st.at(at))]
            bias = statistics.mean(diffs)
            spread = statistics.pstdev(diffs)
            cols.append(f"{bias * 100:+11.1f}cm {spread * 100:4.1f}")
            if st is station_for(spot.lat, spot.lon):
                worst = max(worst, spread)
                offsets.append(bias)
        n = len(levels)
        print(f"{spot.name:22s} {n:6d}  " + "  ".join(cols)
              + f"   {station_for(spot.lat, spot.lon).name}")
    offset_cm = statistics.mean(offsets) * 100
    print(f"against the station each spot uses: Open-Meteo sits {offset_cm:+.0f} cm off the "
          f"table's mean sea level; worst spread around that {worst * 100:.1f} cm")

    # Timing: where does the residual sit in the cycle? A curve that is right at the
    # turning points but wrong mid-tide shows up as error that peaks at full flow.
    st = station_for(spots[0].lat, spots[0].lon)
    by_phase: dict[int, list[float]] = {}
    bias = statistics.mean(z - t.height_m for at, z in series[0].items() if (t := st.at(at)))
    for at, z in series[0].items():
        t = st.at(at)
        if t is None:
            continue
        i = st._bracket(at)
        p0, p1 = st.points[i], st.points[i + 1]
        phase = int(10 * (at - p0.at) / (p1.at - p0.at))
        by_phase.setdefault(phase, []).append(abs(z - bias - t.height_m))
    print(f"\n{spots[0].name} vs {st.name}: mean |difference| after the offset, in cm, "
          "by tenth of a half-cycle")
    print("  " + " ".join(f"{statistics.mean(v) * 100:4.1f}" for _, v in sorted(by_phase.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
