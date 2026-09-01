from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from gogo.assemble import saturday_morning, score_spots_at
from gogo.ingest.openmeteo import OpenMeteoSource
from gogo.ingest.protocol import GridHour
from gogo.models import WindowScore
from gogo.spots import load_spots
from gogo.store import connection, load_current_hours
from gogo.worker import fetch_once


def _load_fixture(path: Path) -> list[GridHour]:
    raw = json.loads(path.read_text())
    return [GridHour.model_validate(row) for row in raw]


def print_ranking(when: datetime, ranked: list[WindowScore]) -> None:
    local = when.replace(tzinfo=None)
    print(f"Windows around {local.strftime('%A %Y-%m-%d %H:%M')} Europe/Lisbon\n")
    for w in ranked:
        mark = {"go": "GO   ", "maybe": "maybe", "no": "no   "}[w.verdict]
        why = "; ".join(
            r.detail for r in w.reasons if r.points == 0 or r.code in {"wind", "size", "period"}
        )
        print(f"  {mark}  {w.score:3d}  {w.spot_name}")
        print(f"         {why}")


def weekend(fixture: Path | None, from_db: bool) -> int:
    spots = load_spots()
    if fixture:
        hours = _load_fixture(fixture)
    elif from_db:
        with connection() as conn:
            hours = load_current_hours(conn, spots)
        if not hours:
            print("No stored forecasts. Run: gogo fetch")
            return 1
    else:
        src = OpenMeteoSource()
        try:
            hours = src.fetch(spots, forecast_days=7)
        finally:
            src.close()

    when = saturday_morning(hours)
    if when is None:
        print("No hours to score.")
        return 1
    print_ranking(when, score_spots_at(spots, hours, when))
    return 0


def fetch() -> int:
    n = fetch_once()
    print(f"Stored {n} current grid-hours.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gogo")
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("weekend", help="Rank spots for Saturday morning (or first 08:00).")
    w.add_argument("--fixture", type=Path, default=None)
    w.add_argument(
        "--db",
        action="store_true",
        help="Score stored forecast_current rows (run gogo fetch first).",
    )
    sub.add_parser("fetch", help="Pull Open-Meteo and write snapshots + current to Postgres.")
    args = parser.parse_args(argv)
    if args.cmd == "weekend":
        return weekend(args.fixture, args.db)
    if args.cmd == "fetch":
        return fetch()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
