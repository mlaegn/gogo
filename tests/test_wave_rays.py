"""The ray tracer, on seabeds whose answer is known.

`scripts/wave_rays.py nazare` checks it against the real Nazaré canyon, which needs the
EMODnet download. These need nothing: a straight sloping beach, where Snell's law gives
the answer in closed form; a spit of land, which must cast a shadow; and a trench
running up to the beach, which must focus long swell beside it and leave short swell
alone — the Nazaré mechanism, built by hand. They skip without numpy, which only the
`eval` dependency group installs.
"""

import math
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wave_rays as wr  # noqa: E402

CELL = 100.0
SIZE = 400  # 40 × 40 km
COAST_X = 30_000.0  # a north–south coast, sea to the west


def _seabed(depth):
    return wr.Seabed(depth=depth, lon0=0.0, lat0=0.0, dx=CELL, dy=CELL)


def _slope(gradient: float = 1 / 100):
    x = np.arange(SIZE) * CELL
    return np.tile(np.clip((COAST_X - x) * gradient, -5.0, 4000.0), (SIZE, 1))


def _start(depth_m: float = 10.0, y: float = 20_000.0, gradient: float = 1 / 100):
    return COAST_X - depth_m / gradient, y


def test_the_explicit_dispersion_relation_is_within_one_percent():
    """Guo (2002) claims 0.75% in k against the exact root of ω² = g k tanh(kh)."""
    for period in (6, 10, 14, 18):
        omega = 2 * math.pi / period
        for h in (2.0, 10.0, 40.0, 150.0, 1000.0):
            k = omega**2 / wr.G
            for _ in range(100):  # Newton on the exact relation
                t = math.tanh(k * h)
                k -= (wr.G * k * t - omega**2) / (wr.G * t + wr.G * k * h * (1 - t * t))
            approx = float(wr.wavenumber(omega, np.array([h]))[0])
            assert abs(approx - k) / k < 0.01


def test_a_straight_beach_obeys_snell():
    """Over straight parallel contours the refraction factor is sqrt(cos θ0 / cos θ),
    with sin θ / c = sin θ0 / c0. The spread smooths it slightly; 3% covers that."""
    seabed = _seabed(_slope())
    x0, y0 = _start()
    for period in (8, 14):
        fan = wr.trace(wr.field(seabed, period), x0, y0, step_deg=0.5)
        c0, _, _ = wr.deep_water(period)
        for frm in (270, 290, 310):
            theta0 = math.radians(frm - 270)
            theta = math.asin(math.sin(theta0) * fan.c_start / c0)
            exact = math.sqrt(math.cos(theta0) / math.cos(theta))
            assert wr.refraction(fan, period, frm) == pytest.approx(exact, rel=0.03)


def test_rays_bend_the_way_snell_says():
    """Launched 20° off the normal from 10 m of water, a 12 s ray reaches deep water
    about 42° off it: sin θ0 = sin θ · c0 / c."""
    seabed = _seabed(_slope())
    x0, y0 = _start()
    fan = wr.trace(wr.field(seabed, 12), x0, y0, step_deg=1.0)
    c0, _, _ = wr.deep_water(12)
    expected = 270 + math.degrees(math.asin(math.sin(math.radians(20)) * c0 / fan.c_start))
    assert float(fan.exit_bearing[290]) == pytest.approx(expected, abs=1.0)


def test_a_spit_of_land_casts_a_shadow():
    """A 15 km spit runs out to sea 2 km north of the point, so its tip is at 278° from
    there. Swell from well south of that is untouched, swell from behind it is gone,
    and in between it fades — no cliff, because the swell is a spread of directions."""
    depth = _slope()
    rows = slice(int(22_000 / CELL), int(23_000 / CELL))
    cols = slice(int((COAST_X - 15_000) / CELL), int(COAST_X / CELL))
    depth[rows, cols] = -5.0
    seabed = _seabed(depth)
    x0, y0 = _start()
    fan = wr.trace(wr.field(seabed, 10), x0, y0, step_deg=0.5)
    k = [wr.refraction(fan, 10, d) for d in (250, 290, 310, 330)]
    assert k[0] > 0.85
    assert k[3] < 0.1
    assert k == sorted(k, reverse=True)


def _trench_seabed():
    """A 40 m shelf with a 400 m trench running up to 2 km from the beach."""
    depth = np.minimum(_slope(1 / 300), 40.0)
    y = np.arange(SIZE)[:, None] * CELL
    x = np.arange(SIZE)[None, :] * CELL
    trench = (np.abs(y - 16_000.0) < 1_200.0) & (x < COAST_X - 2_000.0)
    return _seabed(wr.gaussian_smooth(np.where(trench, 400.0, depth), 3, 3))


def _trench_k(y: float, period: float) -> float:
    x0, y0 = _start(10.0, y=y, gradient=1 / 300)
    fan = wr.trace(wr.field(_trench_seabed(), period), x0, y0, step_deg=0.5)
    return wr.refraction(fan, period, 270)


def test_a_trench_focuses_long_swell_beside_it_and_not_short_swell():
    """Nazaré by hand. Swell over the trench outruns swell on the shelf and bends
    towards the beach beside it, 3.5 km from the trench's axis. A 16 s swell feels the
    shelf and focuses; a 6 s swell is in deep water over most of it and barely does."""
    long_, short = _trench_k(19_500.0, 16), _trench_k(19_500.0, 6)
    assert long_ > 1.15
    assert abs(short - 1.0) < 0.1
    assert long_ > short + 0.1


def test_right_beside_the_trench_long_swell_diverges():
    """The other half of the same mechanism: energy bent away from the trench's edge
    leaves the beach behind its head nearly flat. That is why Nazaré's town beach, at
    the canyon head, is calm while Praia do Norte is not."""
    assert _trench_k(17_800.0, 16) < 0.3
