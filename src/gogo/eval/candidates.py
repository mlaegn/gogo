"""Score changes that have not shipped, measured before they do.

Stage 3's rule is that a change lands only with a backtest number attached. A number
needs something to compare: a *candidate* is the score with one set of `ScoreOptions`
switched on, run over exactly the rows, the events and the resampling the incumbent is
measured on. It is named by a short string so a report and a stored run say precisely
what was tried — `size_period:8.3` is the period-aware size gate with an 8.3 s reference.

Two kinds of evidence come out, and they answer different questions.

- **With labels**: does it order same-day pairs better? `metrics.paired_difference`
  against the incumbent decides that. Until real labels exist it reads n/a, which is
  the correct answer.
- **Without labels**: how big a bet is it? `movement` re-ranks every stored day under
  the incumbent and the candidate and counts what changed: the headline spot, the
  verdicts, the hours each spot gains or loses to a veto. That says nothing about which
  is right. It says how much a wrong one would cost, and which days a label would
  settle it on.

A candidate is never served. Adopting one means making its option the default in
`score.py` and bumping `SCORE_VERSION`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg

from gogo.assemble import scored_hours, windows_for_day
from gogo.clock import to_local
from gogo.eval.dataset import Sample
from gogo.features import BEST_KNOWN, features_for_day
from gogo.models import Spot, WindowScore
from gogo.score import INCUMBENT, ScoreOptions, score_hour
from gogo.spots import by_id

Predictor = Callable[[Sample], float | None]


# --- names ---------------------------------------------------------------------------


def _reference_period(arg: str) -> ScoreOptions:
    try:
        seconds = float(arg)
    except ValueError:
        raise ValueError(f"size_period wants seconds, got {arg!r}") from None
    # Mean period on this coast runs ~6–11 s (p10–p90 over the backfilled year). A
    # reference outside a generous band is a typo, not an experiment.
    if not 3.0 <= seconds <= 20.0:
        raise ValueError(f"size_period reference {seconds:g} s is outside 3–20 s")
    return ScoreOptions(size_ref_period_s=seconds)


#: family -> how its argument becomes options. One entry per idea under test.
FAMILIES: dict[str, Callable[[str], ScoreOptions]] = {
    "size_period": _reference_period,
}

#: The sweep `make backtest-candidates` runs: the coast's p10, median, ~p75 and p90 of
#: mean period. The square root is physics; which reference is right is what labels
#: are for, so all four go in rather than one chosen by eye.
SUGGESTED = ("size_period:6.5", "size_period:8.3", "size_period:10", "size_period:11.5")


def parse(name: str) -> tuple[str, ScoreOptions]:
    """`family:arg` to (canonical name, options). The canonical name is what is
    reported and stored, so `size_period:8.30` and `size_period:8.3` are one run."""
    family, sep, arg = name.partition(":")
    if family not in FAMILIES:
        known = ", ".join(sorted(FAMILIES))
        raise ValueError(f"unknown candidate {name!r}; families are: {known}")
    if not sep or not arg:
        raise ValueError(f"candidate {name!r} needs an argument, e.g. {family}:8.3")
    options = FAMILIES[family](arg)
    return f"{family}:{float(arg):g}", options


# --- with labels ---------------------------------------------------------------------


def predictor(options: ScoreOptions, spots: list[Spot]) -> Predictor:
    """The candidate's opinion of a labelled session, computed the way the dataset
    computes the incumbent's: the rounded mean over the hours the person was in the
    water, from the very forecasts the sample carries. With `INCUMBENT` options this
    returns `predicted_score` exactly, which is what makes the comparison fair."""
    catalogue = by_id(spots)

    def predict(sample: Sample) -> float | None:
        spot = catalogue.get(sample.spot_id)
        if spot is None or not sample.forecasts:
            return None
        scores = [score_hour(spot, h, options).score for h in sample.forecasts]
        return float(round(sum(scores) / len(scores)))

    return predict


# --- without labels ------------------------------------------------------------------


@dataclass
class Movement:
    """What a candidate changes across stored days, against the incumbent."""

    days: int = 0
    #: Days the headline (the top window that is not a no) names a different spot,
    #: counting "nothing worth it" as an answer of its own.
    headline_changed: int = 0
    incumbent_verdicts: Counter = field(default_factory=Counter)
    candidate_verdicts: Counter = field(default_factory=Counter)
    #: Surfable spot-hours a veto stops or starts closing, per spot.
    opened: Counter = field(default_factory=Counter)
    closed: Counter = field(default_factory=Counter)
    hours: Counter = field(default_factory=Counter)
    #: (day, incumbent headline, candidate headline), newest last. The days a label
    #: would settle the question on.
    disagreements: list[tuple[date, str | None, str | None]] = field(default_factory=list)


def _headline(windows: list[WindowScore]) -> tuple[str | None, str]:
    top = next((w for w in windows if w.verdict != "no"), None)
    return (top.spot_id, top.verdict) if top else (None, "no")


def _stored_days(conn: psycopg.Connection) -> tuple[date, date] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT min(valid_at) AS lo, max(valid_at) AS hi FROM forecast_snapshots")
        row = cur.fetchone()
    if not row or row["lo"] is None:
        return None
    return to_local(row["lo"]).date(), to_local(row["hi"]).date()


def movement(
    conn: psycopg.Connection,
    spots: list[Spot],
    candidates: dict[str, ScoreOptions],
    from_day: date | None = None,
    to_day: date | None = None,
) -> dict[str, Movement]:
    """Re-rank every stored day under the incumbent and each candidate.

    Reads `best_known` features — reanalysis included — because the question is how the
    candidate judges conditions, not what was knowable when. Each day's features are
    loaded once and shared by every candidate, so they are compared on identical input.
    """
    out = {name: Movement() for name in candidates}
    if not candidates:
        return out
    span = _stored_days(conn)
    if span is None:
        return out
    day = max(span[0], from_day) if from_day else span[0]
    last = min(span[1], to_day) if to_day else span[1]

    while day <= last:
        hours = features_for_day(conn, spots, day, BEST_KNOWN).hours
        base = windows_for_day(spots, hours, day)
        # Features carry tide context either side of the day, so a day next to a stored
        # one has hours but nothing surfable. It is not a day we hold, and counting it
        # would add a phantom "no" to both columns.
        if base:
            base_head = _headline(base)
            base_veto = {
                spot.id: [h.vetoed for h in scored_hours(spot, hours, day)]
                for spot in spots
            }
            for name, options in candidates.items():
                moved = out[name]
                head = _headline(windows_for_day(spots, hours, day, options=options))
                moved.days += 1
                moved.incumbent_verdicts[base_head[1]] += 1
                moved.candidate_verdicts[head[1]] += 1
                if head[0] != base_head[0]:
                    moved.headline_changed += 1
                    moved.disagreements.append((day, base_head[0], head[0]))
                for spot in spots:
                    now = [h.vetoed for h in scored_hours(spot, hours, day, options=options)]
                    for was, became in zip(base_veto[spot.id], now, strict=True):
                        moved.hours[spot.id] += 1
                        moved.opened[spot.id] += was and not became
                        moved.closed[spot.id] += became and not was
        day += timedelta(days=1)
    return out


def options_for(names: list[str]) -> dict[str, ScoreOptions]:
    """Parse a list of candidate names, refusing duplicates after canonicalisation."""
    parsed: dict[str, ScoreOptions] = {}
    for raw in names:
        name, options = parse(raw)
        if name in parsed:
            raise ValueError(f"candidate {name!r} given twice")
        if options == INCUMBENT:
            raise ValueError(f"candidate {name!r} is the incumbent")
        parsed[name] = options
    return parsed
