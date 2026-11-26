"""What is known in advance, for any spot and hour: the official tide and the daylight.

**The tide** comes from the Instituto Hidrográfico's predicted high and low waters at
Cascais and Peniche, committed as package data by `scripts/tide_table.py`. Open-Meteo's
`sea_level_height_msl` stays what the score reads today; this is the reference beside
it, and the source for what a phase cannot say. A king-tide high and a neap high both
come out "high" from `tide.attach_tide`, because each hour is judged against its own
cycle. Here they are 3.89 m and 2.6 m above chart datum.

Between turning points the level follows half a cosine, the interpolation the tide
tables themselves prescribe for intermediate times. Heights are returned above mean
sea level, the reference of the wavesim sheets' `tide_m`: the table's own mean level,
taken over the hourly curve, is subtracted from chart datum. At Cascais that is 2.08 m.
Open-Meteo's series claims the same reference but sits 38 cm below it at every spot
(`scripts/almanac_check.py`); around that offset the two agree to 8–9 cm.

A spot reads the nearest station. **Every hour is classed** by the half-cycle it sits
in, measured against the year of the table around it:

- `king`: the high water that ends or starts it is in the top 2% of that year's highs,
  or the low water is in the bottom 2% of its lows
- `spring` / `neap`: the low-to-high range is in the top / bottom quarter
- `normal`: everything else

The year is centred on the hour's month, and slides inwards at the ends of the table
so it is always a full year: a ranking against half a year that holds only the
equinoctial springs would call ordinary tides neap.

**The table is astronomy**, so it was knowable on every day a backtest replays and it
leaks nothing at an as-of. It does run out: an hour after its last turning point has no
tide here, and `gogo health` counts the days left.

**Daylight** is the NOAA solar position (Meeus's low-precision formulas), good to
about a minute here. Civil dawn and dusk are the sun at 6° below the horizon: light
enough to see the water.
"""

from __future__ import annotations

import functools
import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from gogo.models import TideClass

DATA_DIR = Path(__file__).parent / "data" / "tides"

TideKind = Literal["high", "low"]

#: Share of a year's high waters, and of its low waters, called king.
KING_SHARE = 0.02
#: Quarter of a year's half-cycle ranges at each end: below is neap, above is spring.
SPRING_NEAP_SHARE = 0.25
_YEAR = timedelta(days=365)


@dataclass(frozen=True)
class TurningPoint:
    at: datetime
    kind: TideKind
    #: Above chart datum, as the tables print it.
    height_cd_m: float
    #: Above mean sea level.
    height_m: float
    king: bool


@dataclass(frozen=True)
class TideHour:
    """The official tide at one instant."""

    #: Above mean sea level.
    height_m: float
    #: Metres per hour, positive while the tide comes in.
    rate_m_h: float
    #: Low to high of the half-cycle this instant sits in.
    range_m: float
    tide_class: TideClass


@dataclass(frozen=True)
class SunTimes:
    """One day's light, in UTC. Civil dawn and dusk are the sun 6° below the horizon."""

    civil_dawn: datetime
    sunrise: datetime
    sunset: datetime
    civil_dusk: datetime


def _utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UTC)


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[round(q * (len(ordered) - 1))]


def _year_around(at: datetime, first: datetime, last: datetime) -> tuple[datetime, datetime]:
    """The year centred on `at`'s month, slid inwards to stay inside the table."""
    centre = at.replace(day=15, hour=0, minute=0, second=0, microsecond=0)
    start, end = centre - _YEAR / 2, centre + _YEAR / 2
    if start < first:
        start, end = first, first + _YEAR
    elif end > last:
        start, end = last - _YEAR, last
    return start, end


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    lat: float
    lon: float
    source: str
    #: Mean sea level above chart datum, from the table's own hourly curve.
    mean_level_cd_m: float
    points: tuple[TurningPoint, ...]
    #: `points[i].at`, for bisecting.
    times: tuple[datetime, ...]
    #: Per half-cycle (points[i] to points[i + 1]): neap / normal / spring, before king.
    _range_class: tuple[TideClass, ...]
    moons: tuple[tuple[datetime, str], ...]

    @property
    def covers_from(self) -> datetime:
        return self.points[0].at

    @property
    def covers_until(self) -> datetime:
        return self.points[-1].at

    def _bracket(self, at: datetime) -> int | None:
        """Index i with points[i].at <= at < points[i + 1].at, or None outside."""
        i = bisect_right(self.times, at) - 1
        if i < 0 or i >= len(self.points) - 1:
            return None
        return i

    def at(self, at: datetime) -> TideHour | None:
        """The tide at an instant, or None outside the table."""
        i = self._bracket(at)
        if i is None:
            return None
        p0, p1 = self.points[i], self.points[i + 1]
        span_h = (p1.at - p0.at).total_seconds() / 3600
        phase = (at - p0.at).total_seconds() / 3600 / span_h
        rise = p1.height_m - p0.height_m
        height = p0.height_m + rise * (1 - math.cos(math.pi * phase)) / 2
        rate = rise * math.pi / 2 * math.sin(math.pi * phase) / span_h
        tide_class: TideClass = "king" if p0.king or p1.king else self._range_class[i]
        return TideHour(
            height_m=round(height, 3),
            rate_m_h=round(rate, 3),
            range_m=round(abs(rise), 2),
            tide_class=tide_class,
        )

    def between(self, start: datetime, end: datetime) -> list[TurningPoint]:
        """High and low waters with start <= at < end."""
        return [p for p in self.points if start <= p.at < end]


def _build(raw: dict) -> Station:
    events = [(_utc(t), "high" if k == "H" else "low", float(h)) for t, k, h in raw["events"]]
    # Mean of the cosine curve over a half-cycle is the mean of its two ends, so the
    # time-weighted mean of those is the mean of the hourly curve.
    weighted = sum(
        (h0 + h1) / 2 * (t1 - t0).total_seconds()
        for (t0, _, h0), (t1, _, h1) in zip(events, events[1:], strict=False)
    )
    mean = weighted / (events[-1][0] - events[0][0]).total_seconds()
    first, last = events[0][0], events[-1][0]

    @functools.cache
    def thresholds(year: tuple[datetime, datetime]) -> tuple[float, float, float, float]:
        start, end = year
        inside = [e for e in events if start <= e[0] < end]
        highs = [h for _, k, h in inside if k == "high"]
        lows = [h for _, k, h in inside if k == "low"]
        ranges = [
            abs(h1 - h0)
            for (_, _, h0), (_, _, h1) in zip(inside, inside[1:], strict=False)
        ]
        return (
            _quantile(highs, 1 - KING_SHARE),
            _quantile(lows, KING_SHARE),
            _quantile(ranges, SPRING_NEAP_SHARE),
            _quantile(ranges, 1 - SPRING_NEAP_SHARE),
        )

    points = []
    for t, kind, h in events:
        king_high, king_low, _, _ = thresholds(_year_around(t, first, last))
        king = h >= king_high if kind == "high" else h <= king_low
        points.append(
            TurningPoint(at=t, kind=kind, height_cd_m=h, height_m=round(h - mean, 3), king=king)
        )

    classes: list[TideClass] = []
    for (t0, _, h0), (_, _, h1) in zip(events, events[1:], strict=False):
        _, _, neap, spring = thresholds(_year_around(t0, first, last))
        r = abs(h1 - h0)
        classes.append("neap" if r <= neap else "spring" if r >= spring else "normal")

    return Station(
        id=raw["station"],
        name=raw["name"],
        lat=raw["lat"],
        lon=raw["lon"],
        source=raw["source"],
        mean_level_cd_m=round(mean, 3),
        points=tuple(points),
        times=tuple(p.at for p in points),
        _range_class=tuple(classes),
        moons=tuple((_utc(t), phase) for t, phase in raw["moons"]),
    )


@functools.cache
def stations(data_dir: Path = DATA_DIR) -> dict[str, Station]:
    """Every committed tide table, by station id."""
    return {
        p.stem: _build(json.loads(p.read_text())) for p in sorted(data_dir.glob("*.json"))
    }


def station_for(lat: float, lon: float) -> Station:
    """The nearest station. Checked against a year of Open-Meteo at every spot
    (`scripts/almanac_check.py`): nearest is the better fit or a tie everywhere,
    including the Ericeira spots, which sit between the two."""
    k = math.cos(math.radians(lat))

    def distance(s: Station) -> float:
        return math.hypot(s.lat - lat, (s.lon - lon) * k)

    return min(stations().values(), key=distance)


def tide_at(lat: float, lon: float, at: datetime) -> TideHour | None:
    """The official tide at a place and instant, or None outside the table."""
    return station_for(lat, lon).at(at)


def covered_until() -> datetime:
    """The last instant every station has a tide for."""
    return min(s.covers_until for s in stations().values())


# --- the sun ----------------------------------------------------------------------

_SUNRISE_ALT = -0.833  # refraction and the sun's radius
_CIVIL_ALT = -6.0


def _sun(at: datetime) -> tuple[float, float]:
    """Declination (degrees) and equation of time (minutes) at an instant."""
    jd = at.timestamp() / 86400 + 2440587.5
    t = (jd - 2451545.0) / 36525
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (
        math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
        + math.sin(3 * mr) * 0.000289
    )
    omega = math.radians(125.04 - 1934.136 * t)
    apparent = math.radians(l0 + c - 0.00569 - 0.00478 * math.sin(omega))
    eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    eps = math.radians(eps0 + 0.00256 * math.cos(omega))
    declination = math.degrees(math.asin(math.sin(eps) * math.sin(apparent)))
    y = math.tan(eps / 2) ** 2
    l0r = math.radians(l0)
    eot = 4 * math.degrees(
        y * math.sin(2 * l0r)
        - 2 * e * math.sin(mr)
        + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
        - 0.5 * y * y * math.sin(4 * l0r)
        - 1.25 * e * e * math.sin(2 * mr)
    )
    return declination, eot


def _crossing(day: date, lat: float, lon: float, altitude: float, rising: bool) -> datetime:
    """When the sun passes `altitude` on `day` (a UTC date), refined twice."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    guess = midnight + timedelta(hours=12)
    for _ in range(3):
        declination, eot = _sun(guess)
        phi, delta = math.radians(lat), math.radians(declination)
        cos_h = (math.sin(math.radians(altitude)) - math.sin(phi) * math.sin(delta)) / (
            math.cos(phi) * math.cos(delta)
        )
        if not -1 <= cos_h <= 1:
            raise ValueError(f"the sun does not cross {altitude}° at {lat} on {day}")
        hour_angle = math.degrees(math.acos(cos_h))
        noon_min = 720 - 4 * lon - eot
        minutes = noon_min - 4 * hour_angle if rising else noon_min + 4 * hour_angle
        guess = midnight + timedelta(minutes=minutes)
    return guess.replace(second=0, microsecond=0) + timedelta(
        minutes=round(guess.second / 60)
    )


def sun_times(day: date, lat: float, lon: float) -> SunTimes:
    """The light on a day. `day` is the Lisbon date: on this coast every crossing falls
    between 04:00 and 22:00 UTC, so the Lisbon date and the UTC date are the same day."""
    return SunTimes(
        civil_dawn=_crossing(day, lat, lon, _CIVIL_ALT, rising=True),
        sunrise=_crossing(day, lat, lon, _SUNRISE_ALT, rising=True),
        sunset=_crossing(day, lat, lon, _SUNRISE_ALT, rising=False),
        civil_dusk=_crossing(day, lat, lon, _CIVIL_ALT, rising=False),
    )
