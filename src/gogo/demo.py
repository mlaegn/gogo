"""Fixture labels, so the Stage 2 harness can be built before real ones exist.

An evaluation pipeline cannot be written against an empty table. This generates sessions
that are structurally identical to real ones — real spots, real days, real reanalysis
behind them, same-day pairs, a spread of ratings — so the joins, the event grouping and
the metrics can all be exercised end to end.

**What this can and cannot answer.** It can prove the harness computes what it claims.
It can never say whether the score is good, because the ratings are *derived from the
score*: a metric run over them measures our own assumptions and comes back flattering.
Every row is written with `is_synthetic = true` (see 006) and every read path defaults
to excluding it, because that is the one mistake there is no recovering from.

**The bias is declared, not hidden, and that is the point.** A synthetic rater who
simply agreed with the score would make every metric read 100% and prove nothing. So
this one has an opinion we wrote down: it likes size more than the score does, and it is
noisy. A working harness must *recover* that — pairwise accuracy below 100%, and
residuals that correlate with swell height. If it reports a perfect score, the harness
is wrong, not the surfer. That is how you test a measuring device: feed it a known
quantity and check the reading.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from gogo.assemble import forecasts_from_grid, windows_for_day
from gogo.clock import from_local_input, to_local
from gogo.ingest.protocol import GridHour
from gogo.models import Observation, Spot

# The injected preference, in score points. A harness that cannot see this is broken.
SIZE_BIAS_PER_M = 14.0
BIAS_PIVOT_M = 1.2
NOISE_SD = 9.0

# Three raters, so labels are not all one taste and the per-user paths get exercised.
# The prefix is a second, human-visible signal on top of the column.
HANDLES = ("demo-ana", "demo-tiago", "demo-max")

# A rating is 1-5, so felt quality has to be bucketed. Calibrated against what the score
# actually emits for spots worth driving to (roughly 65-90) rather than against the
# nominal 0-100, or nearly every session lands on 5 and the fixture has no middle — and
# the middle is exactly where ranking is hard and a harness earns its keep.
RATING_CUTS = ((45.0, 1), (60.0, 2), (72.0, 3), (85.0, 4))


@dataclass
class DemoSession:
    """One generated label, plus the working that produced it."""

    observation: Observation
    handle: str
    day: date
    our_score: int
    felt: float


def _rating_for(felt: float) -> int:
    for cut, rating in RATING_CUTS:
        if felt < cut:
            return rating
    return 5


def _mean_swell_m(spot: Spot, hours: list[GridHour], day: date) -> float:
    """Average swell height over the spot's day, for the bias term."""
    series = [
        h
        for h in forecasts_from_grid(
            [h for h in hours if (h.requested_lat, h.requested_lon) == (spot.lat, spot.lon)]
        )
        if to_local(h.valid_at).date() == day
    ]
    if not series:
        return BIAS_PIVOT_M
    return sum(h.swell_height_m for h in series) / len(series)


SURF_MINUTES = 60
CHECK_MINUTES = 20
CHECK_GAP_MINUTES = 40


def _session_span(day_base: datetime, rank: int) -> tuple[datetime, datetime]:
    """Slots for one rater's day: a session, then looks at other spots after it.

    Every slot is derived from a *single* base for the whole day, which is what makes
    the times distinct by construction. Deriving each from its own spot's window looked
    reasonable and put one rater in two places at once, because two spots' windows can
    differ by exactly the offset between two slots.
    """
    if rank == 0:
        return day_base, day_base + timedelta(minutes=SURF_MINUTES)
    # Checks sit after the session — you surf, then drive and look at the next one.
    start = day_base + timedelta(
        minutes=SURF_MINUTES + (rank - 1) * CHECK_GAP_MINUTES
    )
    return start, start + timedelta(minutes=CHECK_MINUTES)


def generate(
    spots: list[Spot],
    hours: list[GridHour],
    days: list[date],
    spots_per_day: tuple[int, int] = (2, 4),
    seed: int = 20260908,
) -> list[DemoSession]:
    """Build sessions for `days`, at least two spots each so pairs exist.

    Seeded, so re-running produces the same fixture and the unique constraint turns a
    second run into a no-op rather than a second copy.
    """
    rng = random.Random(seed)
    out: list[DemoSession] = []

    by_id = {s.id: s for s in spots}

    for day in sorted(days):
        ranked = windows_for_day(spots, hours, day)
        if not ranked:
            continue

        # One rater owns a day. A day is a person's drive up the coast, so scattering
        # raters across it invents people who were in two places at once.
        handle = rng.choice(HANDLES)

        # Mostly spots worth the drive, with the occasional rejected one kept in: a real
        # surfer is censored toward the top, and a fixture with no low-ranked spots would
        # never exercise the veto paths. Neither extreme is what we want.
        passing = [w for w in ranked if w.verdict != "no"]
        vetoed = [w for w in ranked if w.verdict == "no"]
        wanted = min(rng.randint(*spots_per_day), len(ranked))
        pool = passing[: max(wanted + 2, 6)] or ranked
        chosen = rng.sample(pool, min(wanted, len(pool)))
        if vetoed and rng.random() < 0.3:
            chosen.append(rng.choice(vetoed))

        # Best of the day is the one they surfed; the rest were looked at and rejected.
        # That is where pairs come from, and it mirrors a real surf day.
        chosen.sort(key=lambda w: -w.score)

        # One base time for the rater's whole day. A vetoed top pick has no window, so
        # fall back to a plausible morning.
        best = chosen[0]
        day_base = (
            best.starts_at
            if best.verdict != "no"
            else from_local_input(day, f"{rng.choice([7, 8, 9]):02d}:00")
        )

        for rank, window in enumerate(chosen):
            spot = by_id.get(window.spot_id)
            if spot is None:
                continue

            swell = _mean_swell_m(spot, hours, day)
            felt = (
                window.score
                + SIZE_BIAS_PER_M * (swell - BIAS_PIVOT_M)
                + rng.gauss(0, NOISE_SD)
            )
            rating = _rating_for(felt)

            start, end = _session_span(day_base, rank)
            out.append(
                DemoSession(
                    observation=Observation(
                        spot_id=spot.id,
                        kind="surfed" if rank == 0 else "checked",
                        started_at=start,
                        ended_at=end,
                        rating=rating,
                        would_return=felt >= 55,
                        crowd=rng.choice(["empty", "ok", "ok", "busy", None]),
                        note=None,
                        faults=[],
                        # Nothing was on screen in the past, so nothing was anchored to.
                        # Same reasoning as an imported CSV row.
                        anchored=False,
                    ),
                    handle=handle,
                    day=day,
                    our_score=window.score,
                    felt=felt,
                )
            )
    return out


def pick_days(covered: set[date], count: int, seed: int = 20260908) -> list[date]:
    """Spread `count` days across the reanalysis we hold, most recent first.

    Spread rather than consecutive: adjacent days sit inside one swell and are not
    independent samples, so a block of them inflates every number the harness reports.
    """
    if not covered:
        return []
    ordered = sorted(covered, reverse=True)
    if count >= len(ordered):
        return sorted(ordered)
    step = len(ordered) / count
    picked = {ordered[min(int(i * step), len(ordered) - 1)] for i in range(count)}
    # Jitter deterministically so the sample is not exactly periodic with the weather.
    rng = random.Random(seed)
    while len(picked) < count:
        picked.add(rng.choice(ordered))
    return sorted(picked)
