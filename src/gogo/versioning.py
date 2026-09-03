"""Stable identifiers for the things that decide a recommendation.

A stored verdict is only replayable if it records *which* rules produced it. Two
identifiers do that: `SCORE_VERSION` in `score.py` for the scoring function, and
`spec_version` here for one spot's definition.

The hash must be identical across processes and machines, so it uses `hashlib` and not
`hash()`. It must also be blind to anything that cannot change a recommendation:
reformatting `coast.yml`, reordering keys, reordering the tide list, or renaming a
spot all leave the version untouched. Anything that *can* change a recommendation —
directions, sizes, periods, wind caps, tides, coordinates — is part of the hash.

Changing the canonical form below orphans every historical row that points at an old
version, which is why `test_versioning.py` pins the digest of a fixed spec.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from gogo.models import Spot

_DIGEST_CHARS = 12

# Identity and cosmetics: renaming Ribeira d'Ilhas does not change how it breaks.
_EXCLUDED = frozenset({"id", "name"})


def canonical_spec(spot: Spot) -> str:
    fields: dict[str, Any] = {
        key: value
        for key, value in spot.model_dump(mode="json").items()
        if key not in _EXCLUDED
    }
    # The tide list is a set: [mid, high] and [high, mid] describe the same spot.
    tides = fields.get("tides")
    if isinstance(tides, list):
        fields["tides"] = sorted(tides)
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def spec_version(spot: Spot) -> str:
    """Short, stable digest of everything about a spot that can change a rank."""
    digest = hashlib.sha256(canonical_spec(spot).encode("utf-8")).hexdigest()
    return digest[:_DIGEST_CHARS]
