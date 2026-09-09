#!/bin/sh
# Long-running processes migrate on boot. Worker and api share a Postgres advisory
# lock so two starts cannot apply the same file. One-shot CLI is left alone.
set -eu
case "${1-}" in
  gogo)
    case "${2-}" in
      worker) gogo migrate ;;
    esac
    ;;
  uvicorn) gogo migrate ;;
esac
exec "$@"
