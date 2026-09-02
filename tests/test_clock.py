from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from gogo.assemble import saturday_morning
from gogo.clock import LISBON, UTC, from_unixtime, to_local, to_utc
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


def test_unixtime_round_trips_to_utc():
    when = datetime(2026, 8, 29, 7, 0, tzinfo=UTC)
    assert from_unixtime(int(when.timestamp())) == when


def test_local_input_is_normalised_to_utc():
    local = datetime(2026, 8, 29, 8, 0, tzinfo=LISBON)
    hour = _hour(local)
    assert hour.valid_at == datetime(2026, 8, 29, 7, 0, tzinfo=UTC)
    assert hour.valid_at.tzinfo == timezone.utc


def test_saturday_morning_is_local_not_utc():
    """Saturday 08:00 Lisbon is 07:00Z in summer; UTC 08:00 must not win."""
    hours = [
        _hour(datetime(2026, 8, 29, 7, 0, tzinfo=UTC)),
        _hour(datetime(2026, 8, 29, 8, 0, tzinfo=UTC)),
    ]
    when = saturday_morning(hours)
    assert when == datetime(2026, 8, 29, 7, 0, tzinfo=UTC)
    assert to_local(when).strftime("%A %H:%M") == "Saturday 08:00"


def test_saturday_morning_picks_the_earliest_saturday():
    hours = [
        _hour(datetime(2026, 9, 5, 7, 0, tzinfo=UTC)),
        _hour(datetime(2026, 8, 29, 7, 0, tzinfo=UTC)),
    ]
    assert saturday_morning(hours) == datetime(2026, 8, 29, 7, 0, tzinfo=UTC)
