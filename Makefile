.PHONY: install test weekend weekend-live up down

install:
	uv venv --python 3.12 .venv
	uv pip install --python .venv/bin/python -e ".[dev]"

test:
	.venv/bin/pytest -q

weekend:
	.venv/bin/gogo weekend --fixture tests/fixtures/weekend.json

weekend-live:
	.venv/bin/gogo weekend

up:
	docker compose up -d

down:
	docker compose down
