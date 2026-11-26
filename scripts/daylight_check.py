"""What v5's daylight bound changes, against the fixed 06:00–20:00 it replaced.

    uv run python scripts/daylight_check.py --from 2025-09-01 --to 2026-08-31

Scores a year of Open-Meteo reanalysis (the same archive `gogo backfill` writes) with
both rules and reports:

- the hours each rule offers that the other does not, and how many of them were dark
- on what share of (day, spot) pairs the best window's bounds change
- on what share of days the headline — the top spot and its window — changes

Network only, nothing is written. The old rule is put back by swapping
`assemble._is_surfable` for the duration of the comparison; it lives here, not in the
package, because serving must never be able to choose it.

First run, 2026-11-26, 1 Sep 2025 – 31 Aug 2026, 16 spots:

- offered by both: 73988 spot-hours
- v4 only: 7772, every one of them dark by v5's test (December mornings at 06:00,
  winter evenings from 18:00)
- v5 only: 3263, all light (summer evenings after 20:00, a few June mornings)
- the best window's bounds change on 88.7% of (day, spot) pairs, 3134 of 5180 of them
  by an hour or less at either end; 21 go from no to a pass, 73 from a pass to no
- the headline changes on 79.7% of days, the top spot itself on 11.0%
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import httpx

from gogo import assemble
from gogo.clock import to_local
from gogo.ingest.archive import ArchiveSource
from gogo.ingest.protocol import GridHour
from gogo.spots import load_spots

_V5 = assemble._is_surfable


def _v4(valid_at, day, not_before, lat, lon) -> bool:
    if not_before is not None and valid_at < not_before:
        return False
    local = to_local(valid_at)
    return local.date() == day and 6 <= local.hour < 20


def _months(start: date, end: date):
    a = start
    while a <= end:
        b = min(end, (a.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1))
        yield a, b
        a = b + timedelta(days=1)


def _load(spots, start: date, end: date, cache: Path | None) -> list[GridHour]:
    """A year of reanalysis, a month at a time. Free Open-Meteo counts every variable at
    every location, so a second year in an hour gets a 429: wait and retry, and keep
    what came back in `--cache` so a rerun asks for nothing."""
    if cache and cache.exists():
        return [GridHour.model_validate_json(line) for line in cache.read_text().splitlines()]
    source = ArchiveSource()
    hours: list[GridHour] = []
    try:
        for a, b in _months(start, end):
            for attempt in range(6):
                try:
                    hours.extend(source.fetch(spots, a, b))
                    break
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 429 or attempt == 5:
                        raise
                    print(f"  {a}: rate limited, waiting {60 * (attempt + 1)} s")
                    time.sleep(60 * (attempt + 1))
    finally:
        source.close()
    if cache:
        cache.write_text("\n".join(h.model_dump_json() for h in hours))
    return hours


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--cache", type=Path, help="Keep the fetched hours here.")
    args = parser.parse_args()

    spots = load_spots()
    hours = _load(spots, args.start - timedelta(days=1), args.end + timedelta(days=1), args.cache)
    by_day: dict[date, list] = {}
    for h in hours:
        by_day.setdefault(to_local(h.valid_at).date(), []).append(h)

    def padded(day: date) -> list:
        # The tide phase reads the cycle around each hour, so give it the days either side.
        return [h for d in (day - timedelta(days=1), day, day + timedelta(days=1))
                for h in by_day.get(d, [])]

    offered = Counter()
    pairs = changed_pairs = days = changed_headline = changed_spot = 0
    shifts = Counter()
    opened_passing = closed_passing = 0
    day = args.start
    while day <= args.end:
        series = padded(day)
        for s in spots:
            dawn, dusk = assemble.daylight(day, s.lat, s.lon)
            for h in by_day.get(day, []):
                if (h.requested_lat, h.requested_lon) != (s.lat, s.lon):
                    continue
                old = _v4(h.valid_at, day, None, s.lat, s.lon)
                new = _V5(h.valid_at, day, None, s.lat, s.lon)
                if old and not new:
                    offered["v4 only"] += 1
                    middle = h.valid_at + timedelta(minutes=30)
                    offered["v4 only, dark"] += middle < dawn or middle > dusk
                elif new and not old:
                    offered["v5 only"] += 1
                elif new:
                    offered["both"] += 1

        assemble._is_surfable = _v4
        try:
            before = {w.spot_id: w for w in assemble.windows_for_day(spots, series, day)}
        finally:
            assemble._is_surfable = _V5
        after = {w.spot_id: w for w in assemble.windows_for_day(spots, series, day)}

        for sid in set(before) | set(after):
            pairs += 1
            b, a = before.get(sid), after.get(sid)
            if b is None or a is None or (b.starts_at, b.ends_at) != (a.starts_at, a.ends_at):
                changed_pairs += 1
                if b is not None and a is not None:
                    edge = max(abs(b.starts_at - a.starts_at), abs(b.ends_at - a.ends_at))
                    shifts["≤ 1 h at either end" if edge <= timedelta(hours=1) else "more"] += 1
                    closed_passing += b.verdict != "no" and a.verdict == "no"
                    opened_passing += b.verdict == "no" and a.verdict != "no"
        top_b = min(before.values(), key=lambda w: (-w.score, -w.hours, w.spot_name))
        top_a = min(after.values(), key=lambda w: (-w.score, -w.hours, w.spot_name))
        days += 1
        if (top_b.spot_id, top_b.starts_at, top_b.ends_at) != (
            top_a.spot_id, top_a.starts_at, top_a.ends_at
        ):
            changed_headline += 1
        changed_spot += top_b.spot_id != top_a.spot_id
        day += timedelta(days=1)

    print(f"{days} days × {len(spots)} spots, {args.start} to {args.end}")
    print(f"hours offered by both rules: {offered['both']}")
    print(f"  v4 only: {offered['v4 only']} ({offered['v4 only, dark']} of them dark by v5's "
          "own test: under half the hour between civil dawn and dusk)")
    print(f"  v5 only: {offered['v5 only']} (light, outside 06–20)")
    print(f"best window's bounds change on {changed_pairs / pairs:.1%} of (day, spot) pairs; "
          f"{opened_passing} go from no to a pass, {closed_passing} from a pass to no")
    print(f"  of the changed windows: {dict(shifts)}")
    print(f"headline (top spot and its window) changes on {changed_headline / days:.1%} of days; "
          f"the top spot itself on {changed_spot / days:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
