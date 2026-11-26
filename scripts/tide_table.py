"""Write the official tide table that `gogo.almanac` reads, one file per station.

    uv run python scripts/tide_table.py --from 2025-01-01 --to 2027-12-31

The source is the Instituto Hidrográfico's prediction of high and low water — the
Portuguese tide tables — for Cascais and Peniche, the same API `scripts/tide_check.py`
checks Open-Meteo against. Its times are UTC (see that script's docstring); heights are
metres above chart datum (ZH), as the tables print them.

The table is astronomy, so it can be written years ahead and it was knowable on every
day a backtest replays: unlike a forecast, using it at an as-of leaks nothing. In
November 2026 the API carried predictions from at least January 2025 to the end of
2027. Rerun this once the next year is published; `gogo health` says how many days the
committed table has left.

Each file holds every turning point and moon phase in the range, plus the provenance.
The run refuses to write a table with a hole in it: consecutive turning points must
alternate high and low and lie under nine hours apart, which a semi-diurnal coast
always does.

Terms: the Instituto Hidrográfico allows its content to be copied for private or
informational, non-commercial use, keeping its references to the Marinha intact
(hidrografico.pt, Informação legal). The source line below is that reference.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

IH_URL = "https://www.hidrografico.pt/hmapi/tidestation/"
HEADERS = {"User-Agent": "gogo-surf-planner/0.1 (tide table)", "Accept": "application/json"}
OUT_DIR = Path(__file__).resolve().parents[1] / "src" / "gogo" / "data" / "tides"
SOURCE = "Instituto Hidrográfico, Marinha Portuguesa — previsão de marés (hmapi/tidestation)"

#: Station id -> (API port id, name, latitude, longitude of the tide gauge).
STATIONS = {
    "cascais": (15, "Cascais", 38.6925, -9.4156),
    "peniche": (29, "Peniche", 39.3558, -9.3811),
}

_KIND = {"PM": "H", "BM": "L"}
_MOON = {"LN": "new", "QC": "first", "LC": "full", "QM": "last"}
_MAX_GAP = timedelta(hours=9)


def _get(params: dict) -> list[dict]:
    request = urllib.request.Request(f"{IH_URL}?{urllib.parse.urlencode(params)}", headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def _stamp(raw: str) -> str:
    return datetime.fromisoformat(raw).replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%MZ")


def _collect(port: int, first: date, period: int, start: date, end: date,
             events: dict[str, list], moons: dict[str, list]) -> None:
    for row in _get({"portID": port, "startDate": first.isoformat(), "period": period}):
        at = datetime.fromisoformat(row["date"]).date()
        if not start <= at <= end:
            continue
        stamp = _stamp(row["date"])
        if row.get("tide") in _KIND and row.get("height") is not None:
            events[stamp] = [stamp, _KIND[row["tide"]], round(float(row["height"]), 2)]
        elif row.get("moon") in _MOON:
            moons[stamp] = [stamp, _MOON[row["moon"]]]
    time.sleep(0.3)


def fetch(port: int, start: date, end: date) -> tuple[list[list], list[list]]:
    """Turning points [time, "H" | "L", height] and moons [time, phase], in order.

    Two quirks of the API, both found by `holes`:

    - `period=7` covers seven days, so the walk steps seven. `scripts/tide_check.py`
      steps eight, which loses a day every week.
    - When a moon phase and a turning point fall on the same minute, a query can
      return the moon row twice and drop the tide (Peniche, high water 2027-11-06
      08:00, first quarter at the same minute). A shorter query starting the day
      before returns both, so each hole is asked for again that way.
    """
    events: dict[str, list] = {}
    moons: dict[str, list] = {}
    day = start
    while day <= end:
        _collect(port, day, 7, start, end, events, moons)
        day += timedelta(days=7)
    for _ in range(2):
        gaps = holes([events[k] for k in sorted(events)])
        if not gaps:
            break
        for t0, _t1 in gaps:
            first = datetime.fromisoformat(t0.replace("Z", "+00:00")).date() - timedelta(days=1)
            _collect(port, first, 3, start, end, events, moons)
    return [events[k] for k in sorted(events)], [moons[k] for k in sorted(moons)]


def holes(events: list[list]) -> list[tuple[str, str]]:
    """Every place the series is not an unbroken alternation of high and low water."""
    out = []
    for (t0, k0, _), (t1, k1, _) in zip(events, events[1:], strict=False):
        gap = datetime.fromisoformat(t1.replace("Z", "+00:00")) - datetime.fromisoformat(
            t0.replace("Z", "+00:00")
        )
        if k0 == k1 or gap > _MAX_GAP:
            out.append((t0, t1))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failed = False
    for station, (port, name, lat, lon) in STATIONS.items():
        events, moons = fetch(port, args.start, args.end)
        bad = holes(events)
        if bad or not events:
            failed = True
            print(f"{name}: {len(events)} turning points, {len(bad)} holes — not written")
            for t0, t1 in bad[:10]:
                print(f"  {t0} -> {t1}")
            continue
        table = {
            "station": station,
            "name": name,
            "port_id": port,
            "lat": lat,
            "lon": lon,
            "source": SOURCE,
            "datum": "chart datum (ZH), metres",
            "times": "UTC",
            "fetched": date.today().isoformat(),
            "from": args.start.isoformat(),
            "to": args.end.isoformat(),
            "events": events,
            "moons": moons,
        }
        path = OUT_DIR / f"{station}.json"
        # One row per line: small enough to diff, and a year added later reads as a block.
        rows = ",\n".join(json.dumps(e) for e in events)
        moon_rows = ",\n".join(json.dumps(m) for m in moons)
        meta = {k: v for k, v in table.items() if k not in ("events", "moons")}
        head = json.dumps(meta, indent=1)
        path.write_text(
            head[:-2] + f',\n "events": [\n{rows}\n],\n "moons": [\n{moon_rows}\n]\n}}\n'
        )
        json.loads(path.read_text())  # what was written must parse
        print(f"{name}: {len(events)} turning points, {len(moons)} moons -> {path.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
