import pytest
from pydantic import ValidationError

from gogo.cli import _parse_fault, main
from gogo.store import connect


def _connect_or_skip():
    try:
        return connect()
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")


def test_weekend_fixture(capsys):
    assert main(["weekend", "--fixture", "tests/fixtures/weekend.json"]) == 0
    out = capsys.readouterr().out
    assert "Ribeira" in out or "Foz" in out
    assert "Saturday" in out


def test_fault_shorthand():
    """`--fault tide:-1` — the tide gate was worse than we predicted."""
    fault = _parse_fault("tide:-1")
    assert (fault.code, fault.direction) == ("tide", -1)
    assert _parse_fault("wind:1").direction == 1


def test_fault_defaults_to_worse_than_predicted():
    """Reporting a fault at all almost always means we over-promised."""
    assert _parse_fault("size").direction == -1


def test_fault_rejects_a_gate_the_score_does_not_have():
    with pytest.raises(ValidationError):
        _parse_fault("crowd:-1")


def test_unknown_spot_is_rejected_before_touching_the_database(capsys):
    code = main(["log", "nazare", "--start", "07:00", "--end", "09:00"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Unknown spot" in out
    assert "ribeira" in out


def test_import_dry_run_writes_nothing(capsys):
    """The reason --dry-run exists: a hand-written file gets checked before it lands."""
    assert main(["import", "tests/fixtures/sessions.csv", "--dry-run"]) == 0
    assert "nothing written" in capsys.readouterr().out

    conn = _connect_or_skip()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM observations")
        assert cur.fetchone()["n"] == 0


def test_import_stores_once_then_reports_the_rest_as_already_present(capsys):
    _connect_or_skip().close()
    assert main(["import", "tests/fixtures/sessions.csv", "--user", "cli-test"]) == 0
    assert "Imported 4 observations as cli-test." in capsys.readouterr().out

    assert main(["import", "tests/fixtures/sessions.csv", "--user", "cli-test"]) == 0
    out = capsys.readouterr().out
    assert "Imported 0 observations" in out
    assert "4 were already recorded" in out


def test_import_of_a_missing_file_fails_cleanly(capsys):
    assert main(["import", "tests/fixtures/nope.csv"]) == 1
    assert "No such file" in capsys.readouterr().out
