.PHONY: install test weekend weekend-live weekend-db fetch api up down

install:
	uv venv --python 3.12 --clear .venv
	uv pip install --python .venv/bin/python -e ".[dev]"

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
	docker compose up -d

down:
	docker compose down
