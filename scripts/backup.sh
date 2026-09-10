#!/bin/sh
# Nightly dump, verified, with an optional copy off the box.
#
# Three things beyond pg_dump, all because a backup that looks fine and is not is worse
# than no backup at all — it removes the worry without removing the risk.
#
# 1. The dump is written to a file and checked before it is compressed, rather than
#    piped straight into gzip. POSIX `set -e` cannot see a failure on the left of a
#    pipe, so `pg_dump | gzip` returned 0 even when pg_dump died: every night would have
#    produced a small, valid, useless .gz and said "wrote". That was the previous shape.
# 2. The dump has to end the way pg_dump ends one, and contain the table this whole
#    project exists to fill. A truncated dump is the normal way this fails.
# 3. GOGO_BACKUP_DEST, if set, is an rsync destination and gets a copy. A dump sitting
#    on the same disk as the database it came from survives a mistake, not a dead disk.
#    Local path or user@host:/path/ both work; end a directory with a slash.
#
# Old dumps are pruned last, and only after the off-box copy has succeeded, so a broken
# destination can never combine with the retention window to leave you holding nothing.
#
# Still not covered, and worth doing by hand once: restoring one. An untested restore is
# a belief, not a backup.
set -eu

root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$root"

umask 077
mkdir -p backups
chmod 700 backups

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
plain="backups/gogo-${stamp}.sql"
out="${plain}.gz"

# No password on the argv: pg_dump runs inside the container over the local socket.
docker compose -f docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$plain"

fail() {
  rm -f "$plain"
  echo "backup FAILED: $1" >&2
  exit 1
}

grep -q 'PostgreSQL database dump complete' "$plain" \
  || fail "dump is truncated (pg_dump never reached the end)"
grep -q 'CREATE TABLE public.observations' "$plain" \
  || fail "no observations table in the dump — wrong database?"

gzip "$plain"

if [ -n "${GOGO_BACKUP_DEST:-}" ]; then
  command -v rsync >/dev/null 2>&1 \
    || { echo "GOGO_BACKUP_DEST is set but rsync is missing; local copy kept" >&2; exit 1; }
  rsync -a "$out" "$GOGO_BACKUP_DEST" \
    || { echo "copy to $GOGO_BACKUP_DEST failed; local copy kept, nothing pruned" >&2; exit 1; }
  echo "copied to $GOGO_BACKUP_DEST"
fi

find backups -name 'gogo-*.sql.gz' -mtime +14 -delete

echo "wrote $out ($(du -h "$out" | cut -f1))"
