"""The wire format, declared rather than assembled from dicts.

These exist so the OpenAPI schema is precise, because `web/src/schema.d.ts` is generated
from it: an endpoint returning a bare `dict` types as `Record<string, unknown>` on the
client and TypeScript stops earning its keep. Every field the page reads is named here.

Kept apart from `models.py` on purpose. Those are the domain; these are the contract with
one client, and the two are allowed to change for different reasons.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from typing import Literal

from gogo.models import (
    CrowdReport,
    FaultCode,
    ObservationKind,
    Region,
    TideClass,
    TidePhase,
    Verdict,
)


class ReasonOut(BaseModel):
    code: str
    detail: str
    points: int


class WindowOut(BaseModel):
    spot_id: str
    spot_name: str
    region: Region
    spec_version: str | None = None
    # UTC to compute with, local to display. Deriving one from the other in a browser is
    # where timezone bugs come from, so the server sends both.
    starts_at: datetime
    ends_at: datetime
    starts_at_local: datetime
    ends_at_local: datetime
    peak_at: datetime
    peak_at_local: datetime
    hours: int
    score: int
    peak_score: int
    verdict: Verdict
    vetoed: bool
    reasons: list[ReasonOut]


class DaylightOut(BaseModel):
    """A day's light. Civil dawn and dusk (sun 6° below the horizon) bound the hours a
    window is searched in (score v5); sunrise and sunset are for reading."""

    civil_dawn: datetime
    civil_dawn_local: datetime
    sunrise_local: datetime
    sunset_local: datetime
    civil_dusk: datetime
    civil_dusk_local: datetime


class TideOut(BaseModel):
    """One high or low water from the official tide table."""

    at: datetime
    at_local: datetime
    kind: Literal["high", "low"]
    #: Above chart datum, as the tide tables print it: the number a surfer compares.
    height_m: float
    king: bool


class TideNudgeOut(BaseModel):
    """A king tide in daylight, and the spots whose tide gate it tests hardest. The
    page asks for a checked session at several of them, because the headline metric
    is built from same-day pairs."""

    kind: Literal["high", "low"]
    #: One per station (Cascais, Peniche), earliest first.
    tides: list[TideOut]
    stations: list[str]
    spots: list[SpotOut]


class SpotAlmanacOut(BaseModel):
    """What is known in advance at one spot on one day: its station's tide table and
    the spot's own daylight."""

    station: str
    daylight: DaylightOut
    tides: list[TideOut]
    #: The biggest tide of the day. None outside the committed table.
    tide_class: TideClass | None = None


class WindowsOut(BaseModel):
    day: date
    days: list[date]
    timezone: str
    score_version: str
    as_of: datetime | None = None
    windows: list[WindowOut]
    #: Earliest dawn and latest dusk along the coast: the span the day strip draws.
    daylight: DaylightOut | None = None
    nudges: list[TideNudgeOut] = []


class DaysOut(BaseModel):
    timezone: str
    default: date
    days: list[date]
    as_of: datetime | None = None


class SpotOut(BaseModel):
    """Deliberately score-free.

    This is the whole payload the log screen is allowed to fetch. If a score were on
    this model, our prediction would be sitting on the device while someone decides how
    their session went, and "we do not show it" would be a promise the component makes
    rather than a fact about the response.
    """

    id: str
    name: str
    region: Region


class SpotDetailOut(SpotOut):
    swell_from_min: int
    swell_from_max: int
    size_min_m: float
    size_max_m: float
    period_min_s: float
    offshore_from: int
    max_onshore_kn: float
    tides: list[TidePhase]


class SwellOut(BaseModel):
    """The hour's primary swell offshore, as the score reads it. The spot's map opens on
    the nearest swell it has a model run for. `period_s` is the mean period;
    `peak_period_s` the peak, which is what the model runs' fixed period compares with."""

    height_m: float
    period_s: float
    peak_period_s: float | None = None
    from_deg: float


class HourOut(BaseModel):
    valid_at: datetime
    at_local: str
    score: int
    verdict: Verdict
    reasons: list[ReasonOut]
    swell: SwellOut
    tide: TidePhase | None = None


class SpotDayOut(BaseModel):
    spot: SpotDetailOut
    day: date
    hours: list[HourOut]
    almanac: SpotAlmanacOut | None = None


class ObservationIn(BaseModel):
    """One session, in the local terms a person types them in."""

    spot_id: str
    day: date
    start: str = Field(description="Local HH:MM")
    end: str = Field(description="Local HH:MM")
    kind: ObservationKind = "surfed"
    rating: int = Field(ge=1, le=5, description="Absolute quality, answered blind")
    would_return: bool | None = None
    crowd: CrowdReport | None = None
    faults: list[FaultCode] = Field(default_factory=list)
    note: str | None = None


class ShownOut(BaseModel):
    """What we had said. Only ever returned *after* a label is stored."""

    score: int
    verdict: str
    score_version: str
    spec_version: str


class ObservationOut(BaseModel):
    spot_name: str
    day: date
    span: str
    rating: int
    duplicate: bool
    shown: ShownOut | None = None
