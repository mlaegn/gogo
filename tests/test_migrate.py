"""Proves the schema builds from nothing.

The local database was baselined rather than migrated, so `001_init.sql` had never been
executed by the runner. With the initdb mount gone, a fresh database depends entirely on
these files applying in order — which makes this the test that catches it.
"""

import psycopg
import pytest
from conftest import url_for
from psycopg import sql

from gogo.migrate import migrate, migration_files
from gogo.settings import Settings
from gogo.store import connect

SCRATCH_DB = "gogo_migrate_scratch"

EXPECTED_TABLES = {
    "schema_migrations",
    "spots",
    "spot_grid",
    "forecast_snapshots",
    "forecast_current",
    "users",
    "observations",
    "observation_faults",
    "window_impressions",
}


@pytest.fixture
def empty_database():
    """A database with nothing in it — not even the migration ledger."""
    try:
        admin = psycopg.connect(Settings().database_url, autocommit=True)
    except Exception as exc:
        pytest.skip(f"Postgres not up: {exc}")

    drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
        sql.Identifier(SCRATCH_DB)
    )
    with admin:
        with admin.cursor() as cur:
            cur.execute(drop)
            cur.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(SCRATCH_DB))
            )
        try:
            yield url_for(Settings().database_url, SCRATCH_DB)
        finally:
            with admin.cursor() as cur:
                cur.execute(drop)


def test_migrations_apply_to_an_empty_database(empty_database):
    with connect(empty_database) as conn:
        acted = migrate(conn)
        assert [name for name, _ in acted] == [p.name for p in migration_files()]
        assert {how for _, how in acted} == {"applied"}

        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            tables = {row["table_name"] for row in cur.fetchall()}

    assert EXPECTED_TABLES <= tables
    assert "sessions" not in tables, "002 should have dropped the placeholder table"


def test_a_second_migrate_is_a_no_op(empty_database):
    with connect(empty_database) as conn:
        assert migrate(conn)
        assert migrate(conn) == []


def test_the_fresh_schema_carries_the_stage_0_columns(empty_database):
    """S2's spec_version and S3's scope must exist on a database built from scratch."""
    with connect(empty_database) as conn:
        migrate(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (table_name, column_name) IN
                      (('spots', 'spec_version'), ('observations', 'scope'),
                       ('window_impressions', 'as_of'))
                """
            )
            found = {
                (row["table_name"], row["column_name"]): row["is_nullable"]
                for row in cur.fetchall()
            }

    assert found[("spots", "spec_version")] == "NO"
    assert found[("observations", "scope")] == "NO"
    assert found[("window_impressions", "as_of")] == "NO"
