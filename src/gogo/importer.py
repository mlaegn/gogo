"""Turn a hand-written CSV of remembered sessions into labels.

The point is speed of recall. `gogo log` is one session at a time, which is right in the
car park and useless for the fifty sessions already in your camera roll. Those fifty are
what makes the Stage 2 harness possible this month rather than next season.

Two things about a recalled session differ from a logged one, and both are handled here
rather than left to whoever writes the file:

`anchored` is false. Anchoring records whether our score was visible when the judgement
was made, and for a session from last March it cannot have been — there was nothing to
be anchored to. Importing these as anchored would poison the control group that exists
to prove agreement is not self-fulfilling.

`residual` is usually empty, and `rating` carries the signal instead. A residual is
"better or worse than predicted", and nothing was predicted to you at the time, so there
is no residual to give. Absolute ratings are the weaker signal in general, but for two
spots on the same day they still order them, which is what the headline metric needs.

Which is the last thing: **same-day pairs are the whole game.** Pairwise ranking accuracy
compares two spots labelled on one day, and a surf session is one spot. Fifty rows of one
spot per day produce fifty labels and zero pairs. So rows of `kind=checked` — the spot you
looked at from the road and rejected — are not filler, they are where every pair comes
from, and they are the only trace a wrongly-vetoed spot ever leaves. `summarise` counts
the pairs so a file can be judged before it is trusted.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from io import StringIO
from itertools import combinations
from pathlib import Path

from pydantic import ValidationError

from gogo.clock import from_local_input, to_local
from gogo.models import Fault, Observation
from gogo.spots import by_id

REQUIRED = ("date", "spot", "start", "end")
OPTIONAL = (
    "kind",
    "rating",
    "residual",
    "would_return",
    "crowd",
    "faults",
    "note",
    "anchored",
)

_TRUE = {"y", "yes", "true", "1", "t"}
_FALSE = {"n", "no", "false", "0", "f"}


class RowError(Exception):
    """A single unusable line, reported with its number and left to the caller."""


@dataclass
class Parsed:
    observations: list[Observation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)


def _flag(raw: str, column: str) -> bool | None:
    value = raw.strip().lower()
    if not value:
        return None
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise RowError(f"{column}: expected yes/no, got {raw!r}")


def _number(raw: str, column: str) -> int | None:
    value = raw.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise RowError(f"{column}: expected a whole number, got {raw!r}") from None


def _local_time(day: date, raw: str, column: str) -> datetime:
    try:
        return from_local_input(day, raw)
    except ValueError:
        raise RowError(f"{column}: expected HH:MM, got {raw!r}") from None


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _faults(raw: str) -> list[Fault]:
    """`tide:-1 size:-1`, or semicolon separated. A bare code means worse than predicted."""
    out: list[Fault] = []
    for token in raw.replace(";", " ").replace(",", " ").split():
        code, _, direction = token.partition(":")
        try:
            out.append(Fault.model_validate({"code": code, "direction": int(direction or -1)}))
        except Exception:
            raise RowError(f"faults: {token!r} is not a code[:±1]") from None
    return out


def _observation(row: dict[str, str], spot_ids: set[str]) -> Observation:
    for column in REQUIRED:
        if not (row.get(column) or "").strip():
            raise RowError(f"{column} is required")

    spot = row["spot"].strip()
    if spot not in spot_ids:
        raise RowError(f"unknown spot {spot!r}")

    try:
        day = date.fromisoformat(row["date"].strip())
    except ValueError:
        raise RowError(f"date: expected YYYY-MM-DD, got {row['date']!r}") from None

    # Recalled sessions are unanchored unless the file explicitly says otherwise.
    anchored = _flag(row.get("anchored", ""), "anchored")

    try:
        return Observation(
            spot_id=spot,
            kind=(row.get("kind") or "surfed").strip() or "surfed",
            started_at=_local_time(day, row["start"].strip(), "start"),
            ended_at=_local_time(day, row["end"].strip(), "end"),
            rating=_number(row.get("rating", ""), "rating"),
            residual=_number(row.get("residual", ""), "residual"),
            would_return=_flag(row.get("would_return", ""), "would_return"),
            crowd=(row.get("crowd") or "").strip() or None,
            note=(row.get("note") or "").strip() or None,
            faults=_faults(row.get("faults") or ""),
            anchored=False if anchored is None else anchored,
        )
    except RowError:
        raise
    except ValidationError as exc:
        # Pydantic's own rendering leads with "1 validation error for Observation",
        # which tells the person fixing line 12 nothing. Keep the messages only.
        raise RowError(
            "; ".join(
                e["msg"].removeprefix("Value error, ") for e in exc.errors()
            )
        ) from None


def parse(text: str) -> Parsed:
    """Read every row, keeping errors per line so one bad row does not hide the rest."""
    reader = csv.DictReader(StringIO(text))
    result = Parsed()
    if reader.fieldnames is None:
        result.errors.append("empty file")
        return result

    header = [(name or "").strip().lower() for name in reader.fieldnames]
    missing = [column for column in REQUIRED if column not in header]
    if missing:
        result.errors.append(f"header is missing: {', '.join(missing)}")
        return result
    result.unknown_columns = [c for c in header if c not in REQUIRED + OPTIONAL]

    spot_ids = set(by_id())
    for number, raw in enumerate(reader, start=2):  # line 1 is the header
        row = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
        if not any(value.strip() for value in row.values()):
            continue
        try:
            result.observations.append(_observation(row, spot_ids))
        except RowError as exc:
            result.errors.append(f"line {number}: {exc}")
    return result


def parse_file(path: Path) -> Parsed:
    return parse(path.read_text())


def days(observations: list[Observation]) -> dict[date, list[Observation]]:
    """Group by *local* day, because that is the day a session belongs to."""
    out: dict[date, list[Observation]] = {}
    for obs in observations:
        out.setdefault(to_local(obs.started_at).date(), []).append(obs)
    return out


def pair_count(observations: list[Observation]) -> int:
    """Same-day, different-spot pairs — the sample size of the headline metric."""
    total = 0
    for same_day in days(observations).values():
        spots = {obs.spot_id for obs in same_day}
        total += len(list(combinations(spots, 2)))
    return total


def summarise(observations: list[Observation], covered: set[date] | None = None) -> list[str]:
    """Human-readable lines about what a file is worth, not just how big it is."""
    by_day = days(observations)
    lonely = [day for day, rows in by_day.items() if len({o.spot_id for o in rows}) < 2]
    pairs = pair_count(observations)

    lines = [
        f"{_count(len(observations), 'observation')} across {_count(len(by_day), 'day')}",
        f"{_count(pairs, 'same-day spot pair')} — the headline metric compares these",
    ]
    if lonely:
        lines.append(
            f"{_count(len(lonely), 'day')} with a single spot, yielding no pair; "
            "a 'checked' row for anything you looked at that day fixes that"
        )
    if covered is not None:
        gaps = sorted(day for day in by_day if day not in covered)
        if gaps:
            shown = ", ".join(str(day) for day in gaps[:5])
            more = f" (+{len(gaps) - 5} more)" if len(gaps) > 5 else ""
            lines.append(
                f"{_count(len(gaps), 'day')} without reanalysis: {shown}{more} — "
                f"run gogo backfill --from {gaps[0]} --to {gaps[-1]}"
            )
    return lines
