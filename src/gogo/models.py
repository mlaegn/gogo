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


class WindowScore(BaseModel):
    spot_id: str
    spot_name: str
    valid_at: UtcDatetime
    score: int = Field(ge=0, le=100)
    verdict: Verdict
    reasons: list[Reason]
    vetoed: bool = False
