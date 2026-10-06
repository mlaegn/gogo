from __future__ import annotations

import math
from dataclasses import dataclass

from gogo.geo import angle_distance, in_bearing_window, window_center
from gogo.models import HourForecast, HourScore, Reason, Spot, Verdict

# Bump on any change that can move a rank: weights, thresholds, gates, new terms, or the
# rule that aggregates hours into a window. A stored verdict is meaningless without it.
#
# v2 — the served unit became a range. Per-hour scoring is unchanged from v1; what
# changed is that adjacent passing hours are grouped and ranked by their mean, so the
# same forecast can now produce a different ranking.
#
# v3 — two bugs, not a retune; see S12a in docs/plan.md. Wind reads speed as well as
# direction: calm is glassy wherever it blows from, and a strong cross-shore wind can
# veto. Tide phase is read from the cycle around each hour instead of from the min and
# max of whatever series was loaded, so a backtest and the page agree on it.
SCORE_VERSION = "v3"

# Onshore is ~180° from the spot's offshore_from.
_ONSHORE_ALIGN_DEG = 75
_OFFSHORE_ALIGN_DEG = 50

# Below this the direction of the wind says nothing about the surface. Under v2, 70% of
# sub-5 kn hours on a year of reanalysis lost points for "blowing onshore" at 2 kn.
_GLASSY_KN = 5.0

# Offshore above this holds waves up and blows the lip back: still surfable, no longer
# the best case.
_STRONG_OFFSHORE_KN = 18.0

# Cross-shore is kinder than onshore, so it gets a margin over the spot's onshore cap
# before it closes the spot. A placeholder: under v2, 60% of hours with 20 kn+ of cross
# passed with the same 12 points as a gentle breeze, which nobody would argue for.
_CROSS_OVER_CAP_KN = 6.0


def verdict_for(score: int, vetoed: bool = False) -> Verdict:
    """The go / maybe / no thresholds, shared by an hour and by a window of hours."""
    return "no" if vetoed or score < 40 else "go" if score >= 70 else "maybe"


@dataclass(frozen=True)
class ScoreOptions:
    """Changes to the score that exist so the backtest can try them before they ship.

    Every field defaults to off, and `INCUMBENT` is all defaults, which is exactly the
    score `SCORE_VERSION` names — serving never passes anything else. A candidate in
    `gogo.eval.candidates` is a set of these; adopting one means making its value the
    default and bumping the version, never passing options from the serving path.
    """

    #: Period-aware size (S13, first half). Offshore height alone ignores that a long
    #: swell grows more as it shoals: breaking height goes as H^0.8 T^0.4 (Komar &
    #: Gaughan 1972), so 1.2 m at 14 s breaks about 25% bigger than 1.2 m at 8 s. Equal
    #: breaking height means the size gate can compare `H * sqrt(T / ref)` against the
    #: spot's range, which leaves an hour at the reference period exactly as it was.
    #: The square root is physics; the reference is a calibration the backtest picks.
    size_ref_period_s: float | None = None


INCUMBENT = ScoreOptions()


def effective_height_m(hour: HourForecast, options: ScoreOptions = INCUMBENT) -> float:
    """The height the size gate judges: offshore height, or its period-aware stand-in."""
    if options.size_ref_period_s is None:
        return hour.swell_height_m
    return hour.swell_height_m * math.sqrt(
        max(hour.swell_period_s, 0.0) / options.size_ref_period_s
    )


def score_hour(
    spot: Spot, hour: HourForecast, options: ScoreOptions = INCUMBENT
) -> HourScore:
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

    hs = effective_height_m(hour, options)
    # Name both numbers when the period moved the height, so the sentence still explains
    # the verdict: "0.9 m at 15 s breaks like 1.2 m, in range" is why a 1.0 m min passed.
    adjusted = round(hs, 1) != round(hour.swell_height_m, 1)
    lead = (
        f"{hour.swell_height_m:.1f} m at {hour.swell_period_s:.0f} s breaks like {hs:.1f} m, "
        if adjusted
        else f"{hs:.1f} m "
    )
    if hs < spot.size_min_m:
        reasons.append(
            Reason(
                code="size",
                detail=f"{lead}{'' if adjusted else 'is '}below this spot's "
                f"{spot.size_min_m:.1f} m min",
                points=0,
            )
        )
        vetoed = True
    elif hs > spot.size_max_m:
        reasons.append(
            Reason(
                code="size",
                detail=f"{lead}{'' if adjusted else 'looks like '}a close-out here "
                f"(max {spot.size_max_m:.1f} m)",
                points=0,
            )
        )
        vetoed = True
    else:
        mid = (spot.size_min_m + spot.size_max_m) / 2
        closeness = 1 - abs(hs - mid) / max(mid - spot.size_min_m, 0.3)
        pts = 10 + int(15 * max(0.0, min(1.0, closeness)))
        reasons.append(Reason(code="size", detail=f"{lead}in range", points=pts))

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

    reasons.append(_wind(spot, hour))
    if reasons[-1].points == 0:
        vetoed = True

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
    return HourScore(
        spot_id=spot.id,
        spot_name=spot.name,
        valid_at=hour.valid_at,
        score=total,
        verdict=verdict_for(total, vetoed),
        reasons=reasons,
        vetoed=vetoed,
    )


def _wind(spot: Spot, hour: HourForecast) -> Reason:
    """Speed first, then direction. Direction is only meaningful once there is wind."""
    kn = hour.wind_speed_kn
    onshore_from = (spot.offshore_from + 180) % 360
    onshore_align = angle_distance(hour.wind_from_deg, onshore_from)
    offshore_align = angle_distance(hour.wind_from_deg, spot.offshore_from)

    if onshore_align <= _ONSHORE_ALIGN_DEG and kn > spot.max_onshore_kn:
        return Reason(
            code="wind",
            detail=f"{kn:.0f} kn onshore (from {hour.wind_from_deg:.0f}°) is a no",
            points=0,
        )
    if kn < _GLASSY_KN:
        return Reason(code="wind", detail=f"{kn:.0f} kn, glassy", points=20)
    if offshore_align <= _OFFSHORE_ALIGN_DEG:
        if kn <= _STRONG_OFFSHORE_KN:
            return Reason(code="wind", detail=f"{kn:.0f} kn offshore", points=20)
        return Reason(code="wind", detail=f"{kn:.0f} kn offshore is strong", points=12)
    if onshore_align <= _ONSHORE_ALIGN_DEG:
        return Reason(
            code="wind", detail=f"{kn:.0f} kn onshore, still under the cap", points=6
        )
    if kn > spot.max_onshore_kn + _CROSS_OVER_CAP_KN:
        return Reason(
            code="wind",
            detail=f"{kn:.0f} kn cross-shore (from {hour.wind_from_deg:.0f}°) is a no",
            points=0,
        )
    return Reason(code="wind", detail=f"{kn:.0f} kn cross / sideshore", points=12)


def rank_hour(spots: list[Spot], hour: HourForecast) -> list[HourScore]:
    ranked = [score_hour(s, hour) for s in spots]
    ranked.sort(key=lambda w: (-w.score, w.spot_name))
    return ranked
