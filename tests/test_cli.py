from gogo.cli import main


def test_weekend_fixture(capsys):
    assert main(["weekend", "--fixture", "tests/fixtures/weekend.json"]) == 0
    out = capsys.readouterr().out
    assert "Ribeira" in out or "Foz" in out
    assert "Saturday" in out
