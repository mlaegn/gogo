from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from gogo.assemble import score_spots_at
from gogo.ingest.openmeteo import OpenMeteoSource
from gogo.ingest.protocol import GridHour
from gogo.spots import load_spots


def _load_fixture(path: Path) -> list[GridHour]:
    raw = json.loads(path.read_text())
    return [GridHour.model_validate(row) for row in raw]


def _saturday_morning(hours: list[GridHour]) -> datetime:
    """First Saturday 08:00 in the series, else the first 08:00, else first hour."""
    candidates = [h.valid_at for h in hours]
    saturdays = [t for t in candidates if t.weekday() == 5 and t.hour == 8]
    if saturdays:
        return saturdays[0]
    eights = [t for t in candidates if t.hour == 8]
    return eights[0] if eights else candidates[0]


def weekend(fixture: Path | None) -> int:
    spots = load_spots()
    if fixture:
        hours = _load_fixture(fixture)
    else:
        src = OpenMeteoSource()
        try:
            hours = src.fetch(spots, forecast_days=7)
        finally:
            src.close()

    when = _saturday_morning(hours)
    ranked = score_spots_at(spots, hours, when)
    local = when.replace(tzinfo=None)
    print(f"Windows around {local.strftime('%A %Y-%m-%d %H:%M')} Europe/Lisbon\n")
    for w in ranked:
        mark = {"go": "GO   ", "maybe": "maybe", "no": "no   "}[w.verdict]
        why = "; ".join(r.detail for r in w.reasons if r.points == 0 or r.code in {"wind", "size", "period"})
        print(f"  {mark}  {w.score:3d}  {w.spot_name}")
        print(f"         {why}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gogo")
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("weekend", help="Rank spots for Saturday morning (or first 08:00).")
    w.add_argument("--fixture", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.cmd == "weekend":
        return weekend(args.fixture)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
