from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from gogo.clock import UtcDatetime

TidePhase = Literal["low", "mid", "high"]
TideTrend = Literal["incoming", "outgoing", "slack"]
Skill = Literal["beginner", "intermediate", "advanced", "expert"]
Crowd = Literal["low", "medium", "high"]
Verdict = Literal["no", "maybe", "go"]
Region = Literal["ericeira", "lisbon", "peniche"]

# 'checked' is looked-at-and-did-not-surf: the only trace a wrong veto leaves.
ObservationKind = Literal["surfed", "checked", "cam"]
CrowdReport = Literal["empty", "ok", "busy", "zoo"]

# The gates a human can say we got wrong. These are the score's own reason codes —
# test_observations.py asserts the two sets stay identical, because an attributable
# fault is what makes drift detection possible later.
FaultCode = Literal["swell_dir", "size", "period", "wind", "tide"]


class Spot(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    region: Region
    swell_from_min: int
    swell_from_max: int
    size_min_m: float
    size_max_m: float
    period_min_s: float
    offshore_from: int
    max_onshore_kn: float
    tides: list[TidePhase]
    skill_min: Skill
    skill_max: Skill
    crowd: Crowd


class HourForecast(BaseModel):
    valid_at: UtcDatetime
    swell_height_m: float
    swell_from_deg: float
    swell_period_s: float
    wind_wave_height_m: float = 0.0
    wind_speed_kn: float
    wind_from_deg: float
    wind_gusts_kn: float = 0.0
    sea_level_m: float | None = None
    tide: TidePhase | None = None
    tide_trend: TideTrend | None = None


class Reason(BaseModel):
    code: str
    detail: str
    points: int


class Fault(BaseModel):
    code: FaultCode
    direction: Literal[-1, 1]  # -1 worse than predicted, +1 better


class Observation(BaseModel):
    """One human's view of one spot over one interval. Times are UTC."""

    spot_id: str
    kind: ObservationKind = "surfed"
    started_at: UtcDatetime
    ended_at: UtcDatetime
    residual: int | None = Field(default=None, ge=-2, le=2)
    anchored: bool = True
    would_return: bool | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    crowd: CrowdReport | None = None
    note: str | None = None
    faults: list[Fault] = Field(default_factory=list)
    scope: str = "global"

    @model_validator(mode="after")
    def _interval_runs_forwards(self) -> Observation:
        if self.ended_at <= self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self


class HourScore(BaseModel):
    """One spot, one hour. What the score function decides; not what gets served."""

    spot_id: str
    spot_name: str
    valid_at: UtcDatetime
    score: int = Field(ge=0, le=100)
    verdict: Verdict
    reasons: list[Reason]
    vetoed: bool = False


class WindowScore(BaseModel):
    """A run of adjacent hours that all pass, served as one time range.

    This is the product unit. A person surfs an interval and reports an interval, so a
    prediction has to be one too — ranking a single 08:00 against a session that ran
    07:15 to 09:00 compares a point to a range.

    `score` is the mean over member hours and `reasons` come from the member hour whose
    score is nearest that mean, so the sentence explains the number rather than the best
    moment in it. `peak_at` is kept separately for "best around 09:30".

    `ends_at` is exclusive: it is the start of the hour after the last member, so a
    single 08:00 hour spans 08:00–09:00 and `hours` counts 1.
    """

    spot_id: str
    spot_name: str
    starts_at: UtcDatetime
    ends_at: UtcDatetime
    peak_at: UtcDatetime
    hours: int = Field(ge=1)
    score: int = Field(ge=0, le=100)
    peak_score: int = Field(ge=0, le=100)
    verdict: Verdict
    reasons: list[Reason]
    vetoed: bool = False

    @model_validator(mode="after")
    def _range_runs_forwards(self) -> WindowScore:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if not self.starts_at <= self.peak_at < self.ends_at:
            raise ValueError("peak_at must fall inside the window")
        return self
