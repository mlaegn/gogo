"""The spot file and the migrations must travel with the code.

Both used to be resolved as `parents[2]`, which is the repo root from a checkout and
somewhere above `site-packages` from a wheel. Nothing noticed, because every way we run
gogo today happens to be a checkout. The first container built from the wheel would have
found no spots and no migrations.

So this asserts the weaker, portable property: the paths stay inside the package.
"""

from pathlib import Path

import gogo
from gogo.migrate import MIGRATIONS_DIR, migration_files
from gogo.spots import DEFAULT_PATH, load_spots

PACKAGE = Path(gogo.__file__).resolve().parent


def test_the_spot_file_lives_inside_the_package():
    assert DEFAULT_PATH.is_relative_to(PACKAGE)
    assert DEFAULT_PATH.is_file()
    assert load_spots()


def test_the_migrations_live_inside_the_package():
    assert MIGRATIONS_DIR.is_relative_to(PACKAGE)
    names = [p.name for p in migration_files()]
    assert names == ["001_init.sql", "002_observations.sql"]
