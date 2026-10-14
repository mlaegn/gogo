import math
from datetime import datetime, timedelta

from gogo.clock import UTC
from gogo.tide import SEA_LEVEL_LEAD, TIDE_CONTEXT, attach_tide

START = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
M2_HOURS = 12.42


def _hours(n: int, start: datetime = START) -> list[datetime]:
    return [start + timedelta(hours=i) for i in range(n)]


def _spring_neap(times: list[datetime]) -> list[float]:
    """Semi-diurnal tide whose range swings from ~3.4 m at springs to ~1.0 m at neaps,
    with springs at the start of the series and neaps a week later. Roughly the Lisbon
    coast."""
    out = []
    for t in times:
        h = (t - START).total_seconds() / 3600
        amplitude = 1.1 + 0.6 * math.cos(2 * math.pi * h / (14.77 * 24))
        out.append(amplitude * math.cos(2 * math.pi * h / M2_HOURS))
    return out


def test_classify_semidiurnal_curve():
    # Rough stand-in for the Open-Meteo series we pulled for Ericeira.
    levels = [0.1, 0.4, 0.55, 0.4, 0.0, -0.5, -1.0, -1.3, -1.0, -0.4, 0.2, 0.7]
    times = _hours(len(levels))
    tagged = attach_tide(times, levels)
    assert tagged[times[2]][0] == "high"
    assert tagged[times[7]][0] == "low"
    assert tagged[times[5]][1] == "outgoing"
    assert tagged[times[9]][1] == "incoming"


def test_flat_series_is_mid_slack():
    times = _hours(4)
    tagged = attach_tide(times, [0.2, 0.21, 0.2, 0.19])
    assert all(p == "mid" and t == "slack" for p, t in tagged.values())


def test_attach_tide_skips_nulls():
    times = _hours(4)
    mapping = attach_tide(times, [0.5, None, -1.0, 0.4])
    assert times[1] not in mapping
    assert mapping[times[2]][0] == "low"


def test_the_phase_does_not_depend_on_how_much_was_loaded():
    """The bug v3 fixes. A backtest loads one day, serving loads a week, and the same
    hour must get the same phase from both — otherwise the harness scores a tide term
    nobody was shown. Under v2, 12% of a year's hours disagreed."""
    week = _hours(8 * 24)
    levels = _spring_neap(week)
    from_week = attach_tide(week, levels)

    # One day in the middle, loaded the way features_for_day loads it: padded.
    day_start = START + timedelta(days=5)
    lo = day_start - TIDE_CONTEXT
    hi = day_start + timedelta(days=1) + TIDE_CONTEXT
    slice_t = [t for t in week if lo <= t < hi]
    from_day = attach_tide(slice_t, [levels[week.index(t)] for t in slice_t])

    day = [t for t in slice_t if day_start <= t < day_start + timedelta(days=1)]
    assert [from_day[t] for t in day] == [from_week[t] for t in day]


def test_a_neap_day_still_has_a_low_and_a_high():
    """Against a week's min and max a neap day read "mid" from dawn to dusk, so every
    spot that wants low or high lost its tide points for the whole day. Low means low
    for this cycle, which is what a tide table means by it."""
    week = _hours(8 * 24)
    tagged = attach_tide(week, _spring_neap(week))
    neap_day = [t for t in week if START + timedelta(days=7) <= t]
    phases = {tagged[t][0] for t in neap_day}
    assert phases == {"low", "mid", "high"}


def test_an_edge_hour_is_judged_against_a_whole_cycle():
    """The window slides inwards at the ends of the series rather than shrinking.

    This series starts three hours after high water, on the way down and about level
    with mean sea level. Shrunk to the seven hours after it, the window holds the low
    but none of the high, and the hour reads "high". Slid inwards to a full cycle it
    reads what it is: mid.
    """
    times = _hours(30, START + timedelta(hours=3))
    tagged = attach_tide(times, _spring_neap(times))
    assert tagged[times[0]] == ("mid", "outgoing")


def test_open_meteo_is_read_thirty_two_minutes_back():
    """Measured against the official tide at Cascais and Peniche (scripts/tide_check.py):
    Open-Meteo's highs and lows come 32 minutes early. Fed a tide that runs exactly that
    early, the corrected phases must match the on-time tide, where uncorrected ones do
    not on about a quarter of hours."""
    assert SEA_LEVEL_LEAD == timedelta(minutes=32)
    week = _hours(8 * 24)
    on_time = _spring_neap(week)
    early = _spring_neap([t + SEA_LEVEL_LEAD for t in week])
    truth = attach_tide(week, on_time, lead=timedelta())
    corrected = attach_tide(week, early)
    raw = attach_tide(week, early, lead=timedelta())
    inner = week[24:-24]
    agree = sum(corrected[t][0] == truth[t][0] for t in inner) / len(inner)
    naive = sum(raw[t][0] == truth[t][0] for t in inner) / len(inner)
    assert agree > 0.97
    assert naive < 0.9


def test_the_first_hour_of_a_series_survives_the_correction():
    times = _hours(30)
    tagged = attach_tide(times, _spring_neap(times))
    assert times[0] in tagged
