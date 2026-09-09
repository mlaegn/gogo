# Gogo

Personal surf go / no-go planner. Lisbon–Ericeira–Peniche only.

Thesis `surfreporter` is reference, not a dependency. Do not copy Pinecone, Streamlit, RAG, or IPMA-as-primary.

`docs/plan.md` is the current contract. Read it before starting work: it says which slice is next and why. Community input is training signal for the score, not a feed.

## Frozen decisions

- **One repo.** `api` and `worker` are processes, not repositories. A new forecast source is `src/gogo/ingest/<name>.py` behind `ForecastSource`.
- **Open-Meteo only for v1.** Marine + weather forecast. `cell_selection=sea` on marine. `best_match`. Land cell for wind. Batch unique grid points. `forecast_days=7`, `timezone=Europe/Lisbon`, `timeformat=unixtime`.
- **UTC inside, Lisbon at the edges.** Every stored and passed timestamp is timezone-aware UTC. Local time exists only in CLI output, API responses, and later the UI. "Saturday 08:00" is a *local* concept and must be resolved in `Europe/Lisbon`.
- **Tide** from `sea_level_height_msl` is a phase (low/mid/high), not a navigation table. Compare to Hidrográfico before trusting the tide term. S12 replaces it with height + rate.
- **Score decides.** LLM is not in the path. Reasons must explain a rank in one sentence.
- **A window is a run of adjacent passing hours** (score v2). Score every hour, group consecutive non-`no` hours, rank by the mean and then by duration. A `no` hour or a gap in the data ends a run. `reasons` come from the member hour nearest the mean, so the sentence explains the number; `peak_at` is kept alongside. `ends_at` is exclusive. Changing this rule is a `SCORE_VERSION` bump, not a tweak.
- **`forecast_current` is for serving. Evaluation reads `forecast_snapshots` at an as-of.** Backtesting against current leaks hindsight, because current is overwritten by later runs.
- **Reanalysis is not a forecast.** `gogo backfill` writes `forecast_snapshots` with `is_analysis`, never `forecast_current`. Analysis answers "is the score right about real conditions"; only forecast rows filtered to an as-of can answer "would we have called it right at the time". The archive resolves to the same marine cells as the forecast, so the two join on the grid.
- **Local engine is OrbStack**, not Docker Desktop. Same `docker compose` file.
- **Worker writes, API reads.** `/api/windows` must not call Open-Meteo.
- **No EKS, Redis, Kafka, ClickHouse, Pinecone, Next.js, Auth0.** Identity is an app account (magic link or OAuth); invite-only is the anti-spam design. No Telegram client — the UI is the surface people check.
- **Ratings pool globally, interpretation can be private.** Observations are anonymous global training signal. Group-scoped spec *overlays* (personal → group → base) are how private local knowledge works. Never fragment the label pool.
- **A label has one row.** `unique (user_id, spot_id, started_at)`; `record_observation` returns `None` on a duplicate. Recalled sessions import unanchored and carry `rating`, never `residual` — nothing was predicted to them at the time. Same-day pairs are the sample size of the headline metric, so `kind=checked` rows are the point, not padding.
- **Schema comes from `gogo migrate`**, not from Postgres. There is no initdb mount: `make up` starts the server and migrates it. Add a numbered file in `src/gogo/migrations/`; never edit an applied one.
- **`coast.yml` and the migrations are package data**, under `src/gogo/`. They ship in the wheel and are found relative to `__file__`. Never resolve runtime input by walking up to the repo root — an installed wheel has no repo root.
- **CI** runs `gogo migrate` then `pytest` against compose Postgres on GitHub Actions, plus a second job for the frontend: typecheck, build, and a diff of the generated client types. Do not skip store tests locally if Postgres is up.
- **Tests get their own database.** `tests/conftest.py` creates and migrates `gogo_test`, redirects `DATABASE_URL`, and truncates between tests. Never write a test that writes to `gogo` — a fabricated observation is indistinguishable from a real label once the harness exists.
- **Synthetic labels exist, and are quarantined by a column.** `gogo demo` writes fixture sessions so the Stage 2 harness can be built before real labels exist; every row carries `is_synthetic` (006). They are generated *from our own score*, so a metric that counts them measures our assumptions and comes back flattering. `load_observations` and `count_observations` exclude them by default and `record_observation` defaults to false — never invert those defaults, and never report a number that included them. `gogo demo --purge` removes them all.
- **The worker's snapshot history is unrecoverable.** Each cycle stamps `forecast_snapshots.fetched_at`; the archive can say what the ocean did, but nothing can reconstruct what the forecast *said* beforehand. A day the worker did not run is a permanent hole in the only data that answers Q2. A failed fetch must never end the loop, and `spots_without_hours` runs every cycle because a ranking quietly missing a spot looks completely normal.
- **Deps** come from `uv.lock`. `make install` is `uv sync`. After changing `pyproject.toml`, run `uv lock` and commit the lockfile. `numpy`/`scipy` belong to the `eval` group only — never to the API or worker runtime.

- **Host is a VPS (or Fly), not EKS.** `docker-compose.prod.yml`: Postgres + worker, optional API. Secrets live in a host `.env` that is never copied into the image.

## Layout

```text
docs/plan.md             # the contract — which slice is next
src/gogo/data/coast.yml  # the content — edit here first
src/gogo/score.py        # pure, tested
src/gogo/schemas.py      # the wire format; the client's types are generated from it
web/                     # Vite + React + TS, built into src/gogo/static/app
src/gogo/ingest/         # Open-Meteo: openmeteo.py forecast, archive.py reanalysis
src/gogo/store.py        # Postgres
src/gogo/worker.py       # fetch_once, run_forever, backfill
src/gogo/demo.py         # FIXTURE labels for harness work — never real data
src/gogo/clock.py        # UTC inside, Lisbon at the edges
src/gogo/versioning.py   # spec_version; SCORE_VERSION lives in score.py
src/gogo/migrate.py      # gogo migrate
src/gogo/migrations/     # numbered SQL, applied by gogo migrate
tests/fixtures/          # golden weekends
Dockerfile               # one image: worker default, uvicorn for the page
docker-compose.prod.yml  # VPS: unpublished Postgres + worker; --profile web for the page
scripts/backup.sh        # nightly pg_dump into backups/
```

## How to work

1. Change the spot file or the score.
2. Add or adjust a fixture in `tests/test_score.py`.
3. `make test`. If a rank needs a paragraph of justification, the score is not done.

Do not add a CSS framework, a vector DB, or an LLM while the score is still being argued. The page is Vite + React + TypeScript in `web/`, built into `src/gogo/static/app` and served by FastAPI same-origin; the CSS is hand-written and stays that way.

- **The log screen must not be able to show our score.** Not "must not show" — *must not be able to*. Its only fetch is `/api/spots`, which carries no score, and the reveal lives only in the response to `POST /api/observations`. Never add a score field to `SpotOut`, and never let that screen read `/api/windows`.
- **Types are generated, not hand-written.** `schemas.py` is the wire format; `make ui-types` regenerates `web/src/api/schema.d.ts`; CI fails on a diff. After changing an API model, run it and commit the result.
- **The login stays server-rendered.** `/enter` is Jinja and the cookie is `httponly`, so the secret never reaches JavaScript. Same-origin serving is what keeps auth cookie-only — do not introduce tokens, CORS, or `localStorage`.
- **The bundle is built, not committed.** `src/gogo/static/app` is gitignored and listed under `artifacts` in `pyproject.toml`. `make install` stays Python-only; the score, worker and tests must never need Node.

**The log card must never show our score before the answer is saved.** Anchoring is the one bug in this system that produces data which looks fine and is worthless. `test_web.py` asserts it against rendered HTML; do not relax that test.

**No score improvements before the evaluation harness exists** (`docs/plan.md`, Stage 2 gate). A weight change without a backtest number is an opinion.
