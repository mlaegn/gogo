# Gogo

Go / no-go surf windows for the **Lisbon–Ericeira–Peniche** coast.

Not a generated surf report and not another Surfline. The unit is a **time window**: where to go, when, and why — for you.

```text
Saturday 07:00–10:00 · Ribeira d'Ilhas · 78
offshore, incoming mid, 1.3 m @ 11 s NW
```

Open the list (later: a phone page + Telegram). You get ranked windows, not a 400-word essay. A ping when something clears your bar comes after the rank is trustworthy.

## Status

**Phase 2 — store + fetch.** Score is unchanged; forecasts now persist.

- 16 hand-curated spots in `spots/coast.yml`
- Same deterministic score
- `gogo fetch` writes Open-Meteo hours into Postgres (`forecast_snapshots` + `forecast_current`)
- `GET /windows` and `gogo weekend --db` read **stored** rows, not a live API call
- Local Postgres via OrbStack + `make up`

**Not built yet:** hourly loop (cron/worker process), Telegram, session log, Vite UI, deploy.

Thesis project [`surfreporter`](https://github.com/MaximilianLae/surfreporter) is reference only. This is a new product, not a rewrite of that RAG demo.

## How it works (when Phase 1 is done)

```text
you run:  gogo weekend
          → load spots
          → forecast hours (fixture or Open-Meteo)
          → score every spot at Saturday 08:00
          → print ranked windows + reasons

later:    worker writes hours into Postgres
          site / Telegram only read those rows
```

The click / the bot must not call the weather API. That is a later rule, once the worker exists.

## Stack

| Piece | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Score and domain are the hard part |
| API (later) | FastAPI | `/health` is already there; `/windows` comes with the worker |
| Store (later) | Postgres | Snapshots + current row + session log |
| Forecast | Open-Meteo Marine + Forecast | Hourly swell, wind-sea, wind, sea-level; we checked it on this coast |
| UI (later) | Vite + React + TypeScript | Phone list + detail |
| Alerts (later) | Telegram | The thing you actually use |
| Host (later) | Fly.io or a VPS | Not a personal EKS cluster |

One git repo. `api` and `worker` are processes, not extra repositories. A new forecast source is another file in `src/gogo/ingest/`.

## Run locally

Needs [uv](https://docs.astral.sh/uv/) (or any Python 3.12 + pip). Docker is optional until the worker exists.

```bash
make install
make up               # Postgres in OrbStack (once)
make test
make weekend          # committed fixture, no network
make fetch            # Open-Meteo → Postgres
make weekend-db       # score stored rows
make api              # GET /health and GET /windows
```

Equivalent without Make:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/gogo weekend --fixture tests/fixtures/weekend.json
```

Windows look like:

```text
  GO      81  Ribeira d'Ilhas
         1.2 m in range; 8 s is enough; 8 kn offshore
  maybe   69  Coxos
         1.2 m in range; 8 s is short for this spot; 8 kn offshore
```

Postgres (schema only, unused in Phase 1):

```bash
make up      # docker compose, postgres:16
make down
```

## Layout

```text
spots/coast.yml              # the content — edit here first
src/gogo/score.py            # pure, tested
src/gogo/tide.py             # low / mid / high from sea level
src/gogo/ingest/openmeteo.py # the only forecast source for v1
src/gogo/cli.py              # gogo weekend
src/gogo/api.py              # /health only
migrations/001_init.sql      # spots, snapshots, current, sessions
tests/                       # fixtures over ranks, not RAGAS
```

How to change the product: edit the spot file or the score, add a fixture in `tests/test_score.py`, run `make test`. If a rank needs a paragraph of justification, the score is not done.

## Forecast notes

- Marine: `cell_selection=sea`, `models` left at `best_match`, `timezone=Europe/Lisbon`, `forecast_days=7`
- Wind: weather API, knots, land cell
- Tide is a **phase** from `sea_level_height_msl`, not a Hidrográfico table. Compare before trusting that term.
- Nearby spots often share one wave-model cell. Ranking between two Ericeira reefs on the same hour comes from the spot file, not from a different swell number.

Free Open-Meteo is **non-commercial**, CC BY 4.0, with a daily call cap we will not approach. Attribution is required. If this ever has ads or a paid plan, switch to their customer endpoint.

## Attribution

Forecasts from [Open-Meteo](https://open-meteo.com/) (CC BY 4.0), using national wave and weather models (Météo-France, DWD, ECMWF, NCEP, and others).
