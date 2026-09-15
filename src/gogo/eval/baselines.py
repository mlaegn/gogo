"""Things the score has to beat before anyone should believe in it.

A number on its own says nothing. "68% pairwise accuracy" is excellent or embarrassing
depending entirely on what always-Carcavelos gets, and the only way to find out is to
compute both from the same rows.

These exist to be uncomfortable. The plan says it plainly: if the hand-tuned score loses
to biggest-swell-wins, that is the finding we needed. A baseline suite you cannot lose to
is decoration.

Each baseline is a function from a sample to a number where higher means better, used
only for *ranking* two spots on one day. The absolute values are not comparable across
baselines and are never meant to be.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from gogo.eval.dataset import Sample
from gogo.geo import angle_distance
from gogo.models import Spot
from gogo.spots import by_id

#: A predictor takes one labelled session and returns "how good we think it was".
Baseline = Callable[[Sample], float | None]

#: Roughly onshore, matching the score's own gate so the comparison is about the rule
#: rather than about a different definition of onshore.
_ONSHORE_ALIGN_DEG = 75


def incumbent(sample: Sample) -> float | None:
    """The score as it stands. The thing under test, not a baseline — but it has to be
    measured the same way as everything else or the comparison is rigged."""
    return None if sample.predicted_score is None else float(sample.predicted_score)


def biggest_swell(sample: Sample) -> float | None:
    """Go wherever it is biggest. Crude, and beats a lot of surf forecasting."""
    return sample.mean_swell_m


def always(spot_id: str) -> Baseline:
    """Always name the same spot. The "do not bother modelling" baseline.

    Carcavelos is the interesting one on this coast: it works small, it works often, and
    it is nearest Lisbon. A score that cannot beat "drive to Carcavelos" is not earning
    the drive to Ericeira.
    """

    def predict(sample: Sample) -> float:
        return 1.0 if sample.spot_id == spot_id else 0.0

    return predict


def height_times_period(spots: list[Spot] | None = None) -> Baseline:
    """Energy, with the one veto nobody argues about.

    Height times period is the back-of-the-envelope every surfer already does, and
    onshore wind is the single gate no forecast gets wrong. So this is the strongest
    baseline that requires no per-spot knowledge at all — which makes it the honest
    measure of what `coast.yml` is actually worth.
    """
    catalogue = by_id(spots)

    def predict(sample: Sample) -> float | None:
        spot = catalogue.get(sample.spot_id)
        if spot is None or not sample.forecasts:
            return None
        total = 0.0
        for hour in sample.forecasts:
            onshore_from = (spot.offshore_from + 180) % 360
            blown = (
                angle_distance(hour.wind_from_deg, onshore_from) <= _ONSHORE_ALIGN_DEG
                and hour.wind_speed_kn > spot.max_onshore_kn
            )
            total += 0.0 if blown else hour.swell_height_m * hour.swell_period_s
        return total / len(sample.forecasts)

    return predict


def random_baseline(seed: int = 0) -> Baseline:
    """Coin flips, but the same coin flips every run.

    Derived from the observation id rather than drawn from a stream, so a baseline's
    number does not change because some other metric consumed a random value first.
    Reproducibility is the whole point of having a floor.
    """

    def predict(sample: Sample) -> float:
        digest = hashlib.sha256(f"{seed}:{sample.observation_id}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / 2**64

    return predict


def standard(spots: list[Spot] | None = None) -> dict[str, Baseline]:
    """The suite the gate is reported against, incumbent included for comparison."""
    return {
        "random": random_baseline(),
        "always_carcavelos": always("carcavelos"),
        "biggest_swell": biggest_swell,
        "height_x_period": height_times_period(spots),
        "incumbent": incumbent,
    }
