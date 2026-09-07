"""What gets served, in one place.

The JSON API and the page must answer identically — two copies of "pick a day, score
it, write down what we showed" would drift, and the impression log would stop being a
record of what anyone actually saw.

Reading only. The worker writes; `/windows` must never call Open-Meteo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from gogo.assemble import available_days, scored_hours, windows_for_day
from gogo.clock import now_utc
from gogo.models import HourScore, Spot, WindowScore
from gogo.spots import load_spots
from gogo.store import (
    connection,
    current_as_of,
    load_current_hours,
    record_impressions,
)


class NoForecast(Exception):
    """Nothing stored to score. The worker has not run, or has not run recently."""


class UnknownDay(Exception):
    """A day we hold no surfable hours for."""


@dataclass
class Served:
    day: date
    days: list[date]
    windows: list[WindowScore]
    as_of: datetime | None
    spots: list[Spot]

    @property
    def best(self) -> WindowScore | None:
        """The headline. None when every spot is a no, which is itself the answer."""
        top = next((w for w in self.windows if w.verdict != "no"), None)
        return top


def serve(day: date | None = None, surface: str | None = None) -> Served:
    """Score one local day from stored forecasts.

    `day` defaults to the next day with surfable hours left in it — today until the
    evening runs out, then tomorrow. `surface` records an impression under that name;
    pass None for views that are not a recommendation, so the log stays a record of what
    we put in front of someone rather than of every page they opened.
    """
    spots = load_spots()
    now = now_utc()
    with connection() as conn:
        hours = load_current_hours(conn, spots)
        if not hours:
            raise NoForecast("No stored forecasts. Run gogo fetch.")
        days = available_days(hours, not_before=now)
        if not days:
            raise NoForecast("Stored forecasts have all expired. Run gogo fetch.")
        chosen = day or days[0]
        if chosen not in days:
            raise UnknownDay(f"No surfable hours stored for {chosen.isoformat()}.")

        windows = windows_for_day(spots, hours, chosen, not_before=now)
        as_of = current_as_of(conn)
        if surface is not None and as_of is not None:
            record_impressions(conn, windows, spots, as_of, surface=surface)
    return Served(day=chosen, days=days, windows=windows, as_of=as_of, spots=spots)


def serve_spot(spot: Spot, day: date) -> list[HourScore]:
    """One spot's day hour by hour. Not a recommendation, so nothing is recorded."""
    now = now_utc()
    with connection() as conn:
        hours = load_current_hours(conn, [spot])
    return scored_hours(spot, hours, day, not_before=now)
