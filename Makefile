.PHONY: install test weekend weekend-live weekend-db fetch backfill api up down migrate

install:
	uv sync --python 3.12

test:
	.venv/bin/pytest -q

weekend:
	.venv/bin/gogo weekend --fixture tests/fixtures/weekend.json

weekend-live:
	.venv/bin/gogo weekend

weekend-db:
	.venv/bin/gogo weekend --db

fetch:
	.venv/bin/gogo fetch

# make backfill FROM=2026-08-01 TO=2026-08-31
backfill:
	.venv/bin/gogo backfill --from $(FROM) --to $(TO)

api:
	.venv/bin/uvicorn gogo.api:app --reload --app-dir src

migrate:
	.venv/bin/gogo migrate

up:
	docker compose up -d --wait
	.venv/bin/gogo migrate

down:
	docker compose down
