"""The almanac: the official tide table and the daylight, both known in advance."""

import math
from datetime import UTC, date, datetime, timedelta

import pytest

from gogo.almanac import (
    _build,
    covered_until,
    station_for,
    stations,
    sun_times,
    tide_at,
)
from gogo.spots import load_spots
from gogo.worker import TIDE_TABLE_MIN_DAYS, Health


def _at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=UTC)


# --- the committed tables ------------------------------------------------------------


def test_every_table_is_an_unbroken_alternation_of_high_and_low():
    """The API drops a tide when a moon phase lands on the same minute, and an early
    walk lost a day a week; `scripts/tide_table.py` refuses to write either. This holds
    the committed files to the same rule."""
    for st in stations().values():
        for p0, p1 in zip(st.points, st.points[1:], strict=False):
            assert p0.kind != p1.kind, f"{st.name}: two {p0.kind}s at {p0.at}, {p1.at}"
            assert p1.at - p0.at < timedelta(hours=9), f"{st.name}: hole after {p0.at}"


def test_both_stations_cover_three_years():
    for st in stations().values():
        assert st.covers_from <= _at("2025-01-01 06:00")
        assert st.covers_until >= _at("2027-12-31 18:00")
    assert covered_until() >= _at("2027-12-31 18:00")


def test_mean_sea_level_is_the_cascais_reference():
    """Chart datum at Cascais lies about 2.08 m below mean sea level; the table's own
    hourly mean has to land there or every height above MSL is shifted."""
    assert stations()["cascais"].mean_level_cd_m == pytest.approx(2.08, abs=0.02)
    assert stations()["peniche"].mean_level_cd_m == pytest.approx(2.0, abs=0.05)


# --- the curve between turning points ------------------------------------------------


def test_the_curve_passes_through_every_turning_point_at_slack_water():
    st = stations()["cascais"]
    for p in st.points[100:200]:
        hour = st.at(p.at)
        assert hour.height_m == pytest.approx(p.height_m, abs=0.001)
        assert hour.rate_m_h == pytest.approx(0, abs=0.001)


def _synthetic(*events: tuple[str, str, float]) -> dict:
    return {
        "station": "test",
        "name": "Test",
        "lat": 38.7,
        "lon": -9.4,
        "source": "synthetic",
        "events": [list(e) for e in events],
        "moons": [],
    }


def test_half_a_cosine_between_low_and_high():
    """Low 0.5 m at 00:00, high 3.5 m at 06:00. Halfway is the mean of the two and the
    fastest flow, π/2 × 3 m over 6 h; a quarter of the way is a quarter of the rise
    by the cosine, not by a straight line."""
    st = _build(
        _synthetic(
            ("2027-01-01T00:00Z", "L", 0.5),
            ("2027-01-01T06:00Z", "H", 3.5),
            ("2027-01-01T12:00Z", "L", 0.5),
        )
    )
    mean = st.mean_level_cd_m
    assert mean == pytest.approx(2.0)

    mid = st.at(_at("2027-01-01 03:00"))
    assert mid.height_m == pytest.approx(0.0, abs=0.001)
    assert mid.rate_m_h == pytest.approx(math.pi / 2 * 3 / 6, abs=0.001)
    assert mid.range_m == 3.0

    early = st.at(_at("2027-01-01 01:30"))
    by_cosine = 0.5 + 3 * (1 - math.cos(math.pi / 4)) / 2
    assert early.height_m + mean == pytest.approx(by_cosine, abs=0.001)

    falling = st.at(_at("2027-01-01 09:00"))
    assert falling.rate_m_h == pytest.approx(-mid.rate_m_h, abs=0.001)


def test_no_tide_outside_the_table():
    st = stations()["cascais"]
    assert st.at(st.covers_from - timedelta(minutes=1)) is None
    assert st.at(st.covers_until + timedelta(minutes=1)) is None
    assert tide_at(38.988, -9.419, datetime(2031, 1, 1, tzinfo=UTC)) is None


# --- king, spring, neap --------------------------------------------------------------


@pytest.mark.parametrize(
    ("stamp", "height"),
    [
        ("2027-09-01 14:47", 3.89),  # the highest of the three years
        ("2027-02-22 03:15", 3.88),
        ("2027-01-24 03:34", 3.85),
        ("2027-08-04 15:54", 3.87),
    ],
)
def test_the_year_s_biggest_highs_are_king(stamp, height):
    st = stations()["cascais"]
    point = next(p for p in st.points if p.at == _at(stamp))
    assert (point.kind, point.height_cd_m, point.king) == ("high", height, True)
    assert st.at(_at(stamp)).tide_class == "king"


def test_the_closest_moon_of_the_year_is_not_a_king_tide_here():
    """24 Dec 2026: perigee 8 h from the full moon, the tightest alignment in the
    table, and still only 3.66 m. Near the solstices the moon's declination takes
    height out of a semi-diurnal coast; the kings come at the equinoxes. The class is
    read from the table, not from the moon."""
    st = stations()["cascais"]
    day = [p for p in st.points if p.kind == "high" and p.at.date() == date(2026, 12, 25)]
    assert max(p.height_cd_m for p in day) == 3.66
    assert not any(p.king for p in day)


def test_about_two_percent_king_and_a_quarter_each_spring_and_neap():
    st = stations()["cascais"]
    highs = [p for p in st.points if p.kind == "high"]
    assert 0.015 < sum(p.king for p in highs) / len(highs) < 0.03
    classes = st._range_class
    for name in ("spring", "neap"):
        assert 0.22 < classes.count(name) / len(classes) < 0.28


def test_a_neap_week_is_neap():
    """First quarter on 2027-01-15 20:34; like the springs, the neaps come a day or
    two after the moon, and the 17th has the smallest ranges of the month."""
    st = stations()["cascais"]
    hour = st.at(_at("2027-01-17 12:00"))
    assert hour.tide_class == "neap"
    assert hour.range_m < 2.0


# --- which station ------------------------------------------------------------------


def test_each_region_reads_its_station():
    """Nearest, checked against a year of Open-Meteo at every cell
    (scripts/almanac_check.py): Ericeira's spots are a tie and nearest is Cascais."""
    expected = {"lisbon": "cascais", "ericeira": "cascais", "peniche": "peniche"}
    for spot in load_spots():
        assert station_for(spot.lat, spot.lon).id == expected[spot.region], spot.id


# --- the sun ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "dawn", "rise", "set_", "dusk"),
    [
        # api.sunrise-sunset.org at Ribeira (38.988, -9.419), UTC, seconds dropped.
        ("2026-12-21", "07:22", "07:52", "17:18", "17:48"),
        ("2027-03-28", "06:01", "06:28", "18:56", "19:23"),  # clocks go forward
        ("2027-06-21", "04:40", "05:12", "20:06", "20:38"),
        ("2027-10-31", "06:36", "07:03", "17:38", "18:06"),  # clocks go back
    ],
)
def test_sun_times_match_a_published_table(day, dawn, rise, set_, dusk):
    d = date.fromisoformat(day)
    got = sun_times(d, 38.988, -9.419)
    for value, want in zip(
        (got.civil_dawn, got.sunrise, got.sunset, got.civil_dusk),
        (dawn, rise, set_, dusk),
        strict=True,
    ):
        expected = datetime.fromisoformat(f"{day} {want}").replace(tzinfo=UTC)
        assert abs(value - expected) <= timedelta(minutes=2), (value, expected)
    assert got.civil_dawn < got.sunrise < got.sunset < got.civil_dusk


# --- carried to the score's boundary, and watched ------------------------------------


def test_the_official_tide_reaches_the_scoring_boundary():
    """`assemble` enumerates fields by hand; one it drops never reaches the harness."""
    from gogo.assemble import forecasts_from_grid
    from helpers import grid_hour

    inside, outside = forecasts_from_grid(
        [
            grid_hour(valid_at=datetime(2027, 9, 1, 15, 0, tzinfo=UTC)),
            grid_hour(valid_at=datetime(2031, 9, 1, 15, 0, tzinfo=UTC)),
        ]
    )
    assert inside.tide_class == "king"
    assert inside.tide_height_m == pytest.approx(3.89 - 2.08, abs=0.05)
    assert inside.tide_range_m is not None and inside.tide_rate_m_h is not None
    assert (outside.tide_height_m, outside.tide_class) == (None, None)


def _health(days_left: float) -> Health:
    now = datetime(2027, 6, 1, tzinfo=UTC)
    return Health(
        as_of=now,
        age_s=60,
        max_age_s=7200,
        missing=[],
        cycles_24h=24,
        tide_until=now + timedelta(days=days_left),
        checked_at=now,
    )


def test_health_fails_before_the_tide_table_runs_out():
    assert _health(days_left=400).ok
    short = _health(days_left=TIDE_TABLE_MIN_DAYS - 1)
    assert not short.ok
    assert "RUNNING OUT" in short.lines(spot_count=16)[-1]
