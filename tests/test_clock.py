from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from gogo.assemble import plan_day
from gogo.clock import (
    LISBON,
    UTC,
    from_local_input,
    from_unixtime,
    to_local,
    to_utc,
)
from gogo.ingest.protocol import GridHour

# Portugal turns the clocks back at 02:00 WEST on 25 Oct 2026, so local 01:00
# happens twice: once at 00:00Z (UTC+1) and again at 01:00Z (UTC+0).
FOLD_FIRST = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
FOLD_SECOND = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)


def _hour(valid_at: datetime, **kwargs) -> GridHour:
    base = dict(
        requested_lat=38.988,
        requested_lon=-9.419,
        grid_lat=38.958,
        grid_lon=-9.458,
        valid_at=valid_at,
        swell_height_m=1.3,
        swell_from_deg=290,
        swell_period_s=11.0,
        wind_wave_height_m=0.2,
        wind_speed_kn=8.0,
        wind_from_deg=70,
        wind_gusts_kn=12.0,
        sea_level_m=0.4,
    )
    base.update(kwargs)
    return GridHour.model_validate(base)


def test_dst_fold_hours_stay_distinct():
    """The two local 01:00s are one hour apart and must not collapse."""
    assert to_local(FOLD_FIRST).hour == 1
    assert to_local(FOLD_SECOND).hour == 1
    assert to_local(FOLD_FIRST).utcoffset() == timedelta(hours=1)
    assert to_local(FOLD_SECOND).utcoffset() == timedelta(0)
    assert FOLD_FIRST != FOLD_SECOND

    first, second = _hour(FOLD_FIRST), _hour(FOLD_SECOND)
    assert first.valid_at != second.valid_at


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="naive datetime"):
        to_utc(datetime(2026, 8, 29, 8, 0))

    with pytest.raises(ValidationError):
        _hour(datetime(2026, 8, 29, 8, 0))


def test_typed_local_time_becomes_the_right_instant():
    """Someone logging a session types Lisbon wall-clock time, not UTC."""
    # September is WEST (UTC+1): 07:15 local is 06:15Z.
    assert from_local_input(date(2026, 9, 5), "07:15") == datetime(
        2026, 9, 5, 6, 15, tzinfo=UTC
    )
    # December is WET (UTC+0): the same wall clock is a different offset.
    assert from_local_input(date(2026, 12, 5), "07:15") == datetime(
        2026, 12, 5, 7, 15, tzinfo=UTC
    )


def test_typed_local_time_tolerates_a_bare_hour():
    assert from_local_input(date(2026, 9, 5), "7") == from_local_input(
        date(2026, 9, 5), "07:00"
    )


def test_unixtime_round_trips_to_utc():
    when = datetime(2026, 8, 29, 7, 0, tzinfo=UTC)
    assert from_unixtime(int(when.timestamp())) == when


def test_local_input_is_normalised_to_utc():
    local = datetime(2026, 8, 29, 8, 0, tzinfo=LISBON)
    hour = _hour(local)
    assert hour.valid_at == datetime(2026, 8, 29, 7, 0, tzinfo=UTC)
    assert hour.valid_at.tzinfo == timezone.utc


def test_plan_day_is_a_local_saturday_not_a_utc_one():
    """23:30Z on Friday is already Saturday in Lisbon, and the day must follow Lisbon."""
    friday_late_utc = datetime(2026, 8, 28, 23, 30, tzinfo=UTC)
    assert to_local(friday_late_utc).weekday() == 5
    assert plan_day([_hour(friday_late_utc)]) == date(2026, 8, 29)


def test_plan_day_picks_the_earliest_saturday():
    hours = [
        _hour(datetime(2026, 9, 5, 7, 0, tzinfo=UTC)),
        _hour(datetime(2026, 8, 29, 7, 0, tzinfo=UTC)),
    ]
    assert plan_day(hours) == date(2026, 8, 29)


def test_plan_day_falls_back_to_the_first_day_when_no_saturday_is_in_range():
    hours = [_hour(datetime(2026, 9, 1, 7, 0, tzinfo=UTC))]
    assert plan_day(hours) == date(2026, 9, 1)


def test_plan_day_skips_saturdays_that_already_happened():
    """forecast_current keeps every hour ever fetched, including last weekend's."""
    gone = datetime(2026, 8, 29, 7, 0, tzinfo=UTC)
    coming = datetime(2026, 9, 5, 7, 0, tzinfo=UTC)
    hours = [_hour(gone), _hour(coming)]

    assert plan_day(hours, not_before=datetime(2026, 9, 3, 12, 0, tzinfo=UTC)) == date(2026, 9, 5)
    assert plan_day(hours, not_before=None) == date(2026, 8, 29)
    assert plan_day(hours, not_before=datetime(2027, 1, 1, tzinfo=UTC)) is None
