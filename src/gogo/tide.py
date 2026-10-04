"""Derive low/mid/high and incoming/outgoing from an hourly sea-level series.

**The phase of an hour is judged against the tide cycle around it, not the series it
arrived in.** It used to be relative to the min and max of whatever was passed, which
made the answer depend on the query: serving passes about a week, a backtest passed one
day, and over a year of reanalysis 12% of hours came out a different phase in the two.
A week also spans springs and neaps, so a neap day classified against a spring range
read as "mid" from dawn to dusk. A backtest that classifies differently from serving is
measuring a score nobody was shown.

So each hour looks at `TIDE_CONTEXT` either side of itself — a little over half the
12 h 25 m semi-diurnal cycle, so the window always holds a low and a high — and the
answer for any hour with that much data around it is the same whatever the caller
loaded. Callers that want an hour classified must load the context with it:
`features_for_day` pads its day and `load_current_hours` looks back far enough.

"Low" therefore means low *for this cycle*, which is also what a tide table and a
surfer mean by it. S12 replaces the phase with height plus rate of change.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta

from gogo.models import TidePhase, TideTrend

#: How far either side of an hour its tide cycle is read from.
TIDE_CONTEXT = timedelta(hours=7)

_HOUR = timedelta(hours=1)


def _phase(z: float, lo: float, hi: float) -> TidePhase:
    frac = (z - lo) / (hi - lo)
    if frac < 0.33:
        return "low"
    if frac < 0.67:
        return "mid"
    return "high"


def attach_tide(
    times: list[datetime],
    levels: list[float | None],
) -> dict[datetime, tuple[TidePhase, TideTrend]]:
    """Phase and trend for every hour with a known level. A null level is skipped.

    Near either end of the series the window slides inwards rather than shrinking, so an
    edge hour is still judged against a full cycle when the series holds one.
    """
    known = {t: z for t, z in zip(times, levels, strict=True) if z is not None}
    if not known:
        return {}
    ordered = sorted(known)
    series = [known[t] for t in ordered]
    first, last = ordered[0], ordered[-1]

    out: dict[datetime, tuple[TidePhase, TideTrend]] = {}
    for t in ordered:
        z = known[t]
        start, end = t - TIDE_CONTEXT, t + TIDE_CONTEXT
        if start < first:
            start, end = first, end + (first - start)
        elif end > last:
            start, end = start - (end - last), last
        cycle = series[bisect_left(ordered, start) : bisect_right(ordered, end)]
        lo, hi = min(cycle), max(cycle)

        rising = known.get(t + _HOUR, z) - known.get(t - _HOUR, z)
        if abs(rising) < 0.05:
            trend: TideTrend = "slack"
        elif rising > 0:
            trend = "incoming"
        else:
            trend = "outgoing"

        if hi - lo < 0.15:
            out[t] = ("mid", "slack")
        else:
            out[t] = (_phase(z, lo, hi), trend)
    return out
