from __future__ import annotations

import functools
import math
from dataclasses import dataclass

from gogo.geo import angle_delta, angle_distance, in_bearing_window, window_center
from gogo.models import HourForecast, HourScore, Reason, Spot, Verdict
from gogo.rays import factor, load_tables

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

    #: Period-aware size with the spot ranges re-anchored. A fixed reference stretches
    #: both tails on this coast, because height and period correlate (0.62): small days
    #: are short-period and shrink, big days are long-period and grow, so a fixed
    #: reference closes the bottom and the top of every `size_min_m`..`size_max_m`.
    #: Those ranges were written in raw height and already assume that correlation.
    #: This compares the hour with a *typical* day instead — the height whose usual
    #: period (`TYPICAL_PERIOD`) breaks the same — so a typical day scores exactly as it
    #: does today, the reference cancels out, and only what period says beyond height
    #: moves the gate: long-period small swell up, short-period big windswell down.
    size_period_typical: bool = False

    #: Direction taper (S13), on `open` spots only. Outside the window the swell is let
    #: through for this many degrees past the nearest edge instead of being vetoed at
    #: the first one, and the direction points fade from 10 to 0 across that width.
    #: Forecast direction is easily ±15° wrong, so a hard edge mostly judges noise:
    #: under v2, 60–97% of direction-only vetoes were within 15° of one.
    #:
    #: The height it lets through is cut by refraction, read off the measured shore
    #: normal: over straight contours an oblique swell keeps sqrt(cos α) of its height,
    #: α its angle off `faces_deg`. Taken relative to the window edge, where the spot's
    #: size range was written, so the factor is 1 at the edge and falls to 0 as the
    #: swell turns side-on. A plain cosine fade over the width was tried first and let
    #: 23–29% of Foz, Empa and Pedra Branca's hours through, because their windows end
    #: at 320° and the very common 330–340° swell is 65–85° oblique there.
    #:
    #: Closed regardless: a `sheltered` spot keeps its hard window, because there the
    #: edge is a headland, not an angle; and a swell 90° or more off `faces_deg` would
    #: be arriving from the land side.
    dir_taper_deg: float | None = None

    #: Shadow of the land (S15, first half): how fast a swell's height falls off as it
    #: comes from further into the lee of a headland, in degrees at a 10 s period. Uses
    #: the measured `shadow_sectors`. Shaped on diffraction past the end of a barrier:
    #: half the height reaches the geometric shadow boundary, it decays exponentially
    #: into the lee and recovers the same way on the open side. Scaled with period,
    #: because the angle a wave spreads into a shadow goes with its wavelength's square
    #: root, i.e. with T: a 14 s groundswell bends 40% further round Peniche than a 10 s
    #: one, which is why Supertubos wants long north-west swell. Refraction over the
    #: shelf also bends swell round a headland and is not modelled separately; the rate
    #: absorbs it, which is why it is a setting for the backtest rather than a constant.
    #: Below `LEE_FLOOR` of the height the direction is vetoed. Applies at every spot
    #: with measured sectors, open or sheltered — Baleal sits in the lee of Peniche from
    #: the south-west whatever its overall exposure. It only ever cuts height, which
    #: closes hours in the lee and can open exactly one kind: a close-out. A swell too
    #: big for the open beaches can be the right size behind the headland, which is the
    #: substitution S15 is after ("too big here, go round the peninsula").
    shadow_deg: float | None = None

    #: Wave rays over the real seabed (S13e). The hand-drawn window, the taper and the
    #: fitted shadow are all stand-ins for one physical quantity: how much of an open-sea
    #: swell refraction delivers to the spot, by direction and period. `data/rays.json`
    #: holds it, traced over EMODnet bathymetry by `scripts/wave_rays.py` and checked
    #: against the Nazaré canyon and sixteen things locals know about these spots. With
    #: this on, that factor decides the direction and scales the height, and the hand
    #: window is not read at all. The factor is taken relative to the spot's best
    #: direction at the period typical for the middle of its size range, so the size
    #: range keeps meaning what it was written to mean: offshore height on the kind of
    #: swell the spot is known for. Excludes the taper and the shadow, which it replaces.
    rays: bool = False

    #: Rays for size only: the traced factor scales the height, but the hand window
    #: still decides direction. Rays measure energy arriving, not whether it breaks
    #: well — a point needs swell lined up with it, a bank closes out on the wrong
    #: angle — and a window encodes that as well as the shelter. With `rays` alone
    #: over a year, a third of the swell at the Ericeira cell (320–20°) opened beaches
    #: whose windows end at 320°, and headline "no" days fell from 19 to 3.
    rays_keep_window: bool = False

    def __post_init__(self) -> None:
        if self.size_ref_period_s is not None and self.size_period_typical:
            raise ValueError("size_ref_period_s and size_period_typical are exclusive")
        if self.rays and (self.dir_taper_deg is not None or self.shadow_deg is not None):
            raise ValueError("rays replaces dir_taper and shadow; use one or the other")
        if self.rays_keep_window and not self.rays:
            raise ValueError("rays_keep_window needs rays")


INCUMBENT = ScoreOptions()

#: Median Open-Meteo *mean* swell period by swell height on this coast, at 0.25 m bin
#: centres: ERA5 through the marine archive, 2025-09-01..2026-09-03, all seven cells,
#: 61,824 hours, bins with at least 300 hours. A constant on purpose — the score is
#: pure and must not read the database — so re-measuring it is an explicit edit with a
#: backtest attached. Monotone, which `typical_equivalent_m` relies on; a test pins it.
#: The forecast model looked 0.6–0.7 s shorter at 1–1.5 m over its first two weeks
#: (n≈3400, September, small); recheck against months of `forecast_snapshots`.
TYPICAL_PERIOD: tuple[tuple[float, float], ...] = (
    (0.375, 6.5), (0.625, 6.5), (0.875, 6.9), (1.125, 7.5), (1.375, 7.8),
    (1.625, 8.2), (1.875, 8.4), (2.125, 8.7), (2.375, 9.0), (2.625, 9.4),
    (2.875, 9.7), (3.125, 10.0), (3.375, 10.1), (3.625, 10.4), (3.875, 10.5),
    (4.125, 10.5), (4.375, 10.8), (4.625, 10.8), (4.875, 10.9), (5.125, 10.9),
)


def typical_period_s(height_m: float) -> float:
    """The usual mean period for a swell this size here. Linear between bins, flat
    beyond the ends."""
    points = TYPICAL_PERIOD
    if height_m <= points[0][0]:
        return points[0][1]
    for (h0, t0), (h1, t1) in zip(points, points[1:], strict=False):
        if height_m <= h1:
            return t0 + (t1 - t0) * (height_m - h0) / (h1 - h0)
    return points[-1][1]


def typical_equivalent_m(height_m: float, period_s: float) -> float:
    """The height of a typical day that breaks like this one.

    Solves `h * sqrt(typical_period_s(h)) == height_m * sqrt(period_s)`: equal breaking
    height, the same square root as the fixed-reference version. The left side rises
    strictly with `h`, so bisection always finds the one answer.
    """
    target = height_m * math.sqrt(max(period_s, 0.0))
    lo, hi = 0.0, max(2 * height_m, 1.0)
    while hi * math.sqrt(typical_period_s(hi)) < target:
        hi *= 2
    for _ in range(50):
        mid = (lo + hi) / 2
        if mid * math.sqrt(typical_period_s(mid)) < target:
            lo = mid
        else:
            hi = mid
    found = (lo + hi) / 2
    # A typical day must come back as itself, not as 0.44999… for 0.45, which would
    # print as a different number from the incumbent's while meaning the same one.
    return height_m if math.isclose(found, height_m, abs_tol=1e-9) else found


def effective_height_m(hour: HourForecast, options: ScoreOptions = INCUMBENT) -> float:
    """The height the size gate judges: offshore height, or its period-aware stand-in."""
    if options.size_period_typical:
        return typical_equivalent_m(hour.swell_height_m, hour.swell_period_s)
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
    wrap = 1.0
    traced = _rays(spot, hour, options)
    if traced is not None and options.rays_keep_window:
        # The window decides direction below; the rays only scale the height, and a
        # swell that barely arrives is still closed.
        reason, wrap = traced
        if reason.points == 0:
            reasons.append(reason)
            vetoed = True
            traced = None
    if traced is not None and not options.rays_keep_window:
        reason, wrap = traced
        reasons.append(reason)
        vetoed = reason.points == 0
    elif vetoed:
        pass
    elif not swell_ok or off_swell > 90:
        tapered = _wrapping_in(spot, hour, options)
        if tapered is None:
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
        else:
            excess, wrap = tapered
            width = options.dir_taper_deg or 1.0
            reasons.append(
                Reason(
                    code="swell_dir",
                    detail=(
                        f"swell {hour.swell_from_deg:.0f}° is {excess:.0f}° outside "
                        f"{spot.swell_from_min}–{spot.swell_from_max}°, wrapping in"
                    ),
                    points=round(10 * (1 - excess / width)),
                )
            )
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

    lee = None if vetoed else _in_the_lee(spot, hour, options)
    if lee is not None:
        last = reasons[-1]
        if lee < LEE_FLOOR:
            reasons[-1] = Reason(
                code="swell_dir",
                detail=(
                    f"swell {hour.swell_from_deg:.0f}° is in the lee of land here, "
                    f"about {lee:.0%} gets in"
                ),
                points=0,
            )
            vetoed = True
        else:
            wrap *= lee
            if lee < 0.9:
                reasons[-1] = last.model_copy(
                    update={"detail": f"{last.detail}; in the lee of land, ~{lee:.0%} gets in"}
                )

    hs = effective_height_m(hour, options) * wrap
    if hs < spot.size_min_m:
        side = "below"
    elif hs > spot.size_max_m:
        side = "above"
    else:
        side = "in"
    lead = _size_lead(hour, hs, side, options, wrapped=abs(wrap - 1.0) > 0.005)
    if side == "below":
        reasons.append(
            Reason(
                code="size",
                detail=f"{lead} below this spot's {spot.size_min_m:.1f} m min",
                points=0,
            )
        )
        vetoed = True
    elif side == "above":
        reasons.append(
            Reason(
                code="size",
                detail=f"{lead} a close-out here (max {spot.size_max_m:.1f} m)",
                points=0,
            )
        )
        vetoed = True
    else:
        mid = (spot.size_min_m + spot.size_max_m) / 2
        closeness = 1 - abs(hs - mid) / max(mid - spot.size_min_m, 0.3)
        pts = 10 + int(15 * max(0.0, min(1.0, closeness)))
        reasons.append(Reason(code="size", detail=f"{lead} in range", points=pts))

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


def _shown(value: float, digits: int, side: str) -> str:
    """A height as printed beside a verdict, rounded towards the side that explains it.

    Ordinary rounding prints 0.76 m as "0.8 m is below this spot's 0.8 m min". Beside a
    veto the number is rounded away from the limit, so what is shown never contradicts
    what was decided.
    """
    scale = 10**digits
    if side == "below":
        value = math.floor(value * scale) / scale
    elif side == "above":
        value = math.ceil(value * scale) / scale
    return f"{value:.{digits}f}"


def _size_lead(
    hour: HourForecast, hs: float, side: str, options: ScoreOptions, wrapped: bool = False
) -> str:
    """The first half of the size sentence: the height, and what it breaks like.

    Both numbers are named when the period or a wrapping direction moved the height, so
    the sentence still explains the verdict: "1.1 m at 15 s breaks like 1.5 m, in range"
    is why a 1.2 m minimum passed, and "1.6 m from 345° breaks like 1.2 m" is why a
    swell outside the window still counts. Two decimals only where one would print the
    same number twice.
    """
    raw = hour.swell_height_m
    if abs(hs - raw) < 0.01:
        verb = {"below": "is", "above": "looks like", "in": ""}[side]
        return f"{_shown(hs, 1, side)} m {verb}".rstrip()
    digits = 2 if _shown(hs, 1, side) == f"{raw:.1f}" else 1
    period_moved = options.size_ref_period_s is not None or options.size_period_typical
    how = (f" at {hour.swell_period_s:.0f} s" if period_moved else "") + (
        f" from {hour.swell_from_deg:.0f}°" if wrapped else ""
    )
    like = "a typical " if options.size_period_typical else ""
    return f"{raw:.{digits}f} m{how} breaks like {like}{_shown(hs, digits, side)} m,"


#: Below this share of its height, a swell from the lee of the land is a veto.
LEE_FLOOR = 0.15


@functools.cache
def _best_factor(spot_id: str, period: float) -> float:
    """The most any direction delivers to the spot at this period: its best swell.

    The anchor the size range is read against. Not the window's centre: where a drafted
    window disagrees with the seabed — Lagide faces north with a 250–330° window — the
    centre can be a poor direction, and every other one would look like a gain."""
    table = load_tables()[spot_id]
    return max(factor(table, d, period) for d in table.directions)


def _rays(spot: Spot, hour: HourForecast, options: ScoreOptions) -> tuple[Reason, float] | None:
    """The direction reason and height factor from the traced table, or None when the
    option is off or the spot has no table."""
    if not options.rays:
        return None
    table = load_tables().get(spot.id)
    if table is None:
        return None
    here = factor(table, hour.swell_from_deg, hour.swell_period_s)
    best = _best_factor(spot.id, typical_period_s((spot.size_min_m + spot.size_max_m) / 2))
    share = min(here / best, 1.5) if best > 0 else 0.0
    deg = f"swell {hour.swell_from_deg:.0f}°"
    if share < LEE_FLOOR:
        return Reason(
            code="swell_dir",
            detail=f"{deg} barely reaches here, ~{share:.0%} of what its best swell does",
            points=0,
        ), share
    if share >= 0.9:
        detail = f"{deg} reaches here in full"
    else:
        detail = f"{deg} wraps in at ~{share:.0%} of what its best swell does"
    return Reason(code="swell_dir", detail=detail, points=round(20 * min(1.0, share))), share


def _in_the_lee(spot: Spot, hour: HourForecast, options: ScoreOptions) -> float | None:
    """The share of the swell's height the land lets through, or None if not asked or
    nothing is measured.

    The swell is treated as coming from a spread of directions around its bearing — a
    two-sided exponential whose width is `shadow_deg` scaled by period — and the share
    is the part of that spread with a clear path to open sea, i.e. not inside a shadow
    sector. Against one long headland this is exactly the diffraction shape: half at the
    geometric shadow boundary, decaying into the lee and recovering on the open side.
    Against a small island it stays small, because the island blocks only a sliver of
    the spread — the Berlengas, 15 km off Baleal and a few degrees wide, cannot put
    Baleal in the dark. An arc start 88° or more anticlockwise of the facing, or an end
    as far clockwise, was cut by the scan rather than by open sea, so the arc is taken
    to run on into the land side there.
    """
    if options.shadow_deg is None or not spot.shadow_sectors or spot.faces_deg is None:
        return None
    scale = options.shadow_deg * max(hour.swell_period_s, 1.0) / 10.0

    def cdf(u: float) -> float:
        return 0.5 * math.exp(u / scale) if u < 0 else 1.0 - 0.5 * math.exp(-u / scale)

    blocked = 0.0
    for start, end in spot.shadow_sectors:
        lo = angle_delta(start, hour.swell_from_deg)
        hi = lo + (end - start) % 360
        # The scan runs from 89° anticlockwise of the facing to 89° clockwise of it, so
        # only a start can be cut at the first limit and only an end at the second.
        if angle_delta(start, spot.faces_deg) <= -88:
            lo -= 90
        if angle_delta(end, spot.faces_deg) >= 88:
            hi += 90
        blocked += cdf(hi) - cdf(lo)
    return max(0.0, min(1.0, 1.0 - blocked))


def _wrapping_in(
    spot: Spot, hour: HourForecast, options: ScoreOptions
) -> tuple[float, float] | None:
    """(degrees outside the window, height factor) when the taper lets this swell in.

    None means the hard veto stands: no taper asked for, a sheltered spot or one with
    no measured geometry, a swell from the land side, one past the taper's width, or a
    nearest edge that itself lies side-on to the beach, where there is nothing to
    measure the refraction against.
    """
    width = options.dir_taper_deg
    if width is None or spot.exposure != "open" or spot.faces_deg is None:
        return None
    off_normal = angle_distance(hour.swell_from_deg, spot.faces_deg)
    if off_normal >= 90:
        return None
    to_min = angle_distance(hour.swell_from_deg, spot.swell_from_min)
    to_max = angle_distance(hour.swell_from_deg, spot.swell_from_max)
    excess = min(to_min, to_max)
    if excess >= width:
        return None
    edge = spot.swell_from_min if to_min <= to_max else spot.swell_from_max
    edge_cos = math.cos(math.radians(angle_distance(edge, spot.faces_deg)))
    if edge_cos <= 0:
        return None
    factor = math.sqrt(math.cos(math.radians(off_normal)) / edge_cos)
    return excess, min(1.0, factor)


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
