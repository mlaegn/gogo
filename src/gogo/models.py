from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TidePhase = Literal["low", "mid", "high"]
TideTrend = Literal["incoming", "outgoing", "slack"]
Skill = Literal["beginner", "intermediate", "advanced", "expert"]
Crowd = Literal["low", "medium", "high"]
Verdict = Literal["no", "maybe", "go"]
Region = Literal["ericeira", "lisbon", "peniche"]


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
    valid_at: datetime
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


class WindowScore(BaseModel):
    spot_id: str
    spot_name: str
    valid_at: datetime
    score: int = Field(ge=0, le=100)
    verdict: Verdict
    reasons: list[Reason]
    vetoed: bool = False
