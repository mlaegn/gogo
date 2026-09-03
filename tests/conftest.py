"""Tests never touch the development database.

Writing into `gogo` was tolerable while the suite only produced forecast rows. It stopped
being tolerable the moment it started producing *labels*: the Stage 2 backtest reads
`observations`, and a fabricated Ribeira session is indistinguishable from a real one.

So the suite gets its own database, created once per session, migrated from the files in
`migrations/`, and truncated between tests. The redirect works by setting `DATABASE_URL`,
which `Settings` reads on every `connect()`, so no test needs to know about any of this.

If Postgres is not running, the redirect is skipped and the database tests skip
themselves as they always did — the pure tests still run.
"""

import os

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from gogo.migrate import migrate
from gogo.settings import Settings
from gogo.store import connect

SCRATCH_DB = "gogo_test"

# Everything a test may write. schema_migrations is deliberately absent: truncating it
# would make the migration ledger lie.
MUTABLE_TABLES = (
    "observation_faults",
    "observations",
    "window_impressions",
    "users",
    "forecast_current",
    "forecast_snapshots",
    "spot_grid",
    "spots",
)


def url_for(url: str, dbname: str) -> str:
    params = conninfo_to_dict(url)
    params["dbname"] = dbname
    return make_conninfo(**params)


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Yields the scratch database URL, or None when Postgres is unreachable."""
    development_url = Settings().database_url
    try:
        admin = psycopg.connect(development_url, autocommit=True)
    except Exception:
        yield None
        return

    drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
        sql.Identifier(SCRATCH_DB)
    )
    scratch_url = url_for(development_url, SCRATCH_DB)
    previous = os.environ.get("DATABASE_URL")

    with admin:
        with admin.cursor() as cur:
            cur.execute(drop)
            cur.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(SCRATCH_DB))
            )
        os.environ["DATABASE_URL"] = scratch_url
        with connect(scratch_url) as conn:
            migrate(conn)
        try:
            yield scratch_url
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous
            with admin.cursor() as cur:
                cur.execute(drop)


@pytest.fixture(autouse=True)
def clean_tables(test_database):
    """Each test starts from an empty schema, so order and leftovers cannot matter."""
    yield
    if test_database is None:
        return
    with connect(test_database) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"TRUNCATE {', '.join(MUTABLE_TABLES)} RESTART IDENTITY CASCADE"
            )
        conn.commit()
