from datetime import datetime

from gogo.clock import UTC
from gogo.models import HourForecast
import pytest

from gogo.score import (
    INCUMBENT,
    TYPICAL_PERIOD,
    ScoreOptions,
    rank_hour,
    score_hour,
    typical_equivalent_m,
    typical_period_s,
)
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


def test_every_spot_carries_its_measured_geometry():
    """scripts/spot_geometry.py writes both for every spot; S13's taper will rely on it."""
    for spot in load_spots():
        assert spot.faces_deg is not None, spot.id
        assert spot.exposure in ("open", "sheltered"), spot.id


def test_the_measured_sheltered_spots():
    """Land blocks over 20% of the year's swell energy at these. Pinned so a rerun of
    the script that changes the list is a decision, not a side effect."""
    sheltered = {s.id for s in load_spots() if s.exposure == "sheltered"}
    assert sheltered == {
        "carcavelos", "sao_pedro", "caparica", "supertubos", "consolacao", "lagide"
    }


def test_faces_deg_is_a_bearing():
    with pytest.raises(ValueError):
        by_id()["ribeira"].model_validate({**by_id()["ribeira"].model_dump(), "faces_deg": 360})


def test_geometry_is_stored_not_scored_yet():
    """Until the exposure taper lands as a candidate, these must not move a score."""
    h = hour(swell_from_deg=315, swell_height_m=1.6, swell_period_s=11)
    for spot in load_spots():
        moved = spot.model_copy(update={"faces_deg": (spot.faces_deg + 90) % 360,
                                        "exposure": "open"})
        assert score_hour(moved, h) == score_hour(spot, h)


def test_short_period_west_foz_beats_coxos():
    """Beach that accepts short period should outrank a reef that wants 10 s+."""
    spots = by_id()
    h = hour(swell_height_m=1.2, swell_from_deg=280, swell_period_s=8.0)
    foz = score_hour(spots["foz_lizandro"], h)
    coxos = score_hour(spots["coxos"], h)
    assert foz.score > coxos.score
    assert coxos.verdict != "go"


def test_onshore_gale_is_a_no():
    spots = by_id()
    # Ribeira offshore is 80°; onshore ~260°. 25 kn from the west is a veto.
    h = hour(wind_speed_kn=25, wind_from_deg=260)
    w = score_hour(spots["ribeira"], h)
    assert w.vetoed
    assert w.verdict == "no"
    assert w.score == 0


def test_too_small_is_a_no():
    spots = by_id()
    h = hour(swell_height_m=0.4)
    w = score_hour(spots["coxos"], h)
    assert w.vetoed
    assert w.verdict == "no"


def test_swell_from_south_vetoes_sao_lourenco():
    spots = by_id()
    h = hour(swell_from_deg=180, swell_height_m=1.5, swell_period_s=12)
    w = score_hour(spots["sao_lourenco"], h)
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
    w = score_hour(spots["sao_lourenco"], h)
    assert not w.vetoed
    assert w.score >= 40


# --- wind: speed first, then direction (v3) -----------------------------------------


def _wind(spot_id: str, kn: float, from_deg: float):
    spot = by_id()[spot_id]
    s = score_hour(spot, hour(wind_speed_kn=kn, wind_from_deg=from_deg))
    return s, next(r for r in s.reasons if r.code == "wind")


def test_calm_is_glassy_whichever_way_it_blows():
    """Under v2, 70% of sub-5 kn hours lost points for "blowing onshore" at 2 kn.
    Ribeira's onshore is ~260°; 2 kn from there is a glassy morning."""
    calm_onshore, reason = _wind("ribeira", 2, 260)
    light_offshore, _ = _wind("ribeira", 8, 80)
    assert reason.points == 20
    assert "glassy" in reason.detail
    assert calm_onshore.score == light_offshore.score


def test_calm_beats_a_breeze_from_the_same_direction():
    calm, _ = _wind("ribeira", 3, 260)
    breeze, _ = _wind("ribeira", 10, 260)
    assert calm.score > breeze.score


def test_strong_cross_shore_is_a_no():
    """Ribeira: offshore 80°, so 350° is cross-shore. Its onshore cap is 12 kn, and a
    cross wind gets a margin over that — 15 kn passes, 22 kn closes the spot. Under v2
    any cross wind scored 12 points however hard it blew."""
    moderate, _ = _wind("ribeira", 15, 350)
    strong, reason = _wind("ribeira", 22, 350)
    assert not moderate.vetoed
    assert strong.vetoed
    assert strong.verdict == "no"
    assert reason.code == "wind" and "cross-shore" in reason.detail


def test_the_cross_shore_limit_follows_the_spot():
    """Guincho tolerates wind (cap 20 kn); the same 22 kn cross that closes Ribeira
    does not close it."""
    guincho, _ = _wind("guincho", 22, 350)
    assert not guincho.vetoed


def test_strong_offshore_says_offshore():
    """It used to fall through to "cross / sideshore", which named the wrong wind."""
    _, reason = _wind("ribeira", 22, 80)
    assert reason.points == 12
    assert "offshore" in reason.detail


# --- period-aware size: a candidate, off by default (S13) ---------------------------

PERIOD_AWARE = ScoreOptions(size_ref_period_s=8.3)


def _size(spot_id: str, options: ScoreOptions, **kwargs):
    s = score_hour(by_id()[spot_id], hour(**kwargs), options)
    return s, next(r for r in s.reasons if r.code == "size")


def test_the_default_options_are_the_incumbent():
    """Serving never passes options, so the default must be exactly v3."""
    assert ScoreOptions() == INCUMBENT
    for h in (1.0, 1.5, 2.5):
        for t in (6.0, 9.0, 14.0):
            with_default = score_hour(by_id()["ribeira"], hour(swell_height_m=h, swell_period_s=t))
            explicit = score_hour(by_id()["ribeira"], hour(swell_height_m=h, swell_period_s=t), INCUMBENT)
            assert with_default == explicit


def test_at_the_reference_period_nothing_changes():
    """The square root is 1 there, so the candidate only differs where period does."""
    spots = load_spots()
    for h in (0.5, 1.0, 1.6, 3.0, 4.5):
        at_ref = hour(swell_height_m=h, swell_period_s=8.3)
        for spot in spots:
            assert score_hour(spot, at_ref, PERIOD_AWARE) == score_hour(spot, at_ref)


def test_a_small_long_period_swell_opens_a_spot_that_wants_size():
    """Coxos wants 1.2 m. 1.1 m at 15 s breaks bigger than 1.2 m at 8 s, so the
    incumbent's veto is the size gate ignoring the period."""
    incumbent, _ = _size("coxos", INCUMBENT, swell_height_m=1.1, swell_period_s=15)
    candidate, reason = _size("coxos", PERIOD_AWARE, swell_height_m=1.1, swell_period_s=15)
    assert incumbent.vetoed
    assert not candidate.vetoed
    assert reason.detail == "1.1 m at 15 s breaks like 1.5 m, in range"


def test_short_period_windswell_counts_smaller():
    _, kept = _size("ribeira", INCUMBENT, swell_height_m=0.85, swell_period_s=6)
    closed, reason = _size("ribeira", PERIOD_AWARE, swell_height_m=0.85, swell_period_s=6)
    assert kept.points > 0
    assert closed.vetoed
    assert reason.detail == "0.8 m at 6 s breaks like 0.7 m, below this spot's 0.8 m min"


def test_a_long_period_swell_can_be_too_big_for_a_spot():
    closed, reason = _size("ribeira", PERIOD_AWARE, swell_height_m=3.0, swell_period_s=14)
    assert closed.vetoed
    assert "close-out" in reason.detail and "breaks like 3.9 m" in reason.detail


def test_the_incumbent_sentence_is_unchanged():
    _, reason = _size("ribeira", INCUMBENT, swell_height_m=1.5, swell_period_s=14)
    assert reason.detail == "1.5 m in range"


def test_a_number_beside_a_veto_never_contradicts_it():
    """0.76 m used to print as "0.8 m is below this spot's 0.8 m min"."""
    _, reason = _size("ribeira", INCUMBENT, swell_height_m=0.76, swell_period_s=9)
    assert reason.detail == "0.7 m is below this spot's 0.8 m min"
    _, reason = _size("ribeira", INCUMBENT, swell_height_m=3.24, swell_period_s=9)
    assert reason.detail == "3.3 m looks like a close-out here (max 3.2 m)"


# --- period-aware size, re-anchored at the period typical for each height -----------

TYPICAL = ScoreOptions(size_period_typical=True)


def test_the_typical_period_table_is_monotone():
    """The inversion in typical_equivalent_m needs it, and so does the physics: a
    bigger swell on this coast is never shorter-period on average."""
    heights = [h for h, _ in TYPICAL_PERIOD]
    periods = [t for _, t in TYPICAL_PERIOD]
    assert heights == sorted(heights) and len(set(heights)) == len(heights)
    assert periods == sorted(periods)


def test_typical_period_interpolates_and_holds_flat_past_the_ends():
    assert typical_period_s(0.1) == TYPICAL_PERIOD[0][1]
    assert typical_period_s(9.0) == TYPICAL_PERIOD[-1][1]
    assert typical_period_s(1.0) == pytest.approx((6.9 + 7.5) / 2)


def test_a_typical_day_breaks_like_itself():
    for h in (0.3, 0.8, 1.37, 2.0, 3.3, 6.0):
        assert typical_equivalent_m(h, typical_period_s(h)) == pytest.approx(h, abs=1e-9)


def test_on_a_typical_day_the_re_anchored_gate_is_the_incumbent():
    """The point of re-anchoring: the ranges in coast.yml keep meaning what they were
    written to mean, and only an unusual period for the size moves anything."""
    spots = load_spots()
    for h in (0.45, 0.65, 0.95, 1.35, 1.85, 2.65, 3.05, 3.65, 4.25):
        typical_hour = hour(swell_height_m=h, swell_period_s=typical_period_s(h))
        for spot in spots:
            assert score_hour(spot, typical_hour, TYPICAL) == score_hour(spot, typical_hour)


def test_re_anchoring_removes_the_stretch_a_fixed_reference_puts_on_big_days():
    """A typical 3 m day here is ~9.9 s. Against a fixed 8.3 s reference it counts as
    3.3 m and closes Ribeira (max 3.2 m), purely because big days are long-period
    days. Re-anchored, it is the 3 m day the spot file was written for."""
    big = dict(swell_height_m=3.0, swell_period_s=typical_period_s(3.0))
    fixed, _ = _size("ribeira", PERIOD_AWARE, **big)
    typical, reason = _size("ribeira", TYPICAL, **big)
    assert fixed.vetoed
    assert not typical.vetoed
    assert reason.detail == "3.0 m in range"


def test_re_anchored_long_period_still_counts_bigger_and_windswell_smaller():
    opened, reason = _size("coxos", TYPICAL, swell_height_m=1.1, swell_period_s=15)
    assert not opened.vetoed
    assert reason.detail == "1.1 m at 15 s breaks like a typical 1.5 m, in range"
    closed, reason = _size("ribeira", TYPICAL, swell_height_m=0.85, swell_period_s=6)
    assert closed.vetoed
    assert "breaks like a typical 0.7 m, below" in reason.detail


def test_the_two_period_options_are_exclusive():
    with pytest.raises(ValueError, match="exclusive"):
        ScoreOptions(size_ref_period_s=8.3, size_period_typical=True)
