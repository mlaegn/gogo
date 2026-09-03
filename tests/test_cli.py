import pytest
from pydantic import ValidationError

from gogo.cli import _parse_fault, main


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
