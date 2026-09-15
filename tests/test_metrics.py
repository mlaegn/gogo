"""S10 — the metrics, tested against data whose answer is known in advance.

A metric is a measuring device, and the way to trust one is to feed it a quantity you
already know and check the reading. So most of these build tiny synthetic datasets with
an obvious right answer — a predictor that agrees perfectly must score 1.0, one that
disagrees perfectly must score 0.0 — rather than asserting that some number came out of
real data.

The other half is refusal. With too little data every one of these must say so instead of
returning a plausible-looking number, because a harness that reports 0.5 on an empty
table is worse than one that crashes.
"""

from datetime import date, datetime, timedelta

from gogo.clock import UTC
from gogo.eval import baselines
from gogo.eval.dataset import Dataset, Sample
from gogo.eval.metrics import (
    POOR_RATING,
    brier_would_return,
    calibration,
    compare,
    ndcg_at_3,
    pairwise_accuracy,
    veto_quality,
)
from gogo.models import HourForecast, HourScore, Reason

DAY = date(2026, 9, 5)


def _sample(
    obs_id: int,
    spot_id: str,
    rating: int | None,
    predicted: int | None,
    *,
    day: date = DAY,
    event: int = 0,
    would_return: bool | None = None,
    vetoed: bool = False,
    swell: float = 1.5,
) -> Sample:
    at = datetime(day.year, day.month, day.day, 7, tzinfo=UTC)
    forecast = HourForecast(
        valid_at=at, swell_height_m=swell, swell_from_deg=290, swell_period_s=11.0,
        wind_speed_kn=8.0, wind_from_deg=70,
    )
    hour = HourScore(
        spot_id=spot_id, spot_name=spot_id, valid_at=at,
        score=0 if vetoed else (predicted or 0),
        verdict="no" if vetoed else "go",
        reasons=[Reason(code="size", detail="x", points=10)],
        vetoed=vetoed,
    )
    return Sample(
        observation_id=obs_id, event_id=event, spot_id=spot_id, day=day,
        started_at=at, ended_at=at + timedelta(hours=1), kind="surfed",
        anchored=False, is_synthetic=False, rating=rating, residual=None,
        would_return=would_return, crowd=None, faults=[], forecasts=[forecast],
        hours=[hour], predicted_score=predicted,
        predicted_verdict="no" if vetoed else "go",
        shown_score=None, shown_verdict=None, score_version="v2", spec_version="abc",
    )


def _dataset(samples) -> Dataset:
    return Dataset(policy="best_known", samples=list(samples))


# --- pairwise accuracy, the headline ------------------------------------------------


def test_a_predictor_that_agrees_perfectly_scores_one():
    data = _dataset([
        _sample(1, "ribeira", rating=5, predicted=90),
        _sample(2, "coxos", rating=2, predicted=30),
    ])
    got = pairwise_accuracy(data, baselines.incumbent)
    assert got.value == 1.0
    assert got.n == 1


def test_a_predictor_that_gets_it_exactly_backwards_scores_zero():
    data = _dataset([
        _sample(1, "ribeira", rating=5, predicted=10),
        _sample(2, "coxos", rating=2, predicted=90),
    ])
    assert pairwise_accuracy(data, baselines.incumbent).value == 0.0


def test_a_predictor_with_no_opinion_scores_half():
    """A tie is half credit: no preference expressed, so neither win nor loss."""
    data = _dataset([
        _sample(1, "ribeira", rating=5, predicted=50),
        _sample(2, "coxos", rating=2, predicted=50),
    ])
    assert pairwise_accuracy(data, baselines.incumbent).value == 0.5


def test_pairs_the_human_could_not_separate_are_dropped_not_counted_as_agreement():
    """Equal ratings carry no ordering information. Counting them would reward a
    predictor for being uninformative on exactly the days nobody could tell either."""
    data = _dataset([
        _sample(1, "ribeira", rating=3, predicted=90),
        _sample(2, "coxos", rating=3, predicted=10),
    ])
    got = pairwise_accuracy(data, baselines.incumbent)
    assert got.value is None
    assert got.n == 0


def test_the_same_spot_twice_in_a_day_is_not_a_pair():
    data = _dataset([
        _sample(1, "ribeira", rating=5, predicted=90),
        _sample(2, "ribeira", rating=2, predicted=30),
    ])
    assert pairwise_accuracy(data, baselines.incumbent).n == 0


def test_two_spots_on_different_days_are_not_a_pair():
    data = _dataset([
        _sample(1, "ribeira", rating=5, predicted=90),
        _sample(2, "coxos", rating=2, predicted=30, day=DAY - timedelta(days=9), event=1),
    ])
    assert pairwise_accuracy(data, baselines.incumbent).n == 0


# --- refusing to answer ---------------------------------------------------------------


def test_an_empty_dataset_says_so_rather_than_returning_a_number():
    """0.0 reads as a terrible score and 0.5 as a coin flip. Both would be lies."""
    empty = _dataset([])
    assert pairwise_accuracy(empty, baselines.incumbent).value is None
    assert ndcg_at_3(empty, baselines.incumbent).value is None
    assert brier_would_return(empty, baselines.incumbent).value is None
    assert veto_quality(empty).precision.value is None
    assert calibration(empty) == []


def test_one_event_gets_a_value_but_no_interval():
    """A confidence interval from a single swell would be theatre."""
    got = pairwise_accuracy(
        _dataset([
            _sample(1, "ribeira", rating=5, predicted=90),
            _sample(2, "coxos", rating=2, predicted=30),
        ]),
        baselines.incumbent,
    )
    assert got.value == 1.0
    assert got.lo is None and got.hi is None
    assert "n/a" not in str(got)


def test_the_interval_appears_once_there_are_several_events():
    samples = []
    for event in range(6):
        day = DAY - timedelta(days=event * 5)
        samples += [
            _sample(event * 2, "ribeira", rating=5, predicted=90, day=day, event=event),
            _sample(event * 2 + 1, "coxos", rating=2, predicted=30, day=day, event=event),
        ]
    got = pairwise_accuracy(_dataset(samples), baselines.incumbent)
    assert got.events == 6
    assert got.lo is not None and got.hi is not None
    assert got.lo <= got.value <= got.hi


def test_the_interval_is_resampled_over_events_not_rows():
    """Six labels in one swell must not buy the certainty of six swells.

    Same number of rows, same perfect-plus-one-wrong pattern; only the event grouping
    differs. Treating one swell as six independent draws would return a visibly tighter
    interval, which is precisely how a harness ends up confidently wrong.
    """
    def build(events: list[int]) -> Dataset:
        samples = []
        for i, event in enumerate(events):
            day = DAY - timedelta(days=i * 5)
            backwards = i == 0
            samples += [
                _sample(i * 2, "ribeira", rating=5,
                        predicted=10 if backwards else 90, day=day, event=event),
                _sample(i * 2 + 1, "coxos", rating=2,
                        predicted=90 if backwards else 30, day=day, event=event),
            ]
        return _dataset(samples)

    one_swell = pairwise_accuracy(build([0] * 6), baselines.incumbent)
    six_swells = pairwise_accuracy(build(list(range(6))), baselines.incumbent)

    assert one_swell.value == six_swells.value, "same rows, same point estimate"
    assert one_swell.lo is None, "a single event cannot support an interval at all"
    assert six_swells.lo is not None


# --- baselines ------------------------------------------------------------------------


def test_always_carcavelos_ranks_carcavelos_above_everything():
    predict = baselines.always("carcavelos")
    assert predict(_sample(1, "carcavelos", 3, 50)) > predict(_sample(2, "coxos", 3, 50))


def test_biggest_swell_ranks_by_size_alone():
    predict = baselines.biggest_swell
    assert predict(_sample(1, "a", 3, 50, swell=3.0)) > predict(
        _sample(2, "b", 3, 50, swell=1.0)
    )


def test_random_is_reproducible_across_runs_and_independent_of_call_order():
    a, b = baselines.random_baseline(7), baselines.random_baseline(7)
    sample = _sample(42, "ribeira", 3, 50)
    baselines.random_baseline(7)(_sample(1, "x", 3, 50))  # consume nothing
    assert a(sample) == b(sample)
    assert baselines.random_baseline(8)(sample) != a(sample)


def test_the_suite_runs_every_baseline_on_the_same_rows():
    data = _dataset([
        _sample(1, "ribeira", rating=5, predicted=90, swell=2.0),
        _sample(2, "carcavelos", rating=2, predicted=30, swell=1.0),
    ])
    results = compare(data, baselines.standard())
    assert set(results) == {
        "random", "always_carcavelos", "biggest_swell", "height_x_period", "incumbent",
    }
    assert results["incumbent"].value == 1.0
    assert results["biggest_swell"].value == 1.0
    # Carcavelos was the worse spot that day, so naming it always is exactly wrong.
    assert results["always_carcavelos"].value == 0.0


# --- the veto -------------------------------------------------------------------------


def test_veto_precision_is_how_often_a_refusal_was_right():
    data = _dataset([
        _sample(1, "a", rating=1, predicted=0, vetoed=True),   # right to refuse
        _sample(2, "b", rating=5, predicted=0, vetoed=True),   # wrong to refuse
        _sample(3, "c", rating=4, predicted=70),               # not refused
    ])
    quality = veto_quality(data)
    assert quality.vetoed == 2
    assert quality.precision.value == 0.5


def test_veto_recall_is_how_many_bad_sessions_were_caught():
    data = _dataset([
        _sample(1, "a", rating=1, predicted=0, vetoed=True),
        _sample(2, "b", rating=POOR_RATING, predicted=60),  # bad, and we missed it
    ])
    assert veto_quality(data).recall.value == 0.5


def test_a_score_that_never_refuses_has_no_precision_to_report():
    data = _dataset([_sample(1, "a", rating=1, predicted=60)])
    quality = veto_quality(data)
    assert quality.precision.value is None
    assert quality.recall.value == 0.0, "it caught none of the bad ones"


# --- calibration and the rest ---------------------------------------------------------


def test_calibration_bands_are_ordered_when_the_score_is_meaningful():
    data = _dataset([
        _sample(1, "a", rating=1, predicted=10),
        _sample(2, "b", rating=2, predicted=30),
        _sample(3, "c", rating=4, predicted=70),
        _sample(4, "d", rating=5, predicted=90),
    ])
    bands = calibration(data)
    assert [b.low for b in bands] == [0, 20, 60, 80]
    assert [b.mean_rating for b in bands] == sorted(b.mean_rating for b in bands)


def test_brier_rewards_confidence_that_turns_out_right():
    sure_and_right = _dataset([_sample(1, "a", 5, 100, would_return=True)])
    sure_and_wrong = _dataset([_sample(1, "a", 5, 100, would_return=False)])
    assert brier_would_return(sure_and_right, baselines.incumbent).value == 0.0
    assert brier_would_return(sure_and_wrong, baselines.incumbent).value == 1.0


def test_brier_ignores_sessions_nobody_answered():
    data = _dataset([_sample(1, "a", 5, 90, would_return=None)])
    assert brier_would_return(data, baselines.incumbent).value is None


def test_ndcg_needs_three_spots_on_a_day():
    two = _dataset([
        _sample(1, "a", rating=5, predicted=90),
        _sample(2, "b", rating=2, predicted=30),
    ])
    assert ndcg_at_3(two, baselines.incumbent).value is None

    three = _dataset([
        _sample(1, "a", rating=5, predicted=90),
        _sample(2, "b", rating=3, predicted=60),
        _sample(3, "c", rating=1, predicted=10),
    ])
    got = ndcg_at_3(three, baselines.incumbent)
    assert got.value == 1.0, "a perfect ordering is a perfect ndcg"
    assert got.n == 1


def test_ndcg_punishes_putting_the_worst_spot_first():
    data = _dataset([
        _sample(1, "a", rating=5, predicted=10),
        _sample(2, "b", rating=3, predicted=60),
        _sample(3, "c", rating=1, predicted=90),
    ])
    assert ndcg_at_3(data, baselines.incumbent).value < 1.0
