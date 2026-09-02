.PHONY: install test weekend weekend-live weekend-db fetch api up down

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

api:
	.venv/bin/uvicorn gogo.api:app --reload --app-dir src

up:
	docker compose up -d --wait

down:
	docker compose down
