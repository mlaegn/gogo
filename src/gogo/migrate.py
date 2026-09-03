"""Apply `gogo/migrations/*.sql` in filename order, once each.

Postgres no longer applies anything on its own: `docker compose up` starts an empty
server and `gogo migrate` brings it to the current schema. Each file runs inside one
transaction and is recorded in `schema_migrations`, so a re-run is a no-op.

`--baseline 001_init.sql` records everything up to and including that file as applied
*without running it*, for a database that already had those tables before this runner
existed, and then applies whatever comes after. It is a one-off escape hatch, not a
routine.

Filenames must sort in the order they should run, hence the numeric prefixes.

The `.sql` lives inside the package rather than at the repo root because `gogo migrate`
is a console script: an installed wheel has no repo root to walk up to.

Thirty lines instead of Alembic, deliberately: there is one database, one writer, and
the migrations are plain SQL that reads like the schema it produces.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    baselined   BOOLEAN NOT NULL DEFAULT false
)
"""


def migration_files(directory: Path | None = None) -> list[Path]:
    return sorted((directory or MIGRATIONS_DIR).glob("*.sql"))


def applied(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(_LEDGER)
        conn.commit()
        cur.execute("SELECT filename FROM schema_migrations")
        return {row["filename"] for row in cur.fetchall()}


def migrate(
    conn: psycopg.Connection,
    directory: Path | None = None,
    baseline_through: str | None = None,
) -> list[tuple[str, str]]:
    """Apply pending migrations. Returns (filename, "applied" | "baselined") pairs."""
    done = applied(conn)
    acted: list[tuple[str, str]] = []
    for path in migration_files(directory):
        if path.name in done:
            continue
        mark_only = baseline_through is not None and path.name <= baseline_through
        with conn.cursor() as cur:
            if not mark_only:
                cur.execute(path.read_text())
            cur.execute(
                "INSERT INTO schema_migrations (filename, baselined) VALUES (%s, %s)",
                (path.name, mark_only),
            )
        conn.commit()
        acted.append((path.name, "baselined" if mark_only else "applied"))
    return acted
