"""What the labels say about the score, with an interval attached.

The headline is **pairwise ranking accuracy**: given two spots a person judged on the
same day, did we put them in the same order? That is the question the product actually
answers — the list ranks spots — and it is robust to the thing absolute ratings are worst
at, which is people using the 1-5 scale differently from each other and from themselves
last month. A 4 from someone who never gives 5s and a 4 from someone who gives them
freely are not the same number; "better than the other one that morning" is.

Two rules run through everything here.

**Resample events, never rows.** Ten labels across one four-day groundswell carry about
one swell of information. Bootstrapping over rows would treat them as ten independent
draws and return an interval several times narrower than the data supports, which is how
a harness ends up confidently wrong. Every interval in this module is resampled over the
`event_id` the dataset assigned.

**Say "not enough data" rather than a number.** With no comparable pairs, an accuracy of
0.0 reads as a terrible score and 0.5 reads as a coin flip, and both are lies. Every
metric returns `None` with the sample size beside it instead, so an empty result cannot
be mistaken for a bad one.

No numpy. At a few hundred labels the arithmetic is trivial and a dependency the API and
worker images would have to carry is not worth the import. The `eval` group the plan
reserves arrives with S18, which genuinely needs a sampler.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import combinations

from gogo.eval.dataset import Dataset, Sample

Predictor = Callable[[Sample], float | None]

#: Ratings at or below this are "I should not have bothered". Used by the veto metrics
#: as the human's version of a no.
POOR_RATING = 2

#: Bootstrap resamples. 2000 is plenty for a percentile interval and still instant.
RESAMPLES = 2000
CONFIDENCE = 0.95


@dataclass(frozen=True)
class Estimate:
    """A metric, its sample size, and how little that sample size can support."""

    value: float | None
    #: What the metric was actually computed over — pairs, sessions, whatever the
    #: metric's unit is. Not the number of labels, which is usually larger.
    n: int
    #: Independent events behind those units. The honest sample size.
    events: int
    lo: float | None = None
    hi: float | None = None

    def __str__(self) -> str:
        if self.value is None:
            return f"n/a (n={self.n}, events={self.events})"
        interval = (
            f" [{self.lo:.3f}, {self.hi:.3f}]"
            if self.lo is not None and self.hi is not None
            else ""
        )
        return f"{self.value:.3f}{interval} (n={self.n}, events={self.events})"


def _bootstrap(
    by_event: dict[int, list], compute: Callable[[list], tuple[float, int] | None],
    seed: int = 0,
) -> tuple[float | None, float | None]:
    """Percentile interval, resampling whole events with replacement."""
    events = list(by_event)
    if len(events) < 2:
        return None, None
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(RESAMPLES):
        drawn: list = []
        for _ in events:
            drawn.extend(by_event[rng.choice(events)])
        got = compute(drawn)
        if got is not None:
            values.append(got[0])
    if len(values) < RESAMPLES // 10:
        return None, None
    values.sort()
    tail = (1 - CONFIDENCE) / 2
    return values[int(tail * len(values))], values[int((1 - tail) * len(values)) - 1]


# --- the headline -------------------------------------------------------------------


def _comparable_pairs(samples: Sequence[Sample]) -> list[tuple[Sample, Sample]]:
    """Same day, different spot, and the human actually preferred one of them.

    Equal ratings are dropped rather than counted as agreement. They carry no ordering
    information, and keeping them would let a predictor score well by being uninformative
    on the days people could not tell the difference either.
    """
    by_day: dict[object, list[Sample]] = {}
    for sample in samples:
        if sample.rating is not None:
            by_day.setdefault(sample.day, []).append(sample)
    pairs = []
    for same_day in by_day.values():
        for a, b in combinations(same_day, 2):
            if a.spot_id != b.spot_id and a.rating != b.rating:
                pairs.append((a, b))
    return pairs


def _pairwise(samples: Sequence[Sample], predict: Predictor) -> tuple[float, int] | None:
    concordant = tied = total = 0
    for a, b in _comparable_pairs(samples):
        pa, pb = predict(a), predict(b)
        if pa is None or pb is None:
            continue
        total += 1
        if pa == pb:
            tied += 1
        elif (pa > pb) == (a.rating > b.rating):
            concordant += 1
    if total == 0:
        return None
    # A tie is half credit, the usual rank-correlation treatment: the predictor
    # expressed no preference, so it deserves neither the win nor the loss.
    return (concordant + 0.5 * tied) / total, total


def pairwise_accuracy(
    data: Dataset, predict: Predictor, seed: int = 0
) -> Estimate:
    """The headline. Share of same-day spot pairs ordered the way the human ordered them.

    0.5 is a coin flip. Anything at or below it means the score carries no ordering
    information at all, which is a finding rather than a failure.
    """
    by_event: dict[int, list[Sample]] = {}
    for sample in data.samples:
        by_event.setdefault(sample.event_id, []).append(sample)

    got = _pairwise(data.samples, predict)
    lo, hi = _bootstrap(by_event, lambda rows: _pairwise(rows, predict), seed)
    return Estimate(
        value=None if got is None else got[0],
        n=0 if got is None else got[1],
        events=len(by_event),
        lo=lo,
        hi=hi,
    )


def compare(data: Dataset, baselines: dict[str, Predictor], seed: int = 0) -> dict[str, Estimate]:
    """Every baseline on the same rows. The only way the headline means anything."""
    return {name: pairwise_accuracy(data, fn, seed) for name, fn in baselines.items()}


# --- is the number itself meaningful? -----------------------------------------------


@dataclass(frozen=True)
class Bucket:
    low: int
    high: int
    n: int
    mean_rating: float


def calibration(data: Dataset, width: int = 20) -> list[Bucket]:
    """Predicted score against what people actually said, in bands.

    Not a probability reliability curve, because the score is not a probability — it is
    a 0-100 opinion and the label is a 1-5 rating. What this can show is whether the
    bands are *ordered*: if the 60-80 band is rated no better than the 40-60 band, the
    number is decoration however good the ranking is.
    """
    buckets: dict[int, list[int]] = {}
    for sample in data.samples:
        if sample.predicted_score is None or sample.rating is None:
            continue
        band = min(sample.predicted_score // width * width, 100 - width)
        buckets.setdefault(band, []).append(sample.rating)
    return [
        Bucket(low=low, high=low + width, n=len(r), mean_rating=sum(r) / len(r))
        for low, r in sorted(buckets.items())
    ]


def brier_would_return(data: Dataset, predict: Predictor, seed: int = 0) -> Estimate:
    """Brier score on "would you go back", treating score/100 as a probability.

    That mapping is an assumption, not a measurement, and a crude one: nothing has ever
    calibrated the score into a probability. It is here because *would return* is the
    only genuinely binary label collected, so it is the only place a proper scoring rule
    applies at all. S18 replaces the mapping with a fitted one; until then read this as
    a relative comparison between predictors and not as an absolute.

    Lower is better. 0.25 is what you get by always saying 50%.
    """

    def compute(rows: Sequence[Sample]) -> tuple[float, int] | None:
        total = 0.0
        n = 0
        for sample in rows:
            if sample.would_return is None:
                continue
            p = predict(sample)
            if p is None:
                continue
            probability = max(0.0, min(1.0, p / 100))
            total += (probability - (1.0 if sample.would_return else 0.0)) ** 2
            n += 1
        return (total / n, n) if n else None

    by_event: dict[int, list[Sample]] = {}
    for sample in data.samples:
        by_event.setdefault(sample.event_id, []).append(sample)
    got = compute(data.samples)
    lo, hi = _bootstrap(by_event, compute, seed)
    return Estimate(
        value=None if got is None else got[0],
        n=0 if got is None else got[1],
        events=len(by_event),
        lo=lo,
        hi=hi,
    )


# --- the veto, which is where the damage is -----------------------------------------


@dataclass(frozen=True)
class VetoQuality:
    """How good the score's refusals are.

    Precision is "when we said no, were we right". Recall is "of the sessions that were
    genuinely not worth it, how many did we catch". They trade off, and the plan cares
    about them separately for a reason: a wrong veto is the censoring trap, because it
    sends nobody, so it generates no label and never gets corrected. Recall is cheap to
    buy by vetoing everything. Precision is the one that has to be defended.
    """

    precision: Estimate
    recall: Estimate
    vetoed: int
    poor: int


def veto_quality(data: Dataset, seed: int = 0) -> VetoQuality:
    scored = [s for s in data.samples if s.rating is not None and s.hours]
    by_event: dict[int, list[Sample]] = {}
    for sample in scored:
        by_event.setdefault(sample.event_id, []).append(sample)

    def precision(rows: Sequence[Sample]) -> tuple[float, int] | None:
        vetoed = [s for s in rows if s.vetoed]
        if not vetoed:
            return None
        right = sum(1 for s in vetoed if s.rating <= POOR_RATING)
        return right / len(vetoed), len(vetoed)

    def recall(rows: Sequence[Sample]) -> tuple[float, int] | None:
        poor = [s for s in rows if s.rating <= POOR_RATING]
        if not poor:
            return None
        caught = sum(1 for s in poor if s.vetoed)
        return caught / len(poor), len(poor)

    p, r = precision(scored), recall(scored)
    p_lo, p_hi = _bootstrap(by_event, precision, seed)
    r_lo, r_hi = _bootstrap(by_event, recall, seed)
    events = len(by_event)
    return VetoQuality(
        precision=Estimate(p[0] if p else None, p[1] if p else 0, events, p_lo, p_hi),
        recall=Estimate(r[0] if r else None, r[1] if r else 0, events, r_lo, r_hi),
        vetoed=sum(1 for s in scored if s.vetoed),
        poor=sum(1 for s in scored if s.rating <= POOR_RATING),
    )


# --- the whole day, not just a pair --------------------------------------------------


def _dcg(ratings: Sequence[int]) -> float:
    from math import log2

    return sum(rating / log2(rank + 2) for rank, rating in enumerate(ratings))


def ndcg_at_3(data: Dataset, predict: Predictor, seed: int = 0) -> Estimate:
    """How good the top of the list is, on days with at least three spots labelled.

    Pairwise accuracy treats every pair alike; this asks the question the page actually
    poses, which is whether the spots we put at the top were the good ones. Restricted
    to days with three or more labels because ranking three things is the smallest case
    where "the top of the list" means anything.
    """

    def compute(rows: Sequence[Sample]) -> tuple[float, int] | None:
        by_day: dict = {}
        for sample in rows:
            if sample.rating is not None and predict(sample) is not None:
                by_day.setdefault(sample.day, []).append(sample)
        scores = []
        for same_day in by_day.values():
            spots = {s.spot_id: s for s in same_day}.values()
            if len(spots) < 3:
                continue
            ours = sorted(spots, key=lambda s: -predict(s))[:3]
            best = sorted(spots, key=lambda s: -s.rating)[:3]
            ideal = _dcg([s.rating for s in best])
            if ideal > 0:
                scores.append(_dcg([s.rating for s in ours]) / ideal)
        return (sum(scores) / len(scores), len(scores)) if scores else None

    by_event: dict[int, list[Sample]] = {}
    for sample in data.samples:
        by_event.setdefault(sample.event_id, []).append(sample)
    got = compute(data.samples)
    lo, hi = _bootstrap(by_event, compute, seed)
    return Estimate(
        value=None if got is None else got[0],
        n=0 if got is None else got[1],
        events=len(by_event),
        lo=lo,
        hi=hi,
    )
