"""Per-spot refraction tables traced over the real seabed, read at score time.

`scripts/wave_rays.py spots` traces them offline over EMODnet bathymetry (that needs
numpy, the `eval` group) and writes `data/rays.json`. This module only reads that file,
in plain Python, so the score, the worker and the page never need numpy.

A table gives, for a swell arriving from the open sea from a direction at a period, the
factor by which refraction scales its height on the way to 10 m of water off the spot:
above one where the seabed focuses it, below where it spreads or the land shadows it.
Shoaling is divided out; Komar–Gaughan accounts for that on the way to breaking.

The forecast cells are treated as open sea. Four of the seven sit on land in the real
bathymetry — Open-Meteo's wave grid is kilometres coarse — and the coarse model barely
shelters even the Cascais cell (78–98% of Guincho's swell over a year), so tracing from
the cell would be meaningless and dividing by it would double count very little.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PATH = Path(__file__).parent / "data" / "rays.json"


@dataclass(frozen=True)
class Table:
    periods: tuple[float, ...]
    directions: tuple[float, ...]  # evenly spaced, starting at 0
    kr: tuple[tuple[float, ...], ...]  # [period][direction]


@cache
def load_tables(path: Path = PATH) -> dict[str, Table]:
    """Every spot's table, keyed by spot id. Empty when no file has been traced."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    periods = tuple(float(p) for p in raw["periods"])
    directions = tuple(float(d) for d in raw["directions"])
    return {
        spot_id: Table(periods, directions, tuple(tuple(row) for row in entry["kr"]))
        for spot_id, entry in raw["spots"].items()
    }


def factor(table: Table, from_deg: float, period: float) -> float:
    """The refraction factor for a swell from `from_deg` at `period`: linear in
    direction (wrapping at north) and in period, held flat beyond the traced periods."""
    step = table.directions[1] - table.directions[0]
    position = (from_deg % 360.0) / step
    lo = int(position) % len(table.directions)
    hi = (lo + 1) % len(table.directions)
    t = position - int(position)

    def at(row: tuple[float, ...]) -> float:
        return row[lo] * (1 - t) + row[hi] * t

    periods = table.periods
    if period <= periods[0]:
        return at(table.kr[0])
    if period >= periods[-1]:
        return at(table.kr[-1])
    for i in range(len(periods) - 1):
        if period <= periods[i + 1]:
            w = (period - periods[i]) / (periods[i + 1] - periods[i])
            return at(table.kr[i]) * (1 - w) + at(table.kr[i + 1]) * w
    return at(table.kr[-1])
