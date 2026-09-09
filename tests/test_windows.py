"""S5 — the served unit is a range.

An observation is an interval and a prediction now is too, so these tests are about the
rule that turns twenty-four scored hours into one recommendation: where a run breaks,
what number represents it, and which sentence gets shown next to that number.
"""

from datetime import date, timedelta

from helpers import grid_day

from gogo.assemble import (
    SURFABLE_FROM_HOUR,
    plan_day,
    runs_of_passing_hours,
    window_from_run,
    windows_for_day,
)
from gogo.clock import from_local_input, to_local
from gogo.models import HourScore, Reason
from gogo.score import verdict_for
from gogo.spots import load_spots

DAY = date(2026, 9, 5)  # a Saturday
RIBEIRA = [s for s in load_spots() if s.id == "ribeira"]


def _scored(*pairs: tuple[int, int], vetoed_hours: tuple[int, ...] = ()) -> list[HourScore]:
    """(local hour, score) pairs → HourScores, with reasons that name their own hour."""
    return [
        HourScore(
            spot_id="ribeira",
            spot_name="Ribeira d'Ilhas",
            valid_at=from_local_input(DAY, f"{hour:02d}:00"),
            score=score,
            verdict=verdict_for(score, hour in vetoed_hours),
            reasons=[Reason(code="size", detail=f"hour {hour}", points=score)],
            vetoed=hour in vetoed_hours,
        )
        for hour, score in pairs
    ]


def test_adjacent_passing_hours_become_one_run():
    runs = runs_of_passing_hours(_scored((7, 80), (8, 85), (9, 75)))
    assert [len(run) for run in runs] == [3]


def test_a_failing_hour_splits_the_run_rather_than_averaging_into_it():
    """25 kn of onshore at 09:00 does not become tolerable because 08:00 was clean."""
    runs = runs_of_passing_hours(_scored((7, 80), (8, 85), (9, 20), (10, 75), (11, 90)))
    assert [[to_local(h.valid_at).hour for h in run] for run in runs] == [[7, 8], [10, 11]]


def test_a_gap_in_the_data_splits_the_run():
    """Two hours either side of a hole are not evidence of anything in between."""
    runs = runs_of_passing_hours(_scored((7, 80), (8, 85), (11, 75), (12, 78)))
    assert [[to_local(h.valid_at).hour for h in run] for run in runs] == [[7, 8], [11, 12]]


def test_a_day_with_nothing_passing_has_no_runs():
    assert runs_of_passing_hours(_scored((7, 10), (8, 20))) == []
    assert runs_of_passing_hours([]) == []


def test_the_window_score_is_the_mean_not_the_best_hour():
    window = window_from_run(_scored((7, 60), (8, 100), (9, 62)))
    assert window.score == 74
    assert window.peak_score == 100
    assert to_local(window.peak_at).hour == 8


def test_the_reasons_come_from_the_hour_that_matches_the_score():
    """The sentence has to explain the number, not the best moment inside it."""
    window = window_from_run(_scored((7, 60), (8, 100), (9, 62)))
    assert window.score == 74
    # 62 is nearest 74, so 09:00 speaks for the window — not the 100-point 08:00.
    assert window.reasons[0].detail == "hour 9"


def test_ties_for_representative_go_to_the_earlier_hour():
    """You paddle out at the start, so the earlier hour wins an equal claim."""
    window = window_from_run(_scored((7, 70), (8, 90), (9, 50)))
    assert window.score == 70
    assert window.reasons[0].detail == "hour 7"


def test_the_end_of_a_window_is_exclusive():
    """A single 08:00 hour is 08:00-09:00 and counts as one hour, not zero."""
    one = window_from_run(_scored((8, 80)))
    assert one.hours == 1
    assert one.ends_at - one.starts_at == timedelta(hours=1)
    assert to_local(one.starts_at).hour == 8
    assert to_local(one.ends_at).hour == 9

    three = window_from_run(_scored((7, 80), (8, 80), (9, 80)))
    assert three.hours == 3
    assert three.ends_at - three.starts_at == timedelta(hours=3)


def test_a_real_day_produces_one_ranked_range_per_spot():
    spots = load_spots()
    ranked = windows_for_day(spots, grid_day(DAY, spots, 7, 11), DAY)

    assert len(ranked) == len(spots), "every spot with data gets a verdict"
    assert [w.score for w in ranked] == sorted((w.score for w in ranked), reverse=True)
    for window in ranked:
        assert window.starts_at < window.ends_at
        assert window.starts_at <= window.peak_at < window.ends_at

    passing = [w for w in ranked if w.verdict != "no"]
    assert passing, "the fixture conditions should suit somewhere"
    assert max(w.hours for w in passing) > 1, "adjacent passing hours should group"


def test_hours_in_the_dark_are_not_offered():
    """Nothing in the score knows the sun is down, so the search window bounds it.

    Without this, a glassy 03:00 would be ranked as the day's best call.
    """
    night = grid_day(DAY, RIBEIRA, 2, 5)
    assert windows_for_day(RIBEIRA, night, DAY) == []

    straddling = grid_day(DAY, RIBEIRA, 4, 9)
    ranked = windows_for_day(RIBEIRA, straddling, DAY)
    assert len(ranked) == 1
    assert to_local(ranked[0].starts_at).hour >= SURFABLE_FROM_HOUR


def test_a_closed_spot_reports_the_span_it_searched_and_why():
    """A wrong veto has to leave a trace, so a shut spot still gets a row."""
    offshore_swell = grid_day(DAY, RIBEIRA, 7, 11, swell_from_deg=100)
    ranked = windows_for_day(RIBEIRA, offshore_swell, DAY)

    assert len(ranked) == 1
    closed = ranked[0]
    assert closed.verdict == "no"
    assert closed.vetoed is True
    assert closed.hours == 4
    assert "outside" in " ".join(r.detail for r in closed.reasons)


def test_a_day_with_no_hours_at_all_ranks_nothing():
    spots = load_spots()
    assert windows_for_day(spots, grid_day(DAY, spots, 7, 11), date(2026, 9, 6)) == []
    assert plan_day([]) is None


def test_the_carried_features_survive_the_grid_to_forecast_step():
    """`assemble` enumerates fields by hand, so a new one is dropped unless added here.

    Storing a feature that the scoring boundary never sees is worse than not storing it:
    the row looks complete in Postgres and the harness finds nothing to test.
    """
    from gogo.assemble import forecasts_from_grid
    from helpers import grid_hour

    series = forecasts_from_grid(
        [
            grid_hour(
                swell_peak_period_s=13.4,
                combined_height_m=1.9,
                combined_period_s=7.2,
                swell2_height_m=0.6,
                swell2_from_deg=200,
                swell2_period_s=8.1,
            )
        ]
    )
    assert len(series) == 1
    h = series[0]
    assert h.swell_peak_period_s == 13.4
    assert (h.combined_height_m, h.combined_period_s) == (1.9, 7.2)
    assert (h.swell2_height_m, h.swell2_from_deg, h.swell2_period_s) == (0.6, 200, 8.1)
