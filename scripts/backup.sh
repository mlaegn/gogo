#!/bin/sh
# Dump from inside the Postgres container over the local socket — no password on
# the argv, and the file is written on the host with mode 600.
set -eu

root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$root"

umask 077
mkdir -p backups
chmod 700 backups

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="backups/gogo-${stamp}.sql.gz"

docker compose -f docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$out"

find backups -name 'gogo-*.sql.gz' -mtime +14 -delete

echo "wrote $out"
