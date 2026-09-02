# Gogo

![Gogo — go / no-go surf windows](img/header-banner.png)

Go / no-go surf windows for the **Lisbon–Ericeira–Peniche** coast.

Not a generated surf report and not another Surfline. The unit is a **time window**: where to go, when, and why — for you.

```text
Saturday 07:00–10:00 · Ribeira d'Ilhas · 78
offshore, incoming mid, 1.3 m @ 11 s NW
```

The API already returns a ranked list (today: **one hour**, Saturday 08:00). Grouping hours into ranges like the example above is next. A phone page and Telegram come after that.

## Status

**Phase 2 — store + fetch.** Forecasts persist. The score is unchanged.

| Piece | State |
|---|---|
| 16 spots in `spots/coast.yml` | done |
| Deterministic score + tests | done |
| Open-Meteo marine + wind | done |
| Postgres (`spots`, `spot_grid`, `forecast_snapshots`, `forecast_current`, `sessions`) | done |
| `gogo fetch` | done (one-shot) |
| `gogo weekend --db` / `GET /windows` | done (read stored rows) |
| Hourly worker process | **not yet** |
| Windows as time ranges | **not yet** |
| UI, Telegram, session log, deploy | later |

Thesis [`surfreporter`](https://github.com/MaximilianLae/surfreporter) is reference only.

Where this is going: [`docs/plan.md`](docs/plan.md). Short version — locals' post-session
feedback becomes training signal for the score, not a social feed, and no score change
lands without a backtest number.

## How it works

```text
gogo fetch          → Open-Meteo → snapshots + current (worker writes)
gogo weekend --db   → read current → score Saturday 08:00 → print
GET /windows        → same as --db, JSON
```

The API does **not** call Open-Meteo. If current is empty, `/windows` returns 503 until you `fetch`.

## Stack

| Piece | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Score and domain are the hard part |
| API | FastAPI | `/health`, `/windows` |
| Store | Postgres 16 | Snapshots + current row + later session log |
| Forecast | Open-Meteo Marine + Forecast | Hourly swell, wind-sea, wind, sea-level |
| Local engine | OrbStack | Runs the same `docker compose` file |
| UI (later) | Vite + React + TypeScript | Phone list + detail |
| Alerts (later) | Telegram | The thing you actually use |
| Host (later) | Fly.io or a VPS | Not a personal EKS cluster |

One git repo. `api` and `worker` are processes, not extra repositories.

## Run locally

Needs [uv](https://docs.astral.sh/uv/) and [OrbStack](https://orbstack.dev) (for Postgres).

```bash
make install
make up                 # Postgres; waits until it is healthy
make test
make weekend            # fixture, no network
make fetch              # Open-Meteo → Postgres
make weekend-db         # score stored rows
make api                # http://127.0.0.1:8000/windows
make down               # stop Postgres; volume (data) stays
```

Copy `.env.example` to `.env` if you change the database URL. Defaults match compose (`gogo` / `gogo` / `gogo` on `localhost:5432`).

## Layout

```text
spots/coast.yml              # curated gates — edit here first
src/gogo/score.py            # pure, tested
src/gogo/ingest/openmeteo.py # the only forecast source for v1
src/gogo/store.py            # seed, persist, load current
src/gogo/worker.py           # fetch_once (loop comes next)
src/gogo/cli.py              # gogo weekend | fetch
src/gogo/api.py              # GET /health, GET /windows
migrations/001_init.sql      # applied on first make up
uv.lock                      # pinned Python deps; make install / CI use this
.github/workflows/test.yml   # pytest + compose Postgres
tests/                       # ranks, ingest mocks, store roundtrip
```

How to change the product: edit the spot file or the score, add a fixture in `tests/test_score.py`, run `make test`. If a rank needs a paragraph of justification, the score is not done.

## Forecast notes

- Marine: `cell_selection=sea`, `best_match`, `timezone=Europe/Lisbon`, `timeformat=unixtime`, `forecast_days=7`
- Timestamps are timezone-aware **UTC** everywhere inside; Lisbon is rendered at the edges. `unixtime` avoids the ambiguous local hour on the autumn DST fold
- Wind: weather API, knots, land cell
- Tide is a **phase** from `sea_level_height_msl`, not a Hidrográfico table
- Nearby spots often share one wave-model cell. Ranking between two Ericeira reefs on the same hour comes from the spot file

Free Open-Meteo is **non-commercial**, CC BY 4.0. Attribution is required. If this ever has ads or a paid plan, use their customer endpoint.

## Attribution

Forecasts from [Open-Meteo](https://open-meteo.com/) (CC BY 4.0), using national wave and weather models (Météo-France, DWD, ECMWF, NCEP, and others).
