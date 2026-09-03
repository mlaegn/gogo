from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from gogo.assemble import saturday_morning, score_spots_at
from gogo.clock import from_local_input, now_utc, to_local
from gogo.ingest.archive import PROVISIONAL_DAYS, SOURCE, TIDE_FROM
from gogo.ingest.openmeteo import OpenMeteoSource
from gogo.ingest.protocol import GridHour
from gogo.migrate import migrate
from gogo.models import Fault, Observation, WindowScore
from gogo.score import SCORE_VERSION
from gogo.spots import by_id, load_spots
from gogo.store import (
    connection,
    current_as_of,
    ensure_user,
    impression_for,
    load_current_hours,
    record_impressions,
    record_observation,
)
from gogo.worker import backfill, fetch_once


def _load_fixture(path: Path) -> list[GridHour]:
    raw = json.loads(path.read_text())
    return [GridHour.model_validate(row) for row in raw]


def print_ranking(when: datetime, ranked: list[WindowScore]) -> None:
    local = to_local(when)
    print(
        f"Windows around {local.strftime('%A %Y-%m-%d %H:%M')} Europe/Lisbon"
        f"  ·  score {SCORE_VERSION}\n"
    )
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
            when = saturday_morning(hours, not_before=now_utc())
            if when is None:
                print("No upcoming hours to score. Run: gogo fetch")
                return 1
            ranked = score_spots_at(spots, hours, when)
            as_of = current_as_of(conn)
            if as_of is not None:
                record_impressions(conn, ranked, spots, as_of, surface="cli")
        print_ranking(when, ranked)
        return 0
    else:
        src = OpenMeteoSource()
        try:
            hours = src.fetch(spots, forecast_days=7)
        finally:
            src.close()

    # Fixtures are historical on purpose; a live fetch is not.
    when = saturday_morning(hours, not_before=None if fixture else now_utc())
    if when is None:
        print("No hours to score.")
        return 1
    print_ranking(when, score_spots_at(spots, hours, when))
    return 0


def _parse_fault(raw: str) -> Fault:
    """`tide:-1` — the tide gate was worse than predicted."""
    code, _, direction = raw.partition(":")
    return Fault.model_validate({"code": code, "direction": int(direction or -1)})


def log_observation(args: argparse.Namespace) -> int:
    spots = by_id()
    if args.spot not in spots:
        print(f"Unknown spot: {args.spot}. Known: {', '.join(sorted(spots))}")
        return 1

    day = date.fromisoformat(args.date) if args.date else to_local(now_utc()).date()
    obs = Observation(
        spot_id=args.spot,
        kind=args.kind,
        started_at=from_local_input(day, args.start),
        ended_at=from_local_input(day, args.end),
        residual=args.residual,
        anchored=not args.unanchored,
        would_return=args.would_return,
        rating=args.rating,
        crowd=args.crowd,
        note=args.note,
        faults=[_parse_fault(f) for f in args.fault or []],
    )

    with connection() as conn:
        user_id = ensure_user(conn, args.user)
        observation_id = record_observation(conn, user_id, obs)
        paired = impression_for(conn, obs.spot_id, obs.started_at, obs.ended_at)

    start = to_local(obs.started_at).strftime("%a %d %b %H:%M")
    end = to_local(obs.ended_at).strftime("%H:%M")
    print(f"Logged #{observation_id}: {spots[args.spot].name} {start}–{end} ({obs.kind})")
    if paired:
        print(
            f"  we said {paired['score']} ({paired['verdict']}) "
            f"on score {paired['score_version']} / spec {paired['spec_version']}"
        )
    else:
        print("  no recommendation on record for that window — residual is unpaired")
    return 0


def run_migrations(baseline_through: str | None) -> int:
    with connection() as conn:
        acted = migrate(conn, baseline_through=baseline_through)
    if not acted:
        print("Schema already up to date.")
    for filename, how in acted:
        print(f"{how:>9}  {filename}")
    return 0


def fetch() -> int:
    n = fetch_once()
    print(f"Stored {n} current grid-hours.")
    return 0


def run_backfill(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)
    if end < start:
        print(f"--from {start} is after --to {end}")
        return 1

    settled = to_local(now_utc()).date() - timedelta(days=PROVISIONAL_DAYS)
    if end > settled:
        print(f"Note: hours after {settled} are provisional; back them up again later.")
    if start < TIDE_FROM:
        print(f"Note: no tide in the archive before {TIDE_FROM}; those hours load without it.")

    def progress(chunk_start: date, chunk_end: date, written: int) -> None:
        print(f"  {chunk_start} .. {chunk_end}  {written:6d} hours")

    total = backfill(start, end, chunk_days=args.chunk_days, on_chunk=progress)
    print(f"Wrote {total} analysis grid-hours ({SOURCE}). Serving is untouched.")
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

    b = sub.add_parser(
        "backfill",
        help="Pull ERA5 reanalysis for a past range into snapshots (not into serving).",
    )
    b.add_argument("--from", dest="from_date", required=True, metavar="YYYY-MM-DD")
    b.add_argument("--to", dest="to_date", required=True, metavar="YYYY-MM-DD")
    b.add_argument(
        "--chunk-days",
        type=int,
        default=31,
        help="Days per request; each chunk commits before the next starts.",
    )

    m = sub.add_parser("migrate", help="Apply pending SQL migrations.")
    m.add_argument(
        "--baseline",
        metavar="FILENAME",
        default=None,
        help="Record files up to and including this one as applied without running "
        "them, for a database that predates this runner. e.g. 001_init.sql",
    )

    log = sub.add_parser("log", help="Record what you saw in the water.")
    log.add_argument("spot", help="Spot id, e.g. ribeira")
    log.add_argument("--start", required=True, help="Local time, e.g. 07:15")
    log.add_argument("--end", required=True, help="Local time, e.g. 09:00")
    log.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to today")
    log.add_argument("--kind", default="surfed", choices=["surfed", "checked", "cam"])
    log.add_argument(
        "--residual",
        type=int,
        default=None,
        choices=[-2, -1, 0, 1, 2],
        help="Versus what we predicted: -2 much worse .. +2 much better",
    )
    log.add_argument(
        "--fault",
        action="append",
        default=None,
        metavar="CODE[:±1]",
        help="Gate we got wrong, e.g. tide:-1 size:-1. Repeatable.",
    )
    log.add_argument("--crowd", default=None, choices=["empty", "ok", "busy", "zoo"])
    log.add_argument("--rating", type=int, default=None, choices=[1, 2, 3, 4, 5])
    log.add_argument("--would-return", action="store_true", default=None)
    log.add_argument(
        "--unanchored",
        action="store_true",
        help="You had not seen our score when you judged it (control group).",
    )
    log.add_argument("--note", default=None)
    log.add_argument("--user", default="me", help="Handle; real accounts come with S5b.")

    args = parser.parse_args(argv)
    if args.cmd == "weekend":
        return weekend(args.fixture, args.db)
    if args.cmd == "fetch":
        return fetch()
    if args.cmd == "backfill":
        return run_backfill(args)
    if args.cmd == "migrate":
        return run_migrations(args.baseline)
    if args.cmd == "log":
        return log_observation(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
