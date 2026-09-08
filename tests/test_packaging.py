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
from gogo.web import BUNDLE_DIR, STATIC_DIR, TEMPLATE_DIR

PACKAGE = Path(gogo.__file__).resolve().parent


def test_the_spot_file_lives_inside_the_package():
    assert DEFAULT_PATH.is_relative_to(PACKAGE)
    assert DEFAULT_PATH.is_file()
    assert load_spots()


def test_the_migrations_live_inside_the_package():
    assert MIGRATIONS_DIR.is_relative_to(PACKAGE)
    files = migration_files()
    assert files, "no migrations found — the runner would report a fresh database as done"
    assert all(p.is_relative_to(PACKAGE) for p in files)
    assert files[0].name == "001_init.sql"


def test_the_server_rendered_pages_travel_with_the_code():
    """A wheel that ships the routes but not the templates serves a stack trace."""
    for directory in (TEMPLATE_DIR, STATIC_DIR):
        assert directory.is_relative_to(PACKAGE)
        assert directory.is_dir()

    assert (TEMPLATE_DIR / "base.html").is_file()
    assert (STATIC_DIR / "app.css").is_file()
    assert (STATIC_DIR / "favicon.svg").is_file()
    # Every template the routes can still render. The app screens are React now; these
    # are the door and the two things that explain why there is no app to show.
    for name in ("enter", "off", "unbuilt"):
        assert (TEMPLATE_DIR / f"{name}.html").is_file(), name


def test_the_bundle_is_built_not_committed():
    """`src/gogo/static/app` is Vite output, so it must not be required to import gogo.

    Its absence has to stay a readable notice rather than a crash, because that is the
    state of every fresh checkout until `make ui` runs.
    """
    assert BUNDLE_DIR.is_relative_to(PACKAGE)
    assert BUNDLE_DIR.parent == STATIC_DIR
