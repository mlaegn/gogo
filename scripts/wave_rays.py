"""Wave rays over the real seabed: how much of an offshore swell reaches a point.

Offline analysis, run by hand. Needs the `eval` dependency group (numpy), which the
worker and API images never install:

    uv run --group eval python scripts/wave_rays.py nazare     # the validation
    uv run --group eval python scripts/wave_rays.py spots      # writes src/gogo/data/rays.json

**The physics.** A wave slows down as the water shallows, and a long wave feels the
bottom sooner. Linear theory gives the speed from the depth h and period T through the
dispersion relation ω² = g k tanh(k h), and a crest turns towards the slower, shallower
side — Snell's law applied continuously:

    dφ/ds = (1/c) (sin φ ∂c/∂x − cos φ ∂c/∂y)

**Reverse tracing.** Rays are reversible, so instead of launching parallel rays from
the open sea and hoping some land near the spot, rays are launched *outwards* from a
point just off the beach, over every direction, and followed until they reach the
open sea past the continental slope, or hit land. A ray leaving deep water heading for bearing b says that
swell *from* b arrives at the spot from the ray's starting angle; a ray that hits land
marks an angle at the spot that no swell reaches.

**From rays to height.** Wave action is conserved along a ray, so the directional
spectrum S(θ)·c·cg is invariant (Longuet-Higgins 1957). For a deep-water swell from
θm with a directional spread D, the local energy is the sum over the spot's angles of
D(θ_deep − θm), weighted by c·cg. Dividing out normal-incidence shoaling leaves the
refraction factor

    Kr² = (c0 / c) Σ D(θ_deep,i − θm) Δθ

which over straight parallel contours is exactly cos θ0 / cos θ, and where rays
converge — a canyon, a reef, the end of a point — is larger than one. The spread is
what keeps a caustic finite: pure ray theory would put infinite height where rays
cross. This is the method of O'Reilly & Guza (1993) for the Southern California Bight.

**The data.** EMODnet Bathymetry, the mean depth on a 1/16 arc-minute grid (about
90 × 115 m here), fetched once and cached. It resolves the shelf and the canyons,
which is what bends swell; it does not resolve individual sandbars, which move anyway.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

G = 9.81
#: Cascais to past Nazaré, and far enough west to reach water deep for 18 s swell.
WEST, SOUTH, EAST, NORTH = -10.30, 38.45, -8.85, 39.85
RES_DEG = 1 / 960
WCS = (
    "https://ows.emodnet-bathymetry.eu/wcs?service=WCS&version=1.0.0&request=GetCoverage"
    "&coverage=emodnet:mean&crs=EPSG:4326&format=GeoTIFF"
    f"&BBOX={WEST},{SOUTH},{EAST},{NORTH}&RESX={RES_DEG}&RESY={RES_DEG}"
)

LAT_C = (SOUTH + NORTH) / 2
M_PER_DEG_Y = 110_574.0
M_PER_DEG_X = 111_320.0 * math.cos(math.radians(LAT_C))

#: Smoothing of the seabed before taking gradients, in metres. Rays are sensitive to
#: grid noise, which turns into spurious caustics; a few hundred metres is about one
#: swell wavelength, the scale below which a wave cannot see the bottom's detail.
SMOOTH_M = 250.0
#: Where a ray counts as having hit land.
LAND_M = 1.0
#: Depth of the point a spot is traced from: just seaward of where swell breaks.
START_DEPTH_M = 10.0
#: Directional spread of the swell, cos^2s(θ/2). s = 20 is a standard swell value,
#: a standard deviation of about 18°.
SPREAD_S = 20
#: Where a ray counts as being out at sea. Not "deep for its period", h > L0/2: the
#: Nazaré canyon is deeper than that within a kilometre of the beach, and a ray
#: stopped there never crosses the shelf either side of it, which is where the swell
#: bends. Past the continental slope the depth no longer changes the speed of any
#: swell, so the direction is final.
OPEN_SEA_M = 1000.0
STEP_M = 40.0
MAX_STEPS = 8000


# --- the seabed ----------------------------------------------------------------------


def fetch(cache: Path) -> Path:
    path = cache / "emodnet_mean.tif"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            WCS, headers={"User-Agent": "gogo-surf-planner/0.1 (wave rays)"}
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            path.write_bytes(response.read())
    return path


def read_geotiff(path: Path) -> tuple[np.ndarray, float, float]:
    """Elevation grid (metres, negative below sea level), row 0 at the south, plus the
    longitude and latitude of cell (0, 0). Handles the uncompressed, tiled float32
    GeoTIFF EMODnet serves and nothing else, loudly."""
    raw = path.read_bytes()
    order = ">" if raw[:2] == b"MM" else "<"
    (ifd,) = struct.unpack(order + "I", raw[4:8])
    (count,) = struct.unpack(order + "H", raw[ifd : ifd + 2])
    sizes = {3: 2, 4: 4, 12: 8}
    codes = {3: "H", 4: "I", 12: "d"}
    tags = {}
    for i in range(count):
        entry = raw[ifd + 2 + 12 * i : ifd + 14 + 12 * i]
        tag, kind, n = struct.unpack(order + "HHI", entry[:8])
        if kind not in sizes:
            continue
        nbytes = sizes[kind] * n
        data = entry[8 : 8 + nbytes] if nbytes <= 4 else raw[
            struct.unpack(order + "I", entry[8:12])[0] :
        ][:nbytes]
        tags[tag] = struct.unpack(order + codes[kind] * n, data[:nbytes])
    width, height = tags[256][0], tags[257][0]
    if tags.get(259, (1,))[0] != 1 or tags.get(339, (3,))[0] != 3 or 324 not in tags:
        raise SystemExit("expected an uncompressed, tiled float32 GeoTIFF")
    tw, th = tags[322][0], tags[323][0]
    across = -(-width // tw)
    grid = np.empty((-(-height // th) * th, across * tw), dtype=np.float32)
    dtype = np.dtype(order + "f4")
    for k, offset in enumerate(tags[324]):
        tile = np.frombuffer(raw, dtype=dtype, count=tw * th, offset=offset)
        r, c = divmod(k, across)
        grid[r * th : (r + 1) * th, c * tw : (c + 1) * tw] = tile.reshape(th, tw)
    grid = grid[:height, :width]
    transform = tags[34264]
    lon0, top = transform[3], transform[7]
    scale_y = -transform[5]
    # Flip so row 0 is the southern edge and y grows with the row index.
    return grid[::-1].astype(np.float64), lon0, top - (height - 1) * scale_y


def gaussian_smooth(a: np.ndarray, sigma_rows: float, sigma_cols: float) -> np.ndarray:
    def kernel(sigma: float) -> np.ndarray:
        half = max(1, int(3 * sigma))
        x = np.arange(-half, half + 1)
        k = np.exp(-0.5 * (x / sigma) ** 2)
        return k / k.sum()

    out = a
    for axis, sigma in ((0, sigma_rows), (1, sigma_cols)):
        k = kernel(sigma)
        pad = len(k) // 2
        padded = np.pad(out, [(pad, pad) if ax == axis else (0, 0) for ax in (0, 1)], mode="edge")
        out = sum(
            k[i] * np.take(padded, np.arange(i, i + out.shape[axis]), axis=axis)
            for i in range(len(k))
        )
    return out


@dataclass(frozen=True)
class Seabed:
    depth: np.ndarray  # metres, positive in water, smoothed
    lon0: float
    lat0: float
    dx: float  # metres per column
    dy: float  # metres per row

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) / RES_DEG * self.dx, (lat - self.lat0) / RES_DEG * self.dy

    def latlon(self, x: float, y: float) -> tuple[float, float]:
        return self.lat0 + y / self.dy * RES_DEG, self.lon0 + x / self.dx * RES_DEG


def load_seabed(cache: Path) -> Seabed:
    elevation, lon0, lat0 = read_geotiff(fetch(cache))
    # Land and missing cells are dry: the ocean is what is below sea level.
    depth = np.where(np.isfinite(elevation), -elevation, -10.0)
    dx = RES_DEG * M_PER_DEG_X
    dy = RES_DEG * M_PER_DEG_Y
    smoothed = gaussian_smooth(depth, SMOOTH_M / dy, SMOOTH_M / dx)
    return Seabed(depth=smoothed, lon0=lon0, lat0=lat0, dx=dx, dy=dy)


# --- linear wave theory ----------------------------------------------------------------


def wavenumber(omega: float, h: np.ndarray) -> np.ndarray:
    """k from ω² = g k tanh(kh), explicit to within 0.75% (Guo 2002)."""
    h = np.maximum(h, 0.1)
    x = omega * np.sqrt(h / G)
    kh = x**2 * (1 - np.exp(-(x**2.5))) ** (-0.4)
    return kh / h


def speeds(period: float, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Phase and group speed at depth h."""
    omega = 2 * math.pi / period
    k = wavenumber(omega, h)
    kh = k * np.maximum(h, 0.1)
    c = omega / k
    two = np.minimum(2 * kh, 50.0)
    n = 0.5 * (1 + two / np.sinh(two))
    return c, c * n


def deep_water(period: float) -> tuple[float, float, float]:
    """c0, cg0 and the wavelength L0 in deep water."""
    c0 = G * period / (2 * math.pi)
    return c0, c0 / 2, c0 * period


# --- the rays --------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    seabed: Seabed
    period: float
    c: np.ndarray
    dcdx: np.ndarray
    dcdy: np.ndarray


def field(seabed: Seabed, period: float) -> Field:
    c, _ = speeds(period, seabed.depth)
    dcdy, dcdx = np.gradient(c, seabed.dy, seabed.dx)
    return Field(seabed, period, c, dcdx, dcdy)


def _sample(grid: np.ndarray, fx: np.ndarray, fy: np.ndarray) -> np.ndarray:
    """Bilinear interpolation at fractional (column, row) positions."""
    rows, cols = grid.shape
    fx = np.clip(fx, 0, cols - 1.000001)
    fy = np.clip(fy, 0, rows - 1.000001)
    i, j = fy.astype(int), fx.astype(int)
    ty, tx = fy - i, fx - j
    return (
        grid[i, j] * (1 - tx) * (1 - ty)
        + grid[i, j + 1] * tx * (1 - ty)
        + grid[i + 1, j] * (1 - tx) * ty
        + grid[i + 1, j + 1] * tx * ty
    )


@dataclass(frozen=True)
class Fan:
    """Rays launched from one point over every direction."""

    launch: np.ndarray  # bearing each ray left the point on, degrees
    exit_bearing: np.ndarray  # bearing it was heading when it reached deep water; nan = land
    c_start: float


def trace(f: Field, x0: float, y0: float, step_deg: float = 0.25) -> Fan:
    seabed = f.seabed
    rows, cols = seabed.depth.shape
    _, _, l0 = deep_water(f.period)
    launch = np.arange(0.0, 360.0, step_deg)
    phi = np.radians(90.0 - launch)  # maths angle from +x, anticlockwise
    x = np.full(launch.shape, x0)
    y = np.full(launch.shape, y0)
    alive = np.ones(launch.shape, dtype=bool)
    exit_bearing = np.full(launch.shape, np.nan)

    def rates(x, y, phi):
        fx, fy = x / seabed.dx, y / seabed.dy
        c = _sample(f.c, fx, fy)
        cx = _sample(f.dcdx, fx, fy)
        cy = _sample(f.dcdy, fx, fy)
        return np.cos(phi), np.sin(phi), (np.sin(phi) * cx - np.cos(phi) * cy) / c

    for _ in range(MAX_STEPS):
        idx = np.flatnonzero(alive)
        if idx.size == 0:
            break
        xa, ya, pa = x[idx], y[idx], phi[idx]
        # Midpoint (RK2): good enough at a step well under the smoothing scale.
        u1, v1, w1 = rates(xa, ya, pa)
        u2, v2, w2 = rates(xa + 0.5 * STEP_M * u1, ya + 0.5 * STEP_M * v1, pa + 0.5 * STEP_M * w1)
        xa = xa + STEP_M * u2
        ya = ya + STEP_M * v2
        pa = pa + STEP_M * w2
        x[idx], y[idx], phi[idx] = xa, ya, pa

        fx, fy = xa / seabed.dx, ya / seabed.dy
        outside = (fx < 0) | (fx > cols - 1) | (fy < 0) | (fy > rows - 1)
        h = _sample(seabed.depth, fx, fy)
        landed = (h < LAND_M) & ~outside
        deep = (h > max(0.5 * l0, OPEN_SEA_M)) | outside
        bearing = (90.0 - np.degrees(pa)) % 360.0
        exit_bearing[idx[deep & ~landed]] = bearing[deep & ~landed]
        alive[idx[landed | deep]] = False
    c_start = float(_sample(f.c, np.array([x0 / seabed.dx]), np.array([y0 / seabed.dy]))[0])
    return Fan(launch=launch, exit_bearing=exit_bearing, c_start=c_start)


def spread(delta_deg: np.ndarray, s: int = SPREAD_S) -> np.ndarray:
    """cos^2s(θ/2), normalised to integrate to one over θ in radians."""
    norm = math.gamma(s + 1) / (2 * math.sqrt(math.pi) * math.gamma(s + 0.5))
    return norm * np.cos(np.radians(delta_deg) / 2) ** (2 * s)


def refraction(fan: Fan, period: float, from_deg: float) -> float:
    """Kr for a deep-water swell from `from_deg`: the height factor from bending alone,
    normal-incidence shoaling divided out. 1 over a straight coast at normal incidence,
    above 1 where the seabed focuses, 0 in a shadow."""
    c0, _, _ = deep_water(period)
    reached = np.isfinite(fan.exit_bearing)
    if not reached.any():
        return 0.0
    # A ray leaving towards bearing b carries swell coming *from* b.
    delta = (fan.exit_bearing[reached] - from_deg + 180.0) % 360.0 - 180.0
    step = math.radians(float(fan.launch[1] - fan.launch[0]))
    energy = spread(delta).sum() * step
    return math.sqrt(c0 / fan.c_start * energy)


def start_point(
    seabed: Seabed, lat: float, lon: float, toward_deg: float, depth: float = START_DEPTH_M
) -> tuple[float, float]:
    """Walk from (lat, lon) out to sea along `toward_deg` until the water is `depth`
    deep: just seaward of the break, where linear theory still holds. Walking along
    the beach's own facing matters — the nearest deep-enough cell can be round a
    headland, which once put "Praia do Norte" on the tip of the Nazaré promontory."""
    x, y = seabed.xy(lat, lon)
    ux, uy = math.sin(math.radians(toward_deg)), math.cos(math.radians(toward_deg))
    for _ in range(500):
        h = _sample(seabed.depth, np.array([x / seabed.dx]), np.array([y / seabed.dy]))[0]
        if h >= depth:
            return x, y
        x, y = x + 20.0 * ux, y + 20.0 * uy
    raise ValueError(f"no {depth:.0f} m water within 10 km of {lat}, {lon} towards {toward_deg}°")


# --- the Nazaré check ----------------------------------------------------------------

#: The Nazaré canyon reaches the coast just south of the Sítio headland, 200–290 m deep
#: within 3–5 km of it. Swell crossing the canyon outruns swell on the shelf, bends,
#: and converges on Praia do Norte, the beach north of the headland: the reason it has
#: the biggest surfed waves anywhere. Three things follow from the theory and are
#: checked: focusing next to the canyon at long periods, none at short periods (an
#: 8 s wave is in deep water over the whole shelf edge and cannot see the canyon), and
#: a plain open coast further north. Each point is traced from 10 m of water, walking
#: due west from the shore.
PRAIA_DO_NORTE = (39.612, -9.03)
OPEN_COAST = (39.700, -9.03)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def nazare(seabed: Seabed) -> list[Check]:
    periods = (8, 12, 16)
    k = {}
    for name, (lat, lon) in (("canyon", PRAIA_DO_NORTE), ("open", OPEN_COAST)):
        x0, y0 = start_point(seabed, lat, lon, 270.0)
        for t in periods:
            fan = trace(field(seabed, t), x0, y0, step_deg=0.5)
            k[name, t] = refraction(fan, t, 300.0)
    row = lambda n: " / ".join(f"{k[n, t]:.2f}" for t in periods)  # noqa: E731
    return [
        Check(
            "long swell focuses on Praia do Norte",
            k["canyon", 16] >= 1.15 and k["canyon", 16] >= k["open", 16] + 0.15,
            f"300° at 8/12/16 s: Praia do Norte {row('canyon')}, open coast {row('open')}",
        ),
        Check(
            "short swell does not see the canyon",
            abs(k["canyon", 8] - 1.0) <= 0.1,
            f"8 s at Praia do Norte {k['canyon', 8]:.2f}",
        ),
        Check(
            "focusing grows with period",
            k["canyon", 8] < k["canyon", 12] < k["canyon", 16],
            f"{row('canyon')}",
        ),
        Check(
            "the open coast is plain",
            all(abs(k["open", t] - 1.0) <= 0.1 for t in periods),
            f"{row('open')}",
        ),
    ]


# --- every spot ----------------------------------------------------------------------

PERIODS = (6, 8, 10, 12, 14, 16, 18)
DIRECTIONS = tuple(range(0, 360, 5))
#: Periods actually traced. A "12 s swell" carries energy across roughly 10–14 s, and
#: over the Tagus-mouth banks the focusing lines move with period: traced at one period
#: the factor jumps (Caparica, 300°: 0.60 / 0.32 / 0.67 at 8 / 10 / 14 s, unchanged at
#: five times the rays, so not sampling noise). Energy adds across frequencies, so
#: each tabulated period is the energy average over a spread around it.
TRACED_PERIODS = tuple(range(5, 21))
#: Relative width of that spread, a standard deviation: narrow-band swell.
PERIOD_SPREAD = 0.12
#: Places traced for the knowledge checks that are not spots: (lat, lon, facing).
PROBES = {
    "_probe_praia_grande": (38.815, -9.476, 280.0),
}


def beach_point(seabed: Seabed, lat: float, lon: float, faces: float) -> tuple[float, float]:
    """10 m of water straight out from the beach. A spot's coordinates can sit on the
    sand or already out in the bay (Lagide's are 0.8 km off), so walk back towards the
    shore while the water is deep, then out along the facing to 10 m."""
    x, y = seabed.xy(lat, lon)
    ux, uy = math.sin(math.radians(faces)), math.cos(math.radians(faces))
    for _ in range(150):
        h = _sample(seabed.depth, np.array([x / seabed.dx]), np.array([y / seabed.dy]))[0]
        if h < START_DEPTH_M:
            break
        x, y = x - 20.0 * ux, y - 20.0 * uy
    lat2, lon2 = seabed.latlon(x, y)
    return start_point(seabed, lat2, lon2, faces)


def spot_tables(seabed: Seabed) -> dict:
    from gogo.spots import load_spots

    places = {s.id: (s.lat, s.lon, float(s.faces_deg)) for s in load_spots()}
    places.update(PROBES)
    fields = {t: field(seabed, t) for t in TRACED_PERIODS}
    out = {}
    for name, (lat, lon, faces) in places.items():
        x0, y0 = beach_point(seabed, lat, lon, faces)
        energy = {}
        for t in TRACED_PERIODS:
            fan = trace(fields[t], x0, y0, step_deg=0.5)
            energy[t] = np.array([refraction(fan, t, d) ** 2 for d in DIRECTIONS])
        rows = []
        for t in PERIODS:
            weights = {u: math.exp(-0.5 * ((u - t) / (PERIOD_SPREAD * t)) ** 2) for u in energy}
            total = sum(weights.values())
            mean = sum(w * energy[u] for u, w in weights.items()) / total
            rows.append([round(float(v), 3) for v in np.sqrt(mean)])
        slat, slon = seabed.latlon(x0, y0)
        out[name] = {"start": [round(slat, 5), round(slon, 5)], "kr": rows}
        print(f"  {name:22s} from {slat:.4f}, {slon:.4f}", file=sys.stderr)
    return out


def write_tables(seabed: Seabed, path: Path) -> None:
    table = {
        "about": (
            "Refraction factor from the open sea to 10 m of water off each spot, by "
            "period (rows) and swell direction (columns). scripts/wave_rays.py spots."
        ),
        "seabed": "EMODnet Bathymetry mean, 1/16 arc-minute",
        "smoothing_m": SMOOTH_M,
        "spread_s": SPREAD_S,
        "period_spread": PERIOD_SPREAD,
        "start_depth_m": START_DEPTH_M,
        "periods": list(PERIODS),
        "directions": list(DIRECTIONS),
        "spots": spot_tables(seabed),
    }
    path.write_text(json.dumps(table, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=["nazare", "spots"])
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src" / "gogo" / "data" / "rays.json",
    )
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "gogo-bathy")
    args = parser.parse_args()
    seabed = load_seabed(args.cache)
    print(
        f"seabed {seabed.depth.shape[1]}×{seabed.depth.shape[0]} cells, "
        f"{seabed.dx:.0f}×{seabed.dy:.0f} m, deepest {seabed.depth.max():.0f} m",
        file=sys.stderr,
    )
    if args.what == "nazare":
        checks = nazare(seabed)
        for check in checks:
            print(f"{'PASS' if check.passed else 'FAIL'}  {check.name}: {check.detail}")
        return 0 if all(c.passed for c in checks) else 1
    if args.what == "spots":
        write_tables(seabed, args.out)
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
