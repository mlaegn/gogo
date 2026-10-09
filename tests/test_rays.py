"""The runtime reader for the traced tables: plain Python, no numpy."""

import pytest

from gogo.rays import Table, factor

TABLE = Table(
    periods=(8.0, 12.0),
    directions=tuple(float(d) for d in range(0, 360, 90)),
    kr=((1.0, 0.5, 0.0, 0.5), (2.0, 1.0, 0.0, 1.0)),
)


def test_it_interpolates_in_direction_and_wraps_at_north():
    assert factor(TABLE, 45, 8) == pytest.approx(0.75)
    assert factor(TABLE, 315, 8) == pytest.approx(0.75)  # between 270° and 0°/360°


def test_it_interpolates_in_period_and_holds_flat_beyond_the_ends():
    assert factor(TABLE, 0, 10) == pytest.approx(1.5)
    assert factor(TABLE, 0, 4) == pytest.approx(1.0)
    assert factor(TABLE, 0, 20) == pytest.approx(2.0)
