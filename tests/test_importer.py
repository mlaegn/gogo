"""S7 — a remembered season becomes a dataset.

Three properties matter more than parsing does. Imported rows must be unanchored, or the
control group is poisoned. A re-import must not double-count, because duplicated labels
reweight the metric invisibly. And the summary has to count same-day pairs, since that is
the sample size of the headline metric and a file of one-spot days has a pair count of
zero however many rows it holds.
"""

from datetime import date
from pathlib import Path

import pytest

from gogo.clock import to_local
from gogo.importer import pair_count, parse, parse_file, summarise
from gogo.spots import by_id, load_spots
from gogo.store import connect, ensure_user, record_observation, seed_spots

FIXTURE = Path(__file__).parent / "fixtures" / "sessions.csv"

HEADER = "date,spot,start,end,kind,rating,faults\n"


def _parse(*rows: str):
    return parse(HEADER + "".join(row + "\n" for row in rows))


def test_the_fixture_parses_completely():
    parsed = parse_file(FIXTURE)
    assert parsed.errors == []
    assert parsed.unknown_columns == []
    assert len(parsed.observations) == 4

    first = parsed.observations[0]
    assert first.spot_id == "ribeira"
    assert first.kind == "surfed"
    assert first.rating == 4
    assert first.would_return is True
    assert first.crowd == "ok"
    assert to_local(first.started_at).strftime("%Y-%m-%d %H:%M") == "2026-03-14 07:15"


def test_imported_rows_are_unanchored():
    """Nothing was predicted to you last March, so nothing could have anchored you."""
    parsed = parse_file(FIXTURE)
    assert all(obs.anchored is False for obs in parsed.observations)
    assert all(obs.residual is None for obs in parsed.observations)


def test_a_row_can_opt_back_into_anchored():
    parsed = parse("date,spot,start,end,anchored\n2026-03-14,ribeira,07:00,08:00,yes\n")
    assert parsed.errors == []
    assert parsed.observations[0].anchored is True


def test_checked_rows_are_what_create_pairs():
    surfed_only = _parse(
        "2026-03-14,ribeira,07:00,09:00,surfed,4,",
        "2026-03-15,coxos,07:00,09:00,surfed,3,",
    )
    assert pair_count(surfed_only.observations) == 0, "different days never pair"

    with_checks = _parse(
        "2026-03-14,ribeira,07:00,09:00,surfed,4,",
        "2026-03-14,coxos,09:10,09:20,checked,2,",
        "2026-03-14,foz_lizandro,09:30,09:40,checked,1,",
    )
    assert pair_count(with_checks.observations) == 3


def test_the_same_spot_twice_in_a_day_is_not_a_pair():
    twice = _parse(
        "2026-03-14,ribeira,07:00,09:00,surfed,4,",
        "2026-03-14,ribeira,17:00,18:00,surfed,2,",
    )
    assert pair_count(twice.observations) == 0


def test_the_summary_names_the_days_that_yield_nothing():
    lines = " | ".join(summarise(parse_file(FIXTURE).observations))
    assert "4 observations across 2 days" in lines
    assert "3 same-day spot pairs" in lines
    assert "1 day with a single spot" in lines


def test_the_summary_flags_days_with_no_reanalysis():
    observations = parse_file(FIXTURE).observations
    lines = " | ".join(summarise(observations, covered={date(2026, 3, 14)}))
    assert "1 day without reanalysis" in lines
    assert "2026-03-15" in lines
    assert "gogo backfill --from 2026-03-15" in lines

    covered = {date(2026, 3, 14), date(2026, 3, 15)}
    assert not any("reanalysis" in line for line in summarise(observations, covered=covered))


def test_faults_accept_a_bare_code_and_reject_nonsense():
    parsed = _parse("2026-03-14,ribeira,07:00,09:00,surfed,4,tide")
    assert parsed.errors == []
    fault = parsed.observations[0].faults[0]
    assert (fault.code, fault.direction) == ("tide", -1)

    assert "not a code" in _parse("2026-03-14,ribeira,07:00,09:00,surfed,4,weather:-1").errors[0]


def test_one_bad_row_does_not_hide_the_good_ones():
    parsed = _parse(
        "2026-03-14,atlantis,07:00,09:00,surfed,4,",
        "2026-03-14,ribeira,07:00,09:00,surfed,4,",
        "2026-03-14,coxos,bogus,09:00,surfed,4,",
        "2026-03-14,ribeira,09:00,08:00,surfed,4,",
    )
    assert len(parsed.observations) == 1
    assert parsed.errors == [
        "line 2: unknown spot 'atlantis'",
        "line 4: start: expected HH:MM, got 'bogus'",
        "line 5: ended_at must be after started_at",
    ]


def test_a_missing_header_column_stops_everything():
    parsed = parse("date,spot,start\n2026-03-14,ribeira,07:00\n")
    assert parsed.observations == []
    assert "header is missing: end" in parsed.errors[0]


def test_unknown_columns_are_reported_but_not_fatal():
    parsed = parse("date,spot,start,end,vibes\n2026-03-14,ribeira,07:00,08:00,good\n")
    assert parsed.unknown_columns == ["vibes"]
    assert len(parsed.observations) == 1


def test_blank_lines_are_skipped():
    parsed = parse(HEADER + ",,,,,,\n2026-03-14,ribeira,07:00,09:00,surfed,4,\n")
    assert parsed.errors == []
    assert len(parsed.observations) == 1


def test_all_fixture_spots_exist():
    known = set(by_id())
    assert {o.spot_id for o in parse_file(FIXTURE).observations} <= known


def test_a_second_import_of_the_same_file_stores_nothing():
    observations = parse_file(FIXTURE).observations
    try:
        conn = connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")

    with conn:
        seed_spots(conn, load_spots())
        user_id = ensure_user(conn, "importer-test")
        first = [record_observation(conn, user_id, obs) for obs in observations]
        assert all(isinstance(i, int) for i in first)

        second = [record_observation(conn, user_id, obs) for obs in observations]
        assert second == [None] * len(observations)

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM observations WHERE user_id = %s", (user_id,))
            assert cur.fetchone()["n"] == len(observations)
