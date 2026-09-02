from __future__ import annotations

from gogo.geo import angle_distance, in_bearing_window, window_center
from gogo.models import HourForecast, Reason, Spot, Verdict, WindowScore

# Bump on any change that can move a rank: weights, thresholds, gates, new terms, or the
# rule that aggregates hours into a window. A stored verdict is meaningless without it.
SCORE_VERSION = "v1"

# Onshore is ~180° from the spot's offshore_from.
_ONSHORE_ALIGN_DEG = 75


def score_window(spot: Spot, hour: HourForecast) -> WindowScore:
    reasons: list[Reason] = []
    vetoed = False

    swell_ok = in_bearing_window(
        hour.swell_from_deg, spot.swell_from_min, spot.swell_from_max
    )
    center = window_center(spot.swell_from_min, spot.swell_from_max)
    off_swell = angle_distance(hour.swell_from_deg, center)
    if not swell_ok or off_swell > 90:
        reasons.append(
            Reason(
                code="swell_dir",
                detail=(
                    f"swell {hour.swell_from_deg:.0f}° is outside "
                    f"{spot.swell_from_min}–{spot.swell_from_max}°"
                ),
                points=0,
            )
        )
        vetoed = True
    elif off_swell <= 25:
        reasons.append(
            Reason(
                code="swell_dir",
                detail=f"swell {hour.swell_from_deg:.0f}° in the pocket",
                points=20,
            )
        )
    else:
        reasons.append(
            Reason(
                code="swell_dir",
                detail=f"swell {hour.swell_from_deg:.0f}° is usable, not ideal",
                points=10,
            )
        )

    hs = hour.swell_height_m
    if hs < spot.size_min_m:
        reasons.append(
            Reason(
                code="size",
                detail=f"{hs:.1f} m is below this spot's {spot.size_min_m:.1f} m min",
                points=0,
            )
        )
        vetoed = True
    elif hs > spot.size_max_m:
        reasons.append(
            Reason(
                code="size",
                detail=f"{hs:.1f} m looks like a close-out here (max {spot.size_max_m:.1f} m)",
                points=0,
            )
        )
        vetoed = True
    else:
        mid = (spot.size_min_m + spot.size_max_m) / 2
        closeness = 1 - abs(hs - mid) / max(mid - spot.size_min_m, 0.3)
        pts = 10 + int(15 * max(0.0, min(1.0, closeness)))
        reasons.append(Reason(code="size", detail=f"{hs:.1f} m in range", points=pts))

    if hour.swell_period_s + 0.05 < spot.period_min_s:
        short = spot.period_min_s - hour.swell_period_s
        if short >= 3:
            reasons.append(
                Reason(
                    code="period",
                    detail=f"{hour.swell_period_s:.0f} s is too short (wants ≥ {spot.period_min_s:.0f} s)",
                    points=0,
                )
            )
            vetoed = True
        else:
            reasons.append(
                Reason(
                    code="period",
                    detail=f"{hour.swell_period_s:.0f} s is short for this spot",
                    points=4,
                )
            )
    elif hour.swell_period_s >= spot.period_min_s + 2:
        reasons.append(
            Reason(
                code="period",
                detail=f"{hour.swell_period_s:.0f} s has some punch",
                points=20,
            )
        )
    else:
        reasons.append(
            Reason(
                code="period",
                detail=f"{hour.swell_period_s:.0f} s is enough",
                points=12,
            )
        )

    onshore_from = (spot.offshore_from + 180) % 360
    onshore_align = angle_distance(hour.wind_from_deg, onshore_from)
    offshore_align = angle_distance(hour.wind_from_deg, spot.offshore_from)
    if onshore_align <= _ONSHORE_ALIGN_DEG and hour.wind_speed_kn > spot.max_onshore_kn:
        reasons.append(
            Reason(
                code="wind",
                detail=(
                    f"{hour.wind_speed_kn:.0f} kn onshore "
                    f"(from {hour.wind_from_deg:.0f}°) is a no"
                ),
                points=0,
            )
        )
        vetoed = True
    elif offshore_align <= 50 and hour.wind_speed_kn <= 18:
        reasons.append(
            Reason(
                code="wind",
                detail=f"{hour.wind_speed_kn:.0f} kn offshore",
                points=20,
            )
        )
    elif onshore_align <= _ONSHORE_ALIGN_DEG:
        reasons.append(
            Reason(
                code="wind",
                detail=f"{hour.wind_speed_kn:.0f} kn onshore, still under the cap",
                points=6,
            )
        )
    else:
        reasons.append(
            Reason(
                code="wind",
                detail=f"{hour.wind_speed_kn:.0f} kn cross / sideshore",
                points=12,
            )
        )

    if hour.tide is None:
        reasons.append(Reason(code="tide", detail="tide unknown", points=8))
    elif hour.tide not in spot.tides:
        reasons.append(
            Reason(
                code="tide",
                detail=f"{hour.tide} tide is outside {', '.join(spot.tides)}",
                points=2,
            )
        )
    else:
        extra = " incoming" if hour.tide_trend == "incoming" else ""
        reasons.append(
            Reason(code="tide", detail=f"{hour.tide}{extra} works here", points=15)
        )

    total = 0 if vetoed else min(100, sum(r.points for r in reasons))
    verdict: Verdict = "no" if vetoed or total < 40 else "go" if total >= 70 else "maybe"
    return WindowScore(
        spot_id=spot.id,
        spot_name=spot.name,
        valid_at=hour.valid_at,
        score=total,
        verdict=verdict,
        reasons=reasons,
        vetoed=vetoed,
    )


def rank_hour(spots: list[Spot], hour: HourForecast) -> list[WindowScore]:
    ranked = [score_window(s, hour) for s in spots]
    ranked.sort(key=lambda w: (-w.score, w.spot_name))
    return ranked
