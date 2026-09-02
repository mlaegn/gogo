"""UTC inside, Lisbon at the edges.

Open-Meteo is asked for `timeformat=unixtime`, so every timestamp entering the system
is an absolute instant. Local time is a rendering concern: CLI output, API responses,
and the questions a human answers ("Saturday 08:00" is local, not UTC).

The autumn DST fold is why this module exists. On 25 Oct 2026 local 01:00 happens twice
in Portugal, at 00:00Z and 01:00Z. A naive local timestamp cannot tell them apart, and
`forecast_current` is keyed on `valid_at` — so one of the two hours would overwrite the
other.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, AwareDatetime

UTC = timezone.utc
LISBON = ZoneInfo("Europe/Lisbon")


def to_utc(dt: datetime) -> datetime:
    """Normalise an aware datetime to UTC. Naive input is a bug, not a default."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(
            f"naive datetime {dt!r}: timestamps must be timezone-aware "
            "(UTC inside, Lisbon at the edges)"
        )
    return dt.astimezone(UTC)


def to_local(dt: datetime) -> datetime:
    """Render an instant in Europe/Lisbon. Only for output and human-facing questions."""
    return to_utc(dt).astimezone(LISBON)


def from_unixtime(seconds: int | float) -> datetime:
    """Open-Meteo `timeformat=unixtime` value → aware UTC."""
    return datetime.fromtimestamp(seconds, tz=UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(to_utc)]
"""Pydantic field type: rejects naive input, stores UTC."""
