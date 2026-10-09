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
from dataclasses import dataclass, field, fields, replace
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


def _reference_period(arg: str) -> tuple[str, ScoreOptions]:
    if arg == "typical":
        return arg, ScoreOptions(size_period_typical=True)
    try:
        seconds = float(arg)
    except ValueError:
        raise ValueError(
            f"size_period wants seconds or 'typical', got {arg!r}"
        ) from None
    # Mean period on this coast runs ~6–11 s (p10–p90 over the backfilled year). A
    # reference outside a generous band is a typo, not an experiment.
    if not 3.0 <= seconds <= 20.0:
        raise ValueError(f"size_period reference {seconds:g} s is outside 3–20 s")
    return f"{seconds:g}", ScoreOptions(size_ref_period_s=seconds)


def _taper_width(arg: str) -> tuple[str, ScoreOptions]:
    try:
        degrees = float(arg)
    except ValueError:
        raise ValueError(f"dir_taper wants degrees, got {arg!r}") from None
    # Narrower than 5° is the hard edge again; wider than 90° reaches the land side.
    if not 5.0 <= degrees <= 90.0:
        raise ValueError(f"dir_taper width {degrees:g}° is outside 5–90°")
    return f"{degrees:g}", ScoreOptions(dir_taper_deg=degrees)


def _shadow_scale(arg: str) -> tuple[str, ScoreOptions]:
    try:
        degrees = float(arg)
    except ValueError:
        raise ValueError(f"shadow wants degrees, got {arg!r}") from None
    # Under 2° the lee is a cliff again; over 60° it smears a headland over a quadrant.
    if not 2.0 <= degrees <= 60.0:
        raise ValueError(f"shadow scale {degrees:g}° is outside 2–60°")
    return f"{degrees:g}", ScoreOptions(shadow_deg=degrees)


def _rays(arg: str) -> tuple[str, ScoreOptions]:
    """`rays` lets the traced seabed decide direction and size; `rays:size` keeps the
    hand window for direction and uses the rays for size only."""
    if arg == "":
        return "", ScoreOptions(rays=True)
    if arg == "size":
        return "size", ScoreOptions(rays=True, rays_keep_window=True)
    raise ValueError(f"rays takes nothing or 'size', got {arg!r}")


#: Families that are complete without an argument.
BARE = frozenset({"rays"})

#: family -> how its argument becomes (canonical argument, options). One entry per
#: idea under test.
FAMILIES: dict[str, Callable[[str], tuple[str, ScoreOptions]]] = {
    "size_period": _reference_period,
    "dir_taper": _taper_width,
    "shadow": _shadow_scale,
    "rays": _rays,
}

#: The sweep `make backtest-candidates` runs. Four fixed size references — the coast's
#: p10, median, ~p75 and p90 of mean period — and `typical`, which re-anchors each
#: spot's range at the period usual for its height. Two taper widths for open spots.
#: Three rates for the land's shadow. The traced seabed, alone and with re-anchored
#: size, which stands in for the window, taper and shadow at once. And everything at once,
#: since if each earns its place they would ship together. The physics fixes the
#: shapes; which settings are right is what labels are for, so all go in rather than
#: one chosen by eye.
SUGGESTED = (
    "size_period:6.5",
    "size_period:8.3",
    "size_period:10",
    "size_period:11.5",
    "size_period:typical",
    "dir_taper:30",
    "dir_taper:45",
    "dir_taper:45+size_period:typical",
    "shadow:10",
    "shadow:20",
    "shadow:30",
    "dir_taper:45+shadow:20+size_period:typical",
    "rays",
    "rays:size",
    "rays+size_period:typical",
    "rays:size+size_period:typical",
)


def _parse_one(name: str) -> tuple[str, ScoreOptions]:
    family, sep, arg = name.partition(":")
    if family not in FAMILIES:
        known = ", ".join(sorted(FAMILIES))
        raise ValueError(f"unknown candidate {name!r}; families are: {known}")
    if family in BARE:
        canonical, options = FAMILIES[family](arg)
        return f"{family}:{canonical}" if canonical else family, options
    if not sep or not arg:
        raise ValueError(f"candidate {name!r} needs an argument, e.g. {family}:8.3")
    canonical, options = FAMILIES[family](arg)
    return f"{family}:{canonical}", options


def parse(name: str) -> tuple[str, ScoreOptions]:
    """`family:arg`, or several joined by `+`, to (canonical name, options).

    The canonical name is what is reported and stored, so `size_period:8.30` and
    `size_period:8.3` are one run, and a combination is named in the order given.
    Two parts that set the same option are refused rather than one silently winning.
    """
    names: list[str] = []
    merged = INCUMBENT
    for part in name.split("+"):
        canonical, options = _parse_one(part)
        changes = {
            f.name: getattr(options, f.name)
            for f in fields(ScoreOptions)
            if getattr(options, f.name) != getattr(INCUMBENT, f.name)
        }
        clash = [k for k in changes if getattr(merged, k) != getattr(INCUMBENT, k)]
        if clash:
            raise ValueError(f"candidate {name!r} sets {', '.join(clash)} twice")
        merged = replace(merged, **changes)
        names.append(canonical)
    return "+".join(names), merged


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
