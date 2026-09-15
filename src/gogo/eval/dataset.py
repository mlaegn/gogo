"""Labels joined to what we knew and what we said. One row per observation.

This is the supervised dataset the whole project exists to produce: a human's verdict on
one spot over one interval, beside the features that were available at a chosen moment
and the score those features produce. S10's metrics all read this and nothing else.

Three things here are easy to get wrong and expensive to notice later.

**Events, not rows.** Adjacent hours inside one swell are not independent samples. Ten
labels across a single four-day groundswell carry roughly one swell's worth of
information, and a bootstrap that resamples rows would report a confidence interval
several times narrower than the truth. So every sample carries an `event_id`, assigned
across the whole coast rather than per spot — one swell hits all sixteen — and S10
resamples over those. The v1 rule is the plan's: a gap of more than 36 hours starts a new
event. It is a guess, it is written down as a guess, and it should be revisited once
there are enough labels to look at the distribution.

**Synthetic labels default to excluded, here as everywhere.** `gogo demo` derives its
ratings from our own score, so a metric that counts them measures our assumptions and
comes back flattering with nothing to reveal the error. `only_synthetic` exists for the
opposite purpose: validating that the harness recovers the bias `demo` injects on
purpose, which is the only way to trust a measuring device before pointing it at real
labels.

**A dropped sample is reported, not silently skipped.** An observation with no stored
features cannot be scored, and quietly omitting it would shrink the denominator of every
metric without anyone noticing. `Dataset.dropped` says how many and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import combinations

import psycopg

from gogo.assemble import forecasts_from_grid
from gogo.clock import to_local
from gogo.features import BEST_KNOWN, features_for_day
from gogo.models import HourForecast, HourScore, Spot, Verdict
from gogo.score import SCORE_VERSION, score_hour, verdict_for
from gogo.spots import by_id, load_spots
from gogo.store import impression_for, load_observations
from gogo.versioning import spec_version

#: v1 event rule. Two observations separated by more than this begin a new swell event.
#: A guess, deliberately recorded as one — revisit when the label count makes the gap
#: distribution worth looking at.
EVENT_GAP = timedelta(hours=36)


def assign_events(starts: list[datetime]) -> list[int]:
    """Event id per observation, from times already sorted ascending.

    Coast-wide rather than per spot, because a swell is. Two people at two spots on one
    morning are one event, which is exactly what keeps a same-day pair from being
    resampled as two independent draws.
    """
    events: list[int] = []
    current = 0
    previous: datetime | None = None
    for start in starts:
        if previous is not None and start - previous > EVENT_GAP:
            current += 1
        events.append(current)
        previous = start
    return events


@dataclass(frozen=True)
class Sample:
    """One label, the features behind it, and the score they produce."""

    observation_id: int
    event_id: int
    spot_id: str
    day: date
    started_at: datetime
    ended_at: datetime
    kind: str
    anchored: bool
    is_synthetic: bool

    # What the human said.
    rating: int | None
    residual: int | None
    would_return: bool | None
    crowd: str | None
    faults: list[str]

    # The features themselves, and the score they produce. Both, because a metric
    # needs the scores while validating the harness needs the inputs: `gogo demo`
    # injects a size preference on purpose and the documented way to trust the
    # instrument is to recover it, which takes swell height rather than a verdict.
    forecasts: list[HourForecast]
    hours: list[HourScore]
    predicted_score: int | None
    predicted_verdict: Verdict | None

    # What we actually put on screen at the time, where anything was. Only anchored
    # rows have one, and only they can answer "would we have sent you to the right
    # place" as opposed to "was the water any good".
    shown_score: int | None
    shown_verdict: str | None

    score_version: str
    spec_version: str | None

    @property
    def mean_swell_m(self) -> float | None:
        """Mean offshore swell height over the session. The axis `gogo demo` biases."""
        if not self.forecasts:
            return None
        return sum(f.swell_height_m for f in self.forecasts) / len(self.forecasts)

    @property
    def vetoed(self) -> bool:
        """The score said no for every hour the person was actually in the water."""
        return bool(self.hours) and all(h.verdict == "no" for h in self.hours)


@dataclass(frozen=True)
class Dataset:
    policy: str
    samples: list[Sample]
    #: Why observations did not make it in. Counted rather than swallowed, because a
    #: silently shrinking denominator flatters every metric computed from it.
    dropped: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.samples)

    @property
    def events(self) -> int:
        return len({s.event_id for s in self.samples})

    @property
    def days(self) -> int:
        return len({s.day for s in self.samples})

    @property
    def pairs(self) -> int:
        """Same-day, different-spot pairs — the sample size of the headline metric.

        Fifty one-spot days are fifty labels and no pairs, which is why `kind='checked'`
        rows are the point rather than padding.
        """
        by_day: dict[date, set[str]] = {}
        for sample in self.samples:
            by_day.setdefault(sample.day, set()).add(sample.spot_id)
        return sum(len(list(combinations(spots, 2))) for spots in by_day.values())

    def summary(self) -> list[str]:
        lines = [
            f"{len(self.samples)} samples across {self.events} events, {self.days} days",
            f"{self.pairs} same-day spot pairs — the headline metric compares these",
            f"policy: {self.policy}",
        ]
        if any(s.is_synthetic for s in self.samples):
            lines.append(
                "SYNTHETIC labels included — this measures our own assumptions, "
                "never report it as a result"
            )
        for reason, count in sorted(self.dropped.items()):
            lines.append(f"dropped {count}: {reason}")
        return lines


def _session_hours(
    spot: Spot, hours: list, started_at: datetime, ended_at: datetime
) -> list[HourForecast]:
    """The score's view of the hours the person was actually in the water.

    An hour counts when it overlaps the session at all, so a 07:15–09:00 paddle is
    judged on 07:00, 08:00 and the start of 09:00 rather than on a single instant
    standing in for the whole thing.
    """
    series = forecasts_from_grid(
        [h for h in hours if (h.requested_lat, h.requested_lon) == (spot.lat, spot.lon)]
    )
    overlapping = [
        h
        for h in series
        if h.valid_at < ended_at and h.valid_at + timedelta(hours=1) > started_at
    ]
    return sorted(overlapping, key=lambda h: h.valid_at)


def build_dataset(
    conn: psycopg.Connection,
    policy: str = BEST_KNOWN,
    include_synthetic: bool = False,
    only_synthetic: bool = False,
    spots: list[Spot] | None = None,
) -> Dataset:
    """Join every label to the features available at `policy`'s as-of, and score it.

    Defaults to real labels only, matching `load_observations`, because this is the
    function a metric reads through and a number that quietly counted fixture rows would
    look authoritative while measuring nothing.
    """
    spots = spots or load_spots()
    catalogue = by_id(spots)
    rows = load_observations(
        conn, include_synthetic=include_synthetic, only_synthetic=only_synthetic
    )
    events = assign_events([row["started_at"] for row in rows])

    # One features query per day rather than per observation: a day is the unit an
    # as-of policy resolves to, and a season of labels is only a few hundred days.
    cache: dict[date, list] = {}
    samples: list[Sample] = []
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for row, event_id in zip(rows, events, strict=True):
        spot = catalogue.get(row["spot_id"])
        if spot is None:
            drop("spot is no longer in coast.yml")
            continue

        day = to_local(row["started_at"]).date()
        if day not in cache:
            cache[day] = features_for_day(conn, spots, day, policy).hours

        forecasts = _session_hours(spot, cache[day], row["started_at"], row["ended_at"])
        scored = [score_hour(spot, h) for h in forecasts]
        if not scored:
            drop(f"no stored features at as-of policy '{policy}'")
            continue

        mean = round(sum(h.score for h in scored) / len(scored))
        shown = impression_for(conn, spot.id, row["started_at"], row["ended_at"])
        samples.append(
            Sample(
                observation_id=row["id"],
                event_id=event_id,
                spot_id=spot.id,
                day=day,
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                kind=row["kind"],
                anchored=row["anchored"],
                is_synthetic=row["is_synthetic"],
                rating=row["rating"],
                residual=row["residual"],
                would_return=row["would_return"],
                crowd=row["crowd"],
                faults=list(row["faults"]),
                forecasts=forecasts,
                hours=scored,
                predicted_score=mean,
                predicted_verdict=verdict_for(mean, all(h.vetoed for h in scored)),
                shown_score=shown["score"] if shown else None,
                shown_verdict=shown["verdict"] if shown else None,
                score_version=SCORE_VERSION,
                spec_version=spec_version(spot),
            )
        )

    return Dataset(policy=policy, samples=samples, dropped=dropped)
