from __future__ import annotations

import argparse
import json
import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from gogo.assemble import plan_day, windows_for_day
from gogo.clock import from_local_input, now_utc, to_local
from gogo.demo import (
    BIAS_PIVOT_M,
    NOISE_SD,
    SIZE_BIAS_PER_M,
    generate,
    pick_days,
)
from gogo.importer import parse_file, summarise
from gogo.ingest.archive import PROVISIONAL_DAYS, SOURCE, TIDE_FROM
from gogo.ingest.openmeteo import OpenMeteoSource
from gogo.ingest.protocol import GridHour
from gogo.migrate import migrate
from gogo.models import Fault, Observation, WindowScore
from gogo.score import SCORE_VERSION
from gogo.spots import by_id, load_spots
from gogo.store import (
    analysis_days,
    connection,
    count_observations,
    current_as_of,
    ensure_user,
    impression_for,
    load_analysis_hours,
    load_current_hours,
    record_impressions,
    record_observation,
    seed_spots,
)
from gogo.worker import (
    MAX_AGE_S,
    backfill,
    fetch_once,
    health,
    install_signal_handlers,
    run_forever,
    spots_without_hours,
)


def _load_fixture(path: Path) -> list[GridHour]:
    raw = json.loads(path.read_text())
    return [GridHour.model_validate(row) for row in raw]


def print_ranking(day: date, ranked: list[WindowScore]) -> None:
    print(
        f"Windows for {day.strftime('%A %Y-%m-%d')} Europe/Lisbon"
        f"  ·  score {SCORE_VERSION}\n"
    )
    for w in ranked:
        mark = {"go": "GO   ", "maybe": "maybe", "no": "no   "}[w.verdict]
        why = "; ".join(
            r.detail for r in w.reasons if r.points == 0 or r.code in {"wind", "size", "period"}
        )
        # A closed spot has no window to name; its span is just the hours we searched.
        if w.verdict == "no":
            span = "—"
        else:
            span = f"{to_local(w.starts_at):%H:%M}–{to_local(w.ends_at):%H:%M}"
            if w.hours > 1:
                span += f"  ({w.hours}h, peak {to_local(w.peak_at):%H:%M} at {w.peak_score})"
        print(f"  {mark}  {w.score:3d}  {w.spot_name:<18}  {span}")
        print(f"         {why}")


def weekend(fixture: Path | None, from_db: bool) -> int:
    spots = load_spots()
    if fixture:
        hours = _load_fixture(fixture)
    elif from_db:
        # One `now` for picking the day and for scoring it. They have to be the same
        # bound: a window offered at 15:00 must not begin at 06:00, and this path
        # writes an impression, so a stale window here becomes a recommendation on
        # record that was never actually offerable.
        now = now_utc()
        with connection() as conn:
            hours = load_current_hours(conn, spots)
            if not hours:
                print("No stored forecasts. Run: gogo fetch")
                return 1
            day = plan_day(hours, not_before=now)
            if day is None:
                print("No upcoming hours to score. Run: gogo fetch")
                return 1
            ranked = windows_for_day(spots, hours, day, not_before=now)
            as_of = current_as_of(conn)
            if as_of is not None:
                record_impressions(conn, ranked, spots, as_of, surface="cli")
        print_ranking(day, ranked)
        return 0
    else:
        src = OpenMeteoSource()
        try:
            hours = src.fetch(spots, forecast_days=7)
        finally:
            src.close()

    # Fixtures are historical on purpose; a live fetch is not.
    day = plan_day(hours, not_before=None if fixture else now_utc())
    if day is None:
        print("No hours to score.")
        return 1
    print_ranking(day, windows_for_day(spots, hours, day))
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
    if observation_id is None:
        print(f"Already logged: {spots[args.spot].name} starting {start}. Nothing written.")
        return 0
    print(f"Logged #{observation_id}: {spots[args.spot].name} {start}–{end} ({obs.kind})")
    if paired:
        print(
            f"  we said {paired['score']} ({paired['verdict']}) "
            f"on score {paired['score_version']} / spec {paired['spec_version']}"
        )
    else:
        print("  no recommendation on record for that window — residual is unpaired")
    return 0


def import_observations(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"No such file: {path}")
        return 1

    parsed = parse_file(path)
    for column in parsed.unknown_columns:
        print(f"Ignoring unknown column: {column}")
    for problem in parsed.errors:
        print(f"  {problem}")
    if parsed.errors and not parsed.observations:
        print("Nothing importable.")
        return 1

    with connection() as conn:
        for line in summarise(parsed.observations, covered=analysis_days(conn)):
            print(f"  {line}")

        if args.dry_run:
            print("\nDry run — nothing written.")
            return 1 if parsed.errors else 0

        # Entering a season by hand is a plausible first thing to do on a fresh
        # database, before any forecast has ever been fetched, and observations
        # reference spots.
        seed_spots(conn, load_spots())
        user_id = ensure_user(conn, args.user)
        stored = sum(
            1 for obs in parsed.observations if record_observation(conn, user_id, obs)
        )

    skipped = len(parsed.observations) - stored
    print(f"\nImported {stored} observations as {args.user}.")
    if skipped:
        print(f"{skipped} were already recorded and were left alone.")
    # A partly-bad file is a failure worth noticing in a shell, even though the good
    # rows landed: re-running after a fix is free.
    return 1 if parsed.errors else 0


def run_migrations(baseline_through: str | None) -> int:
    with connection() as conn:
        acted = migrate(conn, baseline_through=baseline_through)
    if not acted:
        print("Schema already up to date.")
    for filename, how in acted:
        print(f"{how:>9}  {filename}")
    return 0


def fetch() -> int:
    written = fetch_once()
    print(
        f"{written.current} servable grid-hours current, "
        f"{written.appended} appended to history."
    )
    return 0


def run_health(args) -> int:
    """Exit 0 when the box is doing its job, 1 when it is not.

    Made a command rather than only a log line because the failure mode is silence:
    a stopped worker and a spot that quietly left the ranking both look like nothing
    happening. Usable as a container healthcheck, a cron line, or something to type
    over SSH when you want an answer instead of a hunch.
    """
    report = health(max_age_s=args.max_age)
    for line in report.lines(spot_count=len(load_spots())):
        print(line)
    return 0 if report.ok else 1


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


def run_worker(args) -> int:
    """The loop that keeps the forecast fresh and the snapshot history unbroken."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.once:
        written = fetch_once()
        print(
            f"{written.current} servable grid-hours current, "
            f"{written.appended} appended to history."
        )
        missing = spots_without_hours()
        if missing:
            print(f"WARNING  no hours for: {', '.join(missing)}")
            return 1
        return 0

    stop = threading.Event()
    install_signal_handlers(stop)
    print(f"Fetching every {args.interval}s. Ctrl-C to stop.")
    run_forever(interval_s=args.interval, stop=stop)
    return 0


def run_demo(args) -> int:
    """Write fixture labels, loudly.

    Kept deliberately noisy: the risk with synthetic data is not that it exists, it is
    that six months later nobody remembers which rows it was.
    """
    with connection() as conn:
        if args.purge:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM observations WHERE is_synthetic RETURNING id")
                removed = len(cur.fetchall())
            conn.commit()
            print(f"Deleted {removed} synthetic labels. Real labels: {count_observations(conn)}.")
            return 0

        spots = load_spots()
        covered = analysis_days(conn)
        if not covered:
            print("No reanalysis stored. Run: gogo backfill --from ... --to ...")
            return 1

        days = pick_days(covered, args.days)
        hours = load_analysis_hours(conn, spots, min(days), max(days))
        sessions = generate(spots, hours, days)
        if not sessions:
            print("Nothing generated — no scorable hours on the days picked.")
            return 1

        written = 0
        for session in sessions:
            user_id = ensure_user(conn, session.handle)
            if record_observation(conn, user_id, session.observation, is_synthetic=True):
                written += 1

        real = count_observations(conn)

    observations = [s.observation for s in sessions]
    print("=" * 68)
    print("SYNTHETIC FIXTURE DATA — never include this in an evaluation number.")
    print("=" * 68)
    for line in summarise(observations):
        print(f"  {line}")
    print(f"  {written} written, {len(sessions) - written} already present")
    print(
        f"\n  Injected bias: +{SIZE_BIAS_PER_M:.0f} pts per metre of swell over "
        f"{BIAS_PIVOT_M} m, noise sd {NOISE_SD:.0f}."
    )
    print("  A harness that reports perfect agreement has a bug — it must see that bias.")
    print(f"\n  Real labels, still: {real}. Only these can measure the score.")
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

    imp = sub.add_parser(
        "import",
        help="Bulk-load remembered sessions from a CSV (date,spot,start,end,...).",
    )
    imp.add_argument("file", help="CSV path. Times are local, one row per spot per day.")
    imp.add_argument("--user", default="me", help="Handle; real accounts come with S5b.")
    imp.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report pairs and gaps without writing anything.",
    )

    wk = sub.add_parser(
        "worker",
        help="Fetch on a loop until stopped. Snapshot history is unrecoverable.",
    )
    wk.add_argument(
        "--interval",
        type=int,
        default=3600,
        metavar="SECONDS",
        help="Seconds between fetches (default 3600).",
    )
    wk.add_argument("--once", action="store_true", help="One cycle, then exit.")

    hp = sub.add_parser(
        "health",
        help="Is the forecast fresh and is every spot still ranked? Exit 1 if not.",
    )
    hp.add_argument(
        "--max-age",
        type=int,
        default=MAX_AGE_S,
        metavar="SECONDS",
        help=f"How stale the served forecast may get (default {MAX_AGE_S}).",
    )

    demo = sub.add_parser(
        "demo",
        help="Write FIXTURE labels for harness development. Never real data.",
    )
    demo.add_argument(
        "--days", type=int, default=40, help="How many past days to invent sessions for."
    )
    demo.add_argument(
        "--purge",
        action="store_true",
        help="Delete every synthetic label and exit. Real labels are untouched.",
    )

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
    if args.cmd == "import":
        return import_observations(args)
    if args.cmd == "worker":
        return run_worker(args)
    if args.cmd == "health":
        return run_health(args)
    if args.cmd == "demo":
        return run_demo(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
