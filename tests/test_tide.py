from datetime import datetime, timedelta

from gogo.tide import attach_tide, classify_levels


def test_classify_semidiurnal_curve():
    # Rough stand-in for the Open-Meteo series we pulled for Ericeira.
    levels = [0.1, 0.4, 0.55, 0.4, 0.0, -0.5, -1.0, -1.3, -1.0, -0.4, 0.2, 0.7]
    tagged = classify_levels(levels)
    phases = [p for p, _ in tagged]
    assert phases[2] == "high"
    assert phases[7] == "low"
    assert tagged[5][1] == "outgoing"
    assert tagged[9][1] == "incoming"


def test_flat_series_is_mid_slack():
    tagged = classify_levels([0.2, 0.21, 0.2, 0.19])
    assert all(p == "mid" and t == "slack" for p, t in tagged)


def test_attach_tide_skips_nulls():
    start = datetime(2026, 8, 26, 0, 0)
    times = [start + timedelta(hours=i) for i in range(4)]
    mapping = attach_tide(times, [0.5, None, -1.0, 0.4])
    assert times[1] not in mapping
    assert mapping[times[2]][0] == "low"
