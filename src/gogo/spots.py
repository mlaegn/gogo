from __future__ import annotations

from pathlib import Path

import yaml

from gogo.models import Spot

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "spots" / "coast.yml"


def load_spots(path: Path | None = None) -> list[Spot]:
    raw = yaml.safe_load((path or DEFAULT_PATH).read_text())
    return [Spot.model_validate(row) for row in raw["spots"]]


def by_id(spots: list[Spot] | None = None) -> dict[str, Spot]:
    return {s.id: s for s in (spots or load_spots())}
