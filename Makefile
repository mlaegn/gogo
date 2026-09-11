.PHONY: install test weekend weekend-live weekend-db fetch backfill api web phone up down \
	migrate ui ui-deps ui-dev ui-types openapi host host-web host-public backup health tunnel

# Python only, on purpose: the score, the worker and the tests must stay installable
# without a Node toolchain. `make ui` is the frontend's entry point.
install:
	uv sync --python 3.12

ui-deps:
	cd web && npm install

# Typecheck then bundle into src/gogo/static/app, where FastAPI serves it from.
ui: ui-deps
	cd web && npm run build

# Frontend work: this on :5173 with `make web` running behind it on :8000.
ui-dev:
	cd web && npm run dev

# FastAPI's schema, dumped without starting a server.
openapi:
	.venv/bin/python -c "import json; from gogo.api import app; print(json.dumps(app.openapi(), indent=2))" > openapi.json

# Regenerate the client's types from that schema. CI runs this and fails on a diff, so
# renaming a field in Python breaks the build instead of rendering an empty span.
ui-types: openapi
	cd web && npm run types

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

# The page. An unset GOGO_WEB_SECRET means off, not open, so dev sets a throwaway one.
# Needs `make ui` once, or it serves a "not built" notice instead of the app.
web:
	GOGO_WEB_SECRET=$${GOGO_WEB_SECRET:-devkey} \
		.venv/bin/uvicorn gogo.api:app --reload --app-dir src

# Same, reachable from your phone on the same wifi: open http://<your-ip>:8000
phone:
	@echo "Phone: http://$$(ipconfig getifaddr en0):8000  key: $${GOGO_WEB_SECRET:-devkey}"
	GOGO_WEB_SECRET=$${GOGO_WEB_SECRET:-devkey} \
		.venv/bin/uvicorn gogo.api:app --host 0.0.0.0 --app-dir src

# The deployed page, on this laptop, through SSH. The honest dev answer: no DNS, no
# certificate, no account, nothing to install. SSH is the encryption, and what you log
# lands in the box's database — the one with the forecast history — instead of starting
# a second one here.
#   make tunnel HOST=root@your.box
tunnel:
	@echo "→ http://localhost:8088   (Ctrl-C to close)"
	ssh -N -L 8088:127.0.0.1:8000 $(HOST)

# Is the forecast fresh and is every spot still ranked? Exit 1 if not.
health:
	.venv/bin/gogo health

migrate:
	.venv/bin/gogo migrate

up:
	docker compose up -d --wait
	.venv/bin/gogo migrate

down:
	docker compose down

# VPS: Postgres + worker. Needs .env with POSTGRES_PASSWORD. See README.
host:
	docker compose -f docker-compose.prod.yml up -d --build

# The page for you: reachable over `make tunnel` or a tailnet. No certificate needed.
host-web:
	docker compose -f docker-compose.prod.yml --profile web up -d --build

# The page for everyone else: adds Caddy and a real certificate. Needs GOGO_DOMAIN in
# .env and an A record already pointing here.
host-public:
	docker compose -f docker-compose.prod.yml --profile web --profile public up -d --build

backup:
	./scripts/backup.sh
