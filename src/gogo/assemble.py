from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from gogo.clock import to_local
from gogo.ingest.protocol import GridHour
from gogo.models import HourForecast, HourScore, Spot, WindowScore
from gogo.score import score_hour, verdict_for
from gogo.tide import attach_tide


def forecasts_from_grid(hours: list[GridHour]) -> list[HourForecast]:
    """Attach tide phase using the sea-level series of each marine grid cell."""
    by_cell: dict[tuple[float, float], list[GridHour]] = defaultdict(list)
    for h in hours:
        by_cell[(h.grid_lat, h.grid_lon)].append(h)

    out: list[HourForecast] = []
    for group in by_cell.values():
        group = sorted(group, key=lambda g: g.valid_at)
        tides = attach_tide(
            [g.valid_at for g in group],
            [g.sea_level_m for g in group],
        )
        for g in group:
            phase = tides.get(g.valid_at)
            out.append(
                HourForecast(
                    valid_at=g.valid_at,
                    swell_height_m=g.swell_height_m,
                    swell_from_deg=g.swell_from_deg,
                    swell_period_s=g.swell_period_s,
                    wind_wave_height_m=g.wind_wave_height_m,
                    wind_speed_kn=g.wind_speed_kn,
                    wind_from_deg=g.wind_from_deg,
                    wind_gusts_kn=g.wind_gusts_kn,
                    sea_level_m=g.sea_level_m,
                    tide=phase[0] if phase else None,
                    tide_trend=phase[1] if phase else None,
                )
            )
    return out


# Hours a person might plausibly surf, in local time. A placeholder for real daylight:
# without any bound the run finder happily reports 03:00–05:00 as the day's best window,
# because nothing in the score knows the sun is down. S12 replaces this with sunrise and
# sunset, at which point these constants go away.
SURFABLE_FROM_HOUR = 6
SURFABLE_UNTIL_HOUR = 20


def plan_day(hours: list[GridHour], not_before: datetime | None = None) -> date | None:
    """The local day to plan for: the next Saturday in the series, else the first day.

    A *local* date, because "Saturday" is a Lisbon concept and a UTC date can disagree
    with it either side of midnight.

    `not_before` drops hours that have already happened. Serving paths pass `now`;
    `forecast_current` keeps every hour ever fetched, so without it a Saturday that has
    been and gone can outrank the one being asked about. Fixtures pass nothing, because
    a golden replay is deliberately historical.
    """
    if not hours:
        return None
    instants = sorted({h.valid_at for h in hours})
    if not_before is not None:
        instants = [t for t in instants if t >= not_before]
    if not instants:
        return None
    local = [to_local(t) for t in instants]
    saturdays = [lt.date() for lt in local if lt.weekday() == 5]
    return saturdays[0] if saturdays else local[0].date()


def _surfable_on(series: list[HourForecast], day: date) -> list[HourForecast]:
    """The day's hours a person could actually be in the water, in order."""
    chosen = []
    for hour in series:
        local = to_local(hour.valid_at)
        if local.date() == day and SURFABLE_FROM_HOUR <= local.hour < SURFABLE_UNTIL_HOUR:
            chosen.append(hour)
    return sorted(chosen, key=lambda h: h.valid_at)


def runs_of_passing_hours(scored: list[HourScore]) -> list[list[HourScore]]:
    """Split an ordered day into maximal runs of adjacent hours that all pass.

    A "no" hour ends a run rather than being averaged into it: a session is continuous,
    and 25 kn of onshore at 10:00 does not become tolerable because 09:00 was clean. A
    gap in the data ends one too, because two hours either side of a hole are not
    evidence of anything in between.
    """
    runs: list[list[HourScore]] = []
    current: list[HourScore] = []
    for hour in scored:
        broken = hour.verdict == "no" or (
            current and hour.valid_at - current[-1].valid_at != timedelta(hours=1)
        )
        if broken and current:
            runs.append(current)
            current = []
        if hour.verdict != "no":
            current.append(hour)
    if current:
        runs.append(current)
    return runs


def window_from_run(run: list[HourScore]) -> WindowScore:
    """The aggregation rule, in one place: mean over members, reasons from the hour
    nearest that mean, peak kept alongside."""
    mean = round(sum(h.score for h in run) / len(run))
    peak = max(run, key=lambda h: h.score)
    # The hour that best represents the number being shown, so the sentence and the
    # score agree. Ties go to the earlier hour, which is the one you would paddle out in.
    speaks_for = min(run, key=lambda h: (abs(h.score - mean), h.valid_at))
    return WindowScore(
        spot_id=run[0].spot_id,
        spot_name=run[0].spot_name,
        starts_at=run[0].valid_at,
        ends_at=run[-1].valid_at + timedelta(hours=1),
        peak_at=peak.valid_at,
        hours=len(run),
        score=mean,
        peak_score=peak.score,
        verdict=verdict_for(mean),
        reasons=speaks_for.reasons,
        vetoed=False,
    )


def _closed_window(scored: list[HourScore]) -> WindowScore:
    """No hour passed. Report the whole span and explain the one that came closest."""
    best = max(scored, key=lambda h: h.score)
    return WindowScore(
        spot_id=scored[0].spot_id,
        spot_name=scored[0].spot_name,
        starts_at=scored[0].valid_at,
        ends_at=scored[-1].valid_at + timedelta(hours=1),
        peak_at=best.valid_at,
        hours=len(scored),
        score=best.score,
        peak_score=best.score,
        verdict=verdict_for(best.score, best.vetoed),
        reasons=best.reasons,
        vetoed=best.vetoed,
    )


def windows_for_day(
    spots: list[Spot], hours: list[GridHour], day: date
) -> list[WindowScore]:
    """Best window per spot for one local day, ranked.

    One window per spot, because the list ranks spots. A spot with a clean morning and a
    clean evening keeps the better of the two; showing both is a UI concern, not a
    ranking one.

    Ranked by score, then by duration: between two equal means, the longer window is the
    better drive. Each spot reads the grid series fetched for its own lat/lon.
    """
    by_request: dict[tuple[float, float], list[GridHour]] = defaultdict(list)
    for h in hours:
        by_request[(h.requested_lat, h.requested_lon)].append(h)

    ranked: list[WindowScore] = []
    for spot in spots:
        series = forecasts_from_grid(by_request.get((spot.lat, spot.lon), []))
        surfable = _surfable_on(series, day)
        if not surfable:
            continue
        scored = [score_hour(spot, hour) for hour in surfable]
        runs = runs_of_passing_hours(scored)
        if runs:
            ranked.append(
                max(
                    (window_from_run(run) for run in runs),
                    key=lambda w: (w.score, w.hours),
                )
            )
        else:
            ranked.append(_closed_window(scored))
    ranked.sort(key=lambda w: (-w.score, -w.hours, w.spot_name))
    return ranked
