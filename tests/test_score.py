from datetime import datetime

from gogo.clock import UTC
from gogo.models import HourForecast
from gogo.score import rank_hour, score_window
from gogo.spots import by_id, load_spots

WHEN = datetime(2026, 8, 29, 7, 0, 0, tzinfo=UTC)  # Saturday 08:00 Lisbon


def hour(**kwargs) -> HourForecast:
    base = dict(
        valid_at=WHEN,
        swell_height_m=1.2,
        swell_from_deg=280,
        swell_period_s=8.0,
        wind_wave_height_m=0.3,
        wind_speed_kn=8.0,
        wind_from_deg=70,
        wind_gusts_kn=12.0,
        tide="mid",
        tide_trend="incoming",
    )
    base.update(kwargs)
    return HourForecast.model_validate(base)


def test_spots_load():
    spots = load_spots()
    assert len(spots) >= 15
    assert {s.region for s in spots} == {"ericeira", "lisbon", "peniche"}


def test_short_period_west_foz_beats_coxos():
    """Beach that accepts short period should outrank a reef that wants 10 s+."""
    spots = by_id()
    h = hour(swell_height_m=1.2, swell_from_deg=280, swell_period_s=8.0)
    foz = score_window(spots["foz_lizandro"], h)
    coxos = score_window(spots["coxos"], h)
    assert foz.score > coxos.score
    assert coxos.verdict != "go"


def test_onshore_gale_is_a_no():
    spots = by_id()
    # Ribeira offshore is 80°; onshore ~260°. 25 kn from the west is a veto.
    h = hour(wind_speed_kn=25, wind_from_deg=260)
    w = score_window(spots["ribeira"], h)
    assert w.vetoed
    assert w.verdict == "no"
    assert w.score == 0


def test_too_small_is_a_no():
    spots = by_id()
    h = hour(swell_height_m=0.4)
    w = score_window(spots["coxos"], h)
    assert w.vetoed
    assert w.verdict == "no"


def test_swell_from_south_vetoes_sao_lourenco():
    spots = by_id()
    h = hour(swell_from_deg=180, swell_height_m=1.5, swell_period_s=12)
    w = score_window(spots["sao_lourenco"], h)
    assert w.vetoed
    assert any(r.code == "swell_dir" for r in w.reasons)


def test_groundswell_northwest_ranks_reefs_above_consolacao():
    spots = load_spots()
    h = hour(
        swell_height_m=1.8,
        swell_from_deg=310,
        swell_period_s=12.0,
        wind_speed_kn=8.0,
        wind_from_deg=70,
        tide="mid",
    )
    ranked = rank_hour(spots, h)
    names = [w.spot_id for w in ranked if w.verdict != "no"]
    assert names[0] in {"ribeira", "coxos", "furnas", "sao_lourenco", "pedra_branca"}
    by = {w.spot_id: w for w in ranked}
    assert by["ribeira"].score > by["consolacao"].score


def test_wrapping_swell_window_sao_lourenco_accepts_north():
    spots = by_id()
    h = hour(swell_from_deg=10, swell_height_m=1.6, swell_period_s=11)
    w = score_window(spots["sao_lourenco"], h)
    assert not w.vetoed
    assert w.score >= 40
