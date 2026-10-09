"""Does the ray tracer know these spots the way locals do?

Written down before the per-spot tables were computed, so the results could not shape
them. Each is something anyone who surfs this coast would say, turned into an
inequality on the refraction factor in `src/gogo/data/rays.json`: the share of an
open-sea swell that reaches the spot, before breaking. "NW" is 300–315°, "W" 260–270°,
"SW" 240°; the period is 12 s unless a check is about period.

None of this proves the numbers are right. It catches a tracer, a facing or a start
point that is wrong in a way a local would laugh at. Needs no numpy: it reads the
committed table through the same loader the score uses.
"""

import pytest

from gogo.rays import factor, load_tables

TABLES = load_tables()

pytestmark = pytest.mark.skipif(not TABLES, reason="no rays.json yet")


def f(spot: str, from_deg: float, period: float = 12.0) -> float:
    return factor(TABLES[spot], from_deg, period)


# --- the Cascais line needs much more swell than the open coast ----------------------


def test_carcavelos_needs_far_more_nw_swell_than_guincho():
    """The Cascais coast and Cabo Raso stand between Carcavelos and the north-west.
    The same NW swell that has Guincho going is small at Carcavelos."""
    for d in (300, 315):
        assert f("carcavelos", d) < 0.6 * f("guincho", d)


def test_sao_pedro_is_sheltered_from_nw_like_carcavelos():
    assert f("sao_pedro", 315) < 0.6 * f("guincho", 315)


def test_praia_grande_is_a_nw_magnet_where_carcavelos_is_not():
    """Praia Grande (Sintra) is not a spot in coast.yml, so it is traced as a probe."""
    pg = TABLES.get("_probe_praia_grande")
    if pg is None:
        pytest.skip("probe not traced")
    for d in (300, 315):
        assert factor(pg, d, 12) >= 0.85
        assert factor(pg, d, 12) > 2 * f("carcavelos", d)


def test_carcavelos_is_sheltered_not_dead():
    """Big long-period NW swells do reach the Cascais line; that is its winter."""
    assert f("carcavelos", 300, 14) > 0.15


def test_carcavelos_prefers_west_and_southwest_to_northwest():
    assert f("carcavelos", 250) > f("carcavelos", 315)


def test_groundswell_wraps_into_carcavelos_better_than_windswell():
    """Kept as written before the tables existed, but read it as *not confirmed*: it
    failed at single periods and passes by 0.01 (0.29 vs 0.28) once periods are
    averaged. The tracer says the share is flat. Diffraction cannot rescue the belief —
    25 km from Cabo Raso it adds under 6% even at 16 s — so the local truth is
    probably about size: long-period swells are the big ones, and Carcavelos needs size
    because only a third arrives. A label on a long, small NW swell would settle it."""
    assert f("carcavelos", 300, 16) > f("carcavelos", 300, 8)


def test_caparica_is_sheltered_from_nw_by_the_lisbon_peninsula():
    assert f("caparica", 315) < f("caparica", 260)
    assert f("caparica", 315) < 0.7 * f("guincho", 315)


# --- the open coast takes NW swell straight ----------------------------------------


@pytest.mark.parametrize("spot", ["guincho", "ribeira", "coxos", "foz_lizandro"])
def test_the_open_coast_takes_nw_swell(spot):
    assert f(spot, 300) >= 0.8


# --- Peniche: the peninsula decides which side works -------------------------------


def test_baleal_beats_supertubos_on_north_west_swell():
    """Baleal faces north-west into the bay; Supertubos is round the corner, facing
    south-west, and a NW swell has to wrap the peninsula to reach it."""
    assert f("baleal", 330) > f("supertubos", 330)


def test_supertubos_beats_baleal_on_southwest_swell():
    """And the other way round: Baleal is in the lee of the peninsula from the
    south-west. This pair is the substitution S15 is about."""
    assert f("supertubos", 240) > f("baleal", 240)


def test_supertubos_takes_west_swell():
    assert f("supertubos", 260) >= 0.7


def test_lagide_faces_north_and_wants_north_west():
    assert f("lagide", 330) > f("lagide", 250)


def test_consolacao_is_tucked_away_from_the_north():
    assert f("consolacao", 330) < f("consolacao", 255)
